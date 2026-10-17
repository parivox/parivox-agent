import asyncio

import pytest
from conftest import FakeJobContext, FakeParticipant, FakeSession
from livekit import rtc
from livekit.agents import StopResponse
from livekit.agents.llm import ChatContext, ChatMessage

from parivox_agents import ParivoxRoom
from parivox_agents.capabilities import handoff as handoff_module


async def started(metadata: dict, *, people=("host",), **kwargs) -> tuple[ParivoxRoom, FakeJobContext, FakeSession]:
    ctx = FakeJobContext(metadata)
    for identity in people:
        ctx.room.join(FakeParticipant(identity, name=identity.title()))
    parivox = ParivoxRoom(ctx, **kwargs)
    session = FakeSession()
    await parivox.start(session, agent=object())
    return parivox, ctx, session


def user_turn(text: str) -> tuple[ChatContext, ChatMessage]:
    return ChatContext(), ChatMessage(role="user", content=[text])


async def test_presence_is_published_before_the_session_starts(metadata):
    _parivox, ctx, session = await started(metadata)
    assert ctx.connected
    assert session.attributes_at_start["nexus.type"] == "ai_avatar"
    assert session.attributes_at_start["nexus.control_ready"] == "false"
    assert ctx.room.local_participant.attributes["nexus.control_ready"] == "true"
    assert session.started_with["room_options"].close_on_disconnect is False
    assert session.room_io.linked_participant.identity == "host"
    assert session.replies == [{"instructions": "Say hello."}]


async def test_starts_hushed_without_greeting(metadata):
    metadata["startQuiet"] = True
    parivox, ctx, session = await started(metadata)
    assert parivox.hushed and parivox.state.quiet_mode
    assert not session.output.audio_enabled
    assert session.replies == []
    assert ctx.room.local_participant.attributes["nexus.quiet_mode"] == "true"


async def test_hush_and_unhush_rpcs(metadata):
    parivox, ctx, session = await started(metadata)
    local = ctx.room.local_participant
    assert await local.call("nexus.avatar.hush", {"mode": "soft"}) == "hushed"
    assert local.attributes["nexus.hushed"] == "true"
    assert not session.output.audio_enabled and not session.output.transcription_enabled
    assert session.interrupts == 1
    with pytest.raises(StopResponse):
        await parivox.before_reply(*user_turn("are you there?"))

    assert await local.call("nexus.avatar.unhush") == "unhushed"
    assert local.attributes["nexus.hushed"] == "false"
    assert session.output.audio_enabled
    await parivox.before_reply(*user_turn("are you there?"))


async def test_terminate_ends_the_job(metadata):
    _, ctx, _ = await started(metadata)
    assert await ctx.room.local_participant.call("nexus.avatar.terminate") == "terminating"
    assert ctx.shutdown_reasons == ["removed by host"]


async def test_suggestion_is_answered_as_the_tapper(metadata):
    parivox, ctx, session = await started(metadata, people=("host", "guest"))
    await parivox.set_suggested_responses(["Summarize so far", " ", "List action items"])
    assert ctx.room.local_participant.attributes["nexus.suggested_responses"] == (
        '["Summarize so far", "List action items"]'
    )
    result = await ctx.room.local_participant.call(
        "nexus.suggested_response.selected", {"text": "Summarize so far"}, caller="guest"
    )
    assert result == "accepted"
    assert parivox.speaker_identity == "guest"
    assert session.replies[-1] == {"user_input": "Summarize so far"}


async def test_listens_to_the_active_speaker_but_not_services(metadata):
    parivox, ctx, _session = await started(metadata, people=("host", "guest"))
    translator = ctx.room.join(
        FakeParticipant(
            "translator",
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
            attributes={"nexus.type": "translation_service"},
        )
    )
    ctx.room.emit("active_speakers_changed", [translator])
    assert parivox.speaker_identity == "host"
    ctx.room.emit("active_speakers_changed", [ctx.room.remote_participants["guest"]])
    assert parivox.speaker_identity == "guest"
    assert [p.identity for p in parivox.humans()] == ["host", "guest"]


