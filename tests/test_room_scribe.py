"""The example agent's own tools, with Parivox faked out."""

import importlib.util
from pathlib import Path

import pytest
from conftest import FakeJobContext, FakeParticipant, FakeSession

from parivox_agents import ParivoxApiError, ParivoxRoom, RoomMemory, RoomMemoryList

spec = importlib.util.spec_from_file_location(
    "room_scribe", Path(__file__).parents[1] / "examples" / "room_scribe" / "agent.py"
)
room_scribe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(room_scribe)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_room_memories(self, requester):
        self.calls.append(("list", requester))
        return RoomMemoryList("Launch", [RoomMemory("m1", "Launch is May 3.")], True)

    async def add_room_memory(self, requester, text):
        self.calls.append(("add", requester, text))
        if requester == "guest":
            raise ParivoxApiError(403, "Only the room owner or a co-owner can change room memory.")
        return RoomMemory("m2", text)

    async def add_shared_file(self, room_id, filename, content):
        self.calls.append(("file", room_id, filename, content))
        return type("Saved", (), {"filename": filename})()

    async def aclose(self):
        pass


@pytest.fixture
async def scribe(metadata):
    ctx = FakeJobContext(metadata)
    ctx.room.join(FakeParticipant("host", name="Host"))
    ctx.room.join(FakeParticipant("guest", name="Guest"))
    parivox = ParivoxRoom(ctx)
    parivox.client = FakeClient()
    agent = room_scribe.RoomScribe(parivox)
    await parivox.start(FakeSession(), agent)
    return agent


def tool_names(agent) -> set[str]:
    return {tool.info.name for tool in agent.tools}


async def test_has_its_own_tools_and_the_capabilities(scribe):
    assert tool_names(scribe) == {
        "enter_quiet_mode",
        "trigger_handoff",
        "recall_room_memory",
        "remember_for_room",
        "save_meeting_notes",
    }
    assert "Be brief." in scribe.instructions
    assert "Escalate billing questions." in scribe.instructions


async def test_room_memory_acts_for_the_speaker(scribe):
    assert "Launch is May 3." in await scribe.recall_room_memory(None)
    assert await scribe.remember_for_room(None, "Budget is $45k.") == "Saved to room memory: Budget is $45k."
    scribe.parivox._listen_to("guest")
    assert "co-owner" in await scribe.remember_for_room(None, "Nope")
    assert [call[:2] for call in scribe.parivox.client.calls] == [("list", "host"), ("add", "host"), ("add", "guest")]


async def test_notes_go_to_shared_files(scribe, monkeypatch):
    assert "Nothing has been said" in await scribe.save_notes()

    async def fake_notes(transcript):
        return "## Summary\n" + " ".join(text for _, text in transcript)

    monkeypatch.setattr(room_scribe, "write_notes", fake_notes)
    scribe.transcript.append(("Host", "Launch moves to May 3."))
    result = await scribe.save_notes()
    assert result.startswith("Saved Meeting notes ")
    _, room_id, filename, content = scribe.parivox.client.calls[-1]
    assert room_id == "room123" and filename.endswith(".md")
    assert "May 3" in content
    assert scribe.notes_saved_at == 1


async def test_outside_parivox_the_tools_say_so(metadata):
    parivox = ParivoxRoom(FakeJobContext({}))
    agent = room_scribe.RoomScribe(parivox)
    assert "only available inside Parivox" in await agent.recall_room_memory(None)
