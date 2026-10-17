"""The in-room protocol between a Parivox agent and the Parivox meeting client.

The meeting client recognises an agent by its participant attributes and
controls it with RPCs. This module holds the names and builds the attribute
set; ``room.ParivoxRoom`` publishes them and registers the handlers.
``docs/protocol.md`` is the specification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .metadata import ParivoxJob

PROTOCOL_VERSION = "1"

# Participant attributes the agent publishes.
ATTR_TYPE = "nexus.type"
ATTR_PROTOCOL_VERSION = "nexus.protocol_version"
ATTR_AGENT_ID = "nexus.agent_id"
ATTR_INSTANCE_ID = "nexus.instance_id"
ATTR_SPOKEN_NAME = "nexus.spoken_name"
ATTR_AVATAR_NAME = "nexus.avatar_name"
ATTR_AVATAR_PROVIDER = "nexus.avatar_provider"
ATTR_VISUAL_REPRESENTATION = "nexus.visual_representation_type"
ATTR_AGENT_IMAGE_URL = "nexus.agent_image_url"
ATTR_CONTROL_READY = "nexus.control_ready"
ATTR_HUSHED = "nexus.hushed"
ATTR_QUIET_MODE = "nexus.quiet_mode"
ATTR_SKILLS = "nexus.skills"
ATTR_START_VIDEO_HIDDEN = "nexus.start_video_hidden"
ATTR_SUGGESTED_RESPONSES = "nexus.suggested_responses"

AGENT_TYPE = "ai_avatar"
AUDIO_ONLY = "audio_only"

# RPCs the meeting client calls on the agent.
RPC_HUSH = "nexus.avatar.hush"
RPC_UNHUSH = "nexus.avatar.unhush"
RPC_TERMINATE = "nexus.avatar.terminate"
RPC_SUGGESTED_RESPONSE_SELECTED = "nexus.suggested_response.selected"
RPC_HANDOFF_ACK = "nexus.handoff.ack"

# RPCs the agent calls on the meeting client.
RPC_HANDOFF_INITIATED = "nexus.handoff.initiated"
RPC_HANDOFF_FAILED = "nexus.handoff.failed"

# Other Parivox participants an agent should not listen to or address.
PARIVOX_SERVICE_TYPES = frozenset({AGENT_TYPE, "transcript_service", "translation_service"})

MAX_SUGGESTED_RESPONSES = 8

PRESENCE_DEADLINE_SECONDS = 15
"""The meeting client cancels a dispatch whose agent has not published
``nexus.type`` this long after it was added."""


def _bool(value: bool) -> str:
    return "true" if value else "false"


@dataclass
class PresenceState:
    """What the agent tells the room about itself. Mutate, then republish."""

    hushed: bool = False
    quiet_mode: bool = False
    control_ready: bool = False
    suggested_responses: list[str] = field(default_factory=list)


def build_attributes(job: ParivoxJob, state: PresenceState) -> dict[str, str]:
    """The full attribute set for this agent in its current state."""
    persona = job.persona
    attributes = {
        ATTR_TYPE: AGENT_TYPE,
        ATTR_PROTOCOL_VERSION: PROTOCOL_VERSION,
        ATTR_AGENT_ID: job.agent_id or "local-dev",
        ATTR_INSTANCE_ID: job.instance_id,
        ATTR_SPOKEN_NAME: job.name,
        ATTR_AVATAR_NAME: job.name,
        # A self-hosted agent publishes audio only; the tile shows its image
        # or a visualizer, as chosen in the agent editor.
        ATTR_AVATAR_PROVIDER: AUDIO_ONLY,
        ATTR_VISUAL_REPRESENTATION: persona.visual_representation_type,
        ATTR_CONTROL_READY: _bool(state.control_ready),
        ATTR_HUSHED: _bool(state.hushed),
        ATTR_QUIET_MODE: _bool(state.quiet_mode),
        # Always published, so clearing the list removes the buttons.
        ATTR_SUGGESTED_RESPONSES: json.dumps(state.suggested_responses[:MAX_SUGGESTED_RESPONSES]),
    }
    if persona.skills:
        attributes[ATTR_SKILLS] = json.dumps(list(persona.skills))
    if persona.agent_image_url:
        attributes[ATTR_AGENT_IMAGE_URL] = persona.agent_image_url
    if job.start_video_hidden:
        attributes[ATTR_START_VIDEO_HIDDEN] = "true"
    return attributes


def parse_payload(payload: str | None) -> dict:
    """An RPC payload as a dict; anything that isn't a JSON object reads as empty."""
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_parivox_service(attributes: dict[str, str] | None) -> bool:
    """True for agents, transcribers and translators Parivox runs in the room."""
    return (attributes or {}).get(ATTR_TYPE, "") in PARIVOX_SERVICE_TYPES