async def test_ends_after_the_last_person_leaves(metadata, monkeypatch):
    monkeypatch.setattr("parivox_agents.room.EMPTY_ROOM_GRACE_SECONDS", 0.01)
    _, ctx, _ = await started(metadata)
    ctx.room.leave("host")
    await asyncio.sleep(0.05)
    assert ctx.shutdown_reasons == ["room empty"]


async def test_someone_returning_cancels_the_shutdown(metadata, monkeypatch):
    monkeypatch.setattr("parivox_agents.room.EMPTY_ROOM_GRACE_SECONDS", 0.05)
    _, ctx, _ = await started(metadata)
    ctx.room.leave("host")
    ctx.room.join(FakeParticipant("host"))
    await asyncio.sleep(0.1)
    assert ctx.shutdown_reasons == []


async def test_quiet_mode_round_trip(metadata):
    parivox, ctx, session = await started(metadata)
    parivox.quiet_mode.on_user_transcript("What's on the agenda?", True)
    assert "not requested" in await parivox.quiet_mode.enter_from_tool()
    assert not parivox.hushed

    parivox.quiet_mode.on_user_transcript("Scribe, give us a minute", True)
    assert "quiet mode" in await parivox.quiet_mode.enter_from_tool()
    assert parivox.hushed and ctx.room.local_participant.attributes["nexus.quiet_mode"] == "true"

    session.emit("user_input_transcribed", type("E", (), {"transcript": "Okay Scribe, what's next?", "is_final": True}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not parivox.hushed
    turn_ctx, message = user_turn("Okay Scribe, what's next?")
    await parivox.before_reply(turn_ctx, message)
    assert "wake phrase" in turn_ctx.items[-1].text_content

    # The wake turn still holds "give us a minute" in context; it must not
    # send the agent straight back to sleep.
    parivox.quiet_mode.on_user_transcript("Scribe, give us a minute", True)
    assert "just exited" in await parivox.quiet_mode.enter_from_tool()
    assert not parivox.hushed


async def test_host_hush_is_not_lifted_by_the_wake_phrase(metadata):
    parivox, ctx, session = await started(metadata)
    await ctx.room.local_participant.call("nexus.avatar.hush")
    session.emit("user_input_transcribed", type("E", (), {"transcript": "Okay Scribe", "is_final": True}))
    await asyncio.sleep(0)
    assert parivox.hushed


async def test_handoff_pages_through_the_host(metadata):
    parivox, ctx, _ = await started(metadata, people=("host", "guest"))
    assert [t.info.name for t in parivox.tools()] == ["enter_quiet_mode", "trigger_handoff"]
    assert "Escalate billing questions." in parivox.instructions()
    local = ctx.room.local_participant

    async def host_acks(destination, method, payload):
        if destination == "host" and method == "nexus.handoff.initiated":
            await local.call("nexus.handoff.ack", {"handoffId": payload["handoffId"]}, caller="host")

    local.on_rpc = host_acks
    result = await parivox.handoff.trigger("Billing question", "They want a refund.")
    assert "being paged" in result
    destination, method, payload = local.rpc_calls[0]
    assert (destination, method) == ("host", "nexus.handoff.initiated")
    assert payload["agentId"] == "agent_scribe"
    assert payload["handoffContact"] == {"email": "owner@example.com"}
    assert payload["timeToWaitForHumanSeconds"] == 45
    assert payload["schemaVersion"] == "1"


async def test_handoff_without_an_ack_tells_the_host(metadata, monkeypatch):
    monkeypatch.setattr(handoff_module, "ACK_TIMEOUT_SECONDS", 0.01)
    parivox, ctx, _ = await started(metadata)
    result = await parivox.handoff.trigger("Billing", "Refund")
    assert "could not be sent" in result
    methods = [method for _, method, _ in ctx.room.local_participant.rpc_calls]
    assert methods == ["nexus.handoff.initiated"] * 3 + ["nexus.handoff.failed"]


async def test_no_handoff_tool_without_a_contact(metadata):
    del metadata["resolvedPersona"]["skill_config"]["escalate-to-human"]["email"]
    parivox, _, _ = await started(metadata)
    assert [t.info.name for t in parivox.tools()] == ["enter_quiet_mode"]
    assert parivox.instructions().count("##") == 0
