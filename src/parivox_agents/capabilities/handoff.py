"""Handoff: the agent pages a human to take over the conversation.

When a participant asks for a person, or the conversation hits the escalation
policy written in the agent editor, the model calls ``trigger_handoff``. The
agent sends ``nexus.handoff.initiated`` to the people in the room; the host's
meeting client acknowledges it, pages the contact configured on the agent
(email or SMS) and shows the paging state. Parivox does the paging; the agent
only asks.

The capability is offered to the model only when the agent has a handoff
contact: turn on "Escalate to Human" in the agent editor and set its email or
phone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING

from livekit.agents import RunContext, function_tool

from ..protocol import RPC_HANDOFF_FAILED, RPC_HANDOFF_INITIATED, parse_payload

if TYPE_CHECKING:
    from ..room import ParivoxRoom

logger = logging.getLogger("parivox_agents")

SKILL_ID = "escalate-to-human"
SCHEMA_VERSION = "1"
PAGED_HUMAN_IDENTITY = "paged-human"
ACK_TIMEOUT_SECONDS = 2.0
MAX_ATTEMPTS = 3
REASON_MAX_CHARS = 280
SUMMARY_MAX_CHARS = 4000
DEFAULT_WAIT_FOR_HUMAN_SECONDS = 30


def escalation_instructions(policy: str) -> str:
    """Prompt text telling the model when to hand off. Empty without a policy."""
    policy = policy.strip()
    if not policy:
        return ""
    return (
        "## Escalation policy\n\n"
        f"{policy}\n\n"
        "An explicit request to talk, speak, or connect with a human is an immediate escalation. "
        "Call the trigger_handoff tool in that same turn with a short reason and summary. "
        "Do not say that you cannot connect the participant, offer to relay a note, or ask a "
        "follow-up question before calling the tool. The tool result will say whether the "
        "configured person is being paged."
    )


def _bounded(text: str, limit: int, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:limit] if cleaned else fallback


class Handoff:
    """The handoff capability. ``ParivoxRoom`` creates and wires it."""

    def __init__(self, room: ParivoxRoom) -> None:
        self._room = room
        self._pending: dict[str, asyncio.Future[None]] = {}

    @property
    def available(self) -> bool:
        return bool(self._room.job.handoff_contact.email or self._room.job.handoff_contact.phone)

    @property
    def wait_for_human_seconds(self) -> int:
        raw = self._room.job.persona.config_for(SKILL_ID).get("timeToWaitForHuman")
        try:
            seconds = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_WAIT_FOR_HUMAN_SECONDS
        return seconds if seconds > 0 else DEFAULT_WAIT_FOR_HUMAN_SECONDS

    def instructions(self) -> str:
        return escalation_instructions(str(self._room.job.persona.config_for(SKILL_ID).get("escalationPolicy") or ""))

    def tools(self) -> list:
        if not self.available:
            return []
        handoff = self

        @function_tool
        async def trigger_handoff(context: RunContext, reason: str, summary: str) -> str:
            """Immediately page the configured human for an explicit human request or escalation-sensitive topic.

            Args:
                reason: One short sentence on why a human is needed.
                summary: What the participant needs and what has been discussed, so the human can pick up.
            """
            return await handoff.trigger(reason, summary)

        return [trigger_handoff]

    async def trigger(self, reason: str, summary: str) -> str:
        """Ask the room's client to page the human. Returns text for the model."""
        room = self._room.room
        job = self._room.job
        handoff_id = f"hf_{uuid.uuid4().hex}"
        payload = json.dumps(
            {
                "handoffId": handoff_id,
                "agentId": job.agent_id,
                "roomName": room.name,
                "reason": _bounded(reason, REASON_MAX_CHARS, "A participant asked for a person."),
                "summary": _bounded(summary, SUMMARY_MAX_CHARS, "The conversation needs a person."),
                "spokenName": "a human specialist",
                "humanIdentity": PAGED_HUMAN_IDENTITY,
                "schemaVersion": SCHEMA_VERSION,
                "timeToWaitForHumanSeconds": self.wait_for_human_seconds,
                "handoffContact": job.handoff_contact.to_payload(),
            }
        )
        ack: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending[handoff_id] = ack
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                destinations = self._destinations()
                if not destinations:
                    logger.warning("Handoff attempt %d: nobody in the room to page through.", attempt)
                    break
                delivered = False
                for identity in destinations:
                    try:
                        await room.local_participant.perform_rpc(
                            destination_identity=identity, method=RPC_HANDOFF_INITIATED, payload=payload
                        )
                        delivered = True
                    except Exception as exc:
                        logger.warning("Handoff RPC to %s failed: %s", identity, exc)
                if not delivered:
                    continue
                try:
                    await asyncio.wait_for(asyncio.shield(ack), timeout=ACK_TIMEOUT_SECONDS)
                    logger.info("Handoff %s acknowledged.", handoff_id)
                    return (
                        "The human is being paged now. Tell the participant briefly, then keep helping while they wait."
                    )
                except TimeoutError:
                    logger.warning("Handoff %s: no acknowledgement on attempt %d.", handoff_id, attempt)
        finally:
            self._pending.pop(handoff_id, None)

        if job.host_identity in room.remote_participants:
            try:
                await room.local_participant.perform_rpc(
                    destination_identity=job.host_identity,
                    method=RPC_HANDOFF_FAILED,
                    payload=json.dumps(
                        {
                            "handoffId": handoff_id,
                            "roomName": room.name,
                            "schemaVersion": SCHEMA_VERSION,
                            "reason": "ack_timeout",
                        }
                    ),
                )
            except Exception as exc:
                logger.warning("Could not tell the host the handoff failed: %s", exc)
        return (
            "The page could not be sent. Tell the participant you could not reach a person right now, "
            "and offer to note what they need so the host can follow up."
        )

    def on_ack(self, payload: str | None) -> str:
        """Handle ``nexus.handoff.ack`` from the client."""
        handoff_id = str(parse_payload(payload).get("handoffId") or "")
        future = self._pending.get(handoff_id)
        if future is not None and not future.done():
            future.set_result(None)
        return "ack_received"

    def _destinations(self) -> list[str]:
        """The host first, then every other person: whoever's client is the host's acks."""
        humans = [p.identity for p in self._room.humans()]
        host = self._room.job.host_identity
        ordered = [host] if host in humans else []
        ordered += sorted(identity for identity in humans if identity != host and identity != PAGED_HUMAN_IDENTITY)
        return ordered
