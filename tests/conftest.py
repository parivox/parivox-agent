"""Fakes for the bits of LiveKit that ParivoxRoom touches. No server needed."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from livekit import rtc


class Emitter:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event: str, callback=None):
        def register(fn):
            self.handlers.setdefault(event, []).append(fn)
            return fn

        return register(callback) if callback else register

    def emit(self, event: str, *args: Any) -> None:
        for fn in self.handlers.get(event, []):
            fn(*args)


@dataclass
class FakeParticipant:
    identity: str
    name: str = ""
    kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    attributes: dict[str, str] = field(default_factory=dict)


class FakeLocalParticipant:
    identity = "agent-AJ_test"

    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}
        self.attribute_history: list[dict[str, str]] = []
        self.rpc_handlers: dict[str, Any] = {}
        self.rpc_calls: list[tuple[str, str, dict]] = []
        self.on_rpc = None

    async def set_attributes(self, attributes: dict[str, str]) -> None:
        self.attributes = dict(attributes)
        self.attribute_history.append(dict(attributes))

    def register_rpc_method(self, method: str):
        def register(fn):
            self.rpc_handlers[method] = fn
            return fn

        return register

    async def perform_rpc(self, *, destination_identity: str, method: str, payload: str) -> str:
        self.rpc_calls.append((destination_identity, method, json.loads(payload)))
        if self.on_rpc is not None:
            await self.on_rpc(destination_identity, method, json.loads(payload))
        return "ok"

    async def call(self, method: str, payload: dict | None = None, caller: str = "host") -> str:
        data = rtc.RpcInvocationData(
            request_id="r1", caller_identity=caller, payload=json.dumps(payload or {}), response_timeout=5.0
        )
        return await self.rpc_handlers[method](data)


class FakeRoom(Emitter):
    def __init__(self, name: str = "pr-room123") -> None:
        super().__init__()
        self.name = name
        self.local_participant = FakeLocalParticipant()
        self.remote_participants: dict[str, FakeParticipant] = {}

    def join(self, participant: FakeParticipant) -> FakeParticipant:
        self.remote_participants[participant.identity] = participant
        self.emit("participant_connected", participant)
        return participant

    def leave(self, identity: str) -> None:
        participant = self.remote_participants.pop(identity)
        self.emit("participant_disconnected", participant)


@dataclass
class FakeJob:
    metadata: str
    room: Any


class FakeJobContext:
    def __init__(self, metadata: dict | str = "", room: FakeRoom | None = None) -> None:
        self.room = room or FakeRoom()
        self.job = FakeJob(metadata=metadata if isinstance(metadata, str) else json.dumps(metadata), room=self.room)
        self.connected = False
        self.shutdown_reasons: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    def shutdown(self, reason: str = "") -> None:
        self.shutdown_reasons.append(reason)


class FakeOutput:
    def __init__(self) -> None:
        self.audio_enabled = True
        self.transcription_enabled = True

    def set_audio_enabled(self, enabled: bool) -> None:
        self.audio_enabled = enabled

    def set_transcription_enabled(self, enabled: bool) -> None:
        self.transcription_enabled = enabled


class FakeRoomIO:
    def __init__(self, room: FakeRoom) -> None:
        self._room = room
        self.linked_participant: FakeParticipant | None = None

    def set_participant(self, identity: str) -> None:
        self.linked_participant = self._room.remote_participants[identity]


class FakeSession(Emitter):
    def __init__(self) -> None:
        super().__init__()
        self.output = FakeOutput()
        self.interrupts = 0
        self.replies: list[dict] = []
        self.started_with: dict | None = None
        self.room_io: FakeRoomIO | None = None
        self.attributes_at_start: dict[str, str] | None = None

    async def start(self, agent, *, room, room_options) -> None:
        self.attributes_at_start = dict(room.local_participant.attributes)
        self.started_with = {"agent": agent, "room": room, "room_options": room_options}
        self.room_io = FakeRoomIO(room)

    def interrupt(self) -> None:
        self.interrupts += 1

    def generate_reply(self, **kwargs: Any) -> None:
        self.replies.append(kwargs)


PARIVOX_METADATA = {
    "agentId": "agent_scribe",
    "agentInstanceId": "inst_1",
    "roomName": "pr-room123",
    "hostIdentity": "host",
    "dispatchDisplayName": "Scribe",
    "agentMeetingApiUrl": "https://parivox.example/",
    "agentMeetingToken": "meeting-token",
    "hostTimezone": "America/Los_Angeles",
    "resolvedPersona": {
        "avatar_participant_name": "Scribe",
        "system_prompt": "Be brief.",
        "intro_prompt": "Say hello.",
        "skills": ["escalate-to-human"],
        "skill_config": {
            "escalate-to-human": {
                "escalationPolicy": "Escalate billing questions.",
                "email": "owner@example.com",
                "timeToWaitForHuman": 45,
            }
        },
        "keyterms": ["Parivox"],
        "visual_representation_type": "livekit_audio_visualizer",
    },
}


@pytest.fixture
def metadata() -> dict:
    return json.loads(json.dumps(PARIVOX_METADATA))
