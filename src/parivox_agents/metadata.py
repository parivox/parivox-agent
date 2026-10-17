"""Parse the job metadata Parivox attaches to every agent dispatch.

When someone adds your agent to a Parivox room, Parivox dispatches a LiveKit
job to your worker and puts everything the agent needs into
``ctx.job.metadata``: who the agent is, the prompt edited in the Parivox agent
editor, the room's host, how the agent should start, and short-lived tokens
for calling back into Parivox.

``ParivoxJob.from_metadata`` turns that JSON into typed values. It never
raises on missing or malformed fields, so the same agent also runs in a room
Parivox did not dispatch it to (a LiveKit playground, ``console`` mode, a test).
Check ``job.is_parivox`` when behaviour should differ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

VISUAL_REPRESENTATION_TYPES = ("image", "video_loop", "livekit_audio_visualizer")


def _text(value: Any, limit: int | None = None) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text[:limit] if limit else text


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


@dataclass(frozen=True)
class HandoffContact:
    """Who the handoff capability pages. Configured in the agent editor."""

    email: str = ""
    phone: str = ""

    def to_payload(self) -> dict[str, str]:
        return {key: value for key, value in (("email", self.email), ("phone", self.phone)) if value}


@dataclass(frozen=True)
class Persona:
    """The agent as configured in the Parivox agent editor.

    Edit these in Parivox, not in code: a prompt change takes effect on the
    next dispatch without redeploying the worker.
    """

    name: str = ""
    """The agent's display name, also the name people say to wake it from quiet mode."""
    system_prompt: str = ""
    intro_prompt: str = ""
    """Instructions for the greeting, or empty for no greeting."""
    end_of_meeting_prompt: str = ""
    skills: tuple[str, ...] = ()
    skill_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    keyterms: tuple[str, ...] = ()
    visual_representation_type: str = "image"
    agent_image_url: str = ""

    def has_skill(self, skill_id: str) -> bool:
        return skill_id in self.skills

    def config_for(self, skill_id: str) -> dict[str, Any]:
        """The editor's settings for one skill, or an empty dict."""
        return dict(self.skill_config.get(skill_id) or {})

    @classmethod
    def from_payload(cls, raw: Any) -> Persona:
        if not isinstance(raw, dict):
            return cls()
        skill_config_raw = raw.get("skill_config")
        skill_config = (
            {str(key): dict(value) for key, value in skill_config_raw.items() if isinstance(value, dict)}
            if isinstance(skill_config_raw, dict)
            else {}
        )
        visual = _text(raw.get("visual_representation_type"))
        return cls(
            name=_text(raw.get("avatar_participant_name"), 120),
            system_prompt=_text(raw.get("system_prompt")),
            intro_prompt=_text(raw.get("intro_prompt")),
            end_of_meeting_prompt=_text(raw.get("end_of_meeting_prompt")),
            skills=_strings(raw.get("skills")),
            skill_config=skill_config,
            keyterms=_strings(raw.get("keyterms")),
            visual_representation_type=visual if visual in VISUAL_REPRESENTATION_TYPES else "image",
            agent_image_url=_text(raw.get("agent_image_url")),
        )


@dataclass(frozen=True)
class ParivoxJob:
    """Everything Parivox told this job, parsed. Build it with ``from_metadata``."""

    agent_id: str = ""
    instance_id: str = ""
    room_name: str = ""
    host_identity: str = ""
    display_name: str = ""
    persona: Persona = field(default_factory=Persona)
    start_hushed: bool = False
    start_quiet: bool = False
    start_video_hidden: bool = False
    host_timezone: str = ""
    handoff_contact: HandoffContact = field(default_factory=HandoffContact)
    api_url: str = ""
    """Parivox's public origin, for the callback API."""
    meeting_token: str = ""
    """Bearer token for the callback API, scoped to this agent and room."""
    runtime_secret_token: str = ""
    """Present only when the agent's owner is the one using it; see ``secrets``."""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_parivox(self) -> bool:
        """True when Parivox dispatched this job, false in a playground or test."""
        return bool(self.agent_id)

    @property
    def name(self) -> str:
        """The name to introduce yourself with and to listen for."""
        return self.persona.name or self.display_name or "Assistant"

    @property
    def has_callback_api(self) -> bool:
        return bool(self.api_url and self.meeting_token)

    @property
    def room_id(self) -> str:
        """The Parivox room this LiveKit room belongs to, for room-scoped APIs.

        A scheduled room's LiveKit name is ``pr-<roomId>`` and a permanent
        room's meeting is ``pm-<meetingId>``; Parivox addresses both by the id.
        """
        name = self.room_name
        for prefix in ("pm-", "pr-"):
            if name.startswith(prefix) and len(name) > len(prefix):
                return name[len(prefix) :]
        return name

    @classmethod
    def from_metadata(cls, metadata: str | dict[str, Any] | None, *, room_name: str = "") -> ParivoxJob:
        """Parse ``ctx.job.metadata``. ``room_name`` fills in when metadata lacks one."""
        data: Any = metadata
        if isinstance(metadata, str):
            try:
                data = json.loads(metadata) if metadata.strip() else {}
            except json.JSONDecodeError:
                data = {}
        if not isinstance(data, dict):
            data = {}

        persona = Persona.from_payload(data.get("resolvedPersona"))
        contact_raw = data.get("handoffContact")
        if not isinstance(contact_raw, dict):
            contact_raw = persona.config_for("escalate-to-human")
        contact = HandoffContact(
            email=_text(contact_raw.get("email"), 320),
            phone=_text(contact_raw.get("phone"), 32),
        )
        return cls(
            agent_id=_text(data.get("agentId")),
            instance_id=_text(data.get("agentInstanceId")),
            room_name=_text(data.get("roomName")) or room_name,
            host_identity=_text(data.get("hostIdentity")),
            display_name=_text(data.get("dispatchDisplayName"), 120),
            persona=persona,
            start_hushed=_flag(data.get("startHushed")),
            start_quiet=_flag(data.get("startQuiet")),
            start_video_hidden=_flag(data.get("startVideoHidden")),
            host_timezone=_text(data.get("hostTimezone")),
            handoff_contact=contact,
            api_url=_text(data.get("agentMeetingApiUrl")).rstrip("/"),
            meeting_token=_text(data.get("agentMeetingToken")),
            runtime_secret_token=_text(data.get("agentRuntimeSecretToken")),
            raw=data,
        )
