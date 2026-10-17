"""Room Scribe: a meeting notetaker that remembers the room.

It answers questions by voice, keeps the room's long-lived facts in Parivox
room memory ("remember that the launch is May 3"), offers one-tap
"Summarize so far" buttons, and when the meeting ends it writes notes to the
room's Shared Files. It also goes quiet when asked and can page a human.

Run it on your machine:  uv run examples/room_scribe/agent.py dev
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    inference,
)
from livekit.agents.llm import ChatContext
from livekit.plugins import silero

from parivox_agents import ParivoxAgent, ParivoxApiError, ParivoxRoom

load_dotenv()
logger = logging.getLogger("room-scribe")

AGENT_NAME = os.getenv("AGENT_NAME", "room-scribe-dev")
"""The worker name. It must match "Self-hosted worker name" on the Parivox agent."""

LLM_MODEL = os.getenv("SCRIBE_LLM", "openai/gpt-4.1-mini")
STT_MODEL = os.getenv("SCRIBE_STT", "deepgram/nova-3")
TTS_MODEL = os.getenv("SCRIBE_TTS", "cartesia/sonic-3")

BASE_INSTRUCTIONS = """\
You are {name}, a note-taker in a video meeting with several people. You speak
through a voice interface, so keep answers to one or two short sentences,
with no lists, markdown or emoji.

Listen more than you talk. Answer when someone addresses you or asks you
something directly. You can:
- recall what this room has decided before, from its room memory;
- save a fact to room memory, but only when someone explicitly asks you to
  remember it;
- summarize the meeting so far, or list its action items;
- save the meeting notes to Shared Files when asked. Notes are also saved
  automatically when the meeting ends.
"""

SUGGESTIONS = ["Summarize so far", "List action items", "What does this room remember?"]

NOTES_PROMPT = """\
Write concise meeting notes in Markdown from the transcript below. Use these
sections: Summary (three to five sentences), Decisions, Action items (owner and
task, when stated), Open questions. Leave out any section with nothing in it.
Do not invent anything that is not in the transcript.
"""


class RoomScribe(ParivoxAgent):
    def __init__(self, parivox: ParivoxRoom) -> None:
        instructions = BASE_INSTRUCTIONS.format(name=parivox.job.name)
        # The prompt written in the Parivox agent editor, if any, refines the
        # base one, so you can tune the agent without redeploying it.
        if parivox.job.persona.system_prompt:
            instructions += f"\n\nInstructions from the agent's owner:\n{parivox.job.persona.system_prompt}"
        super().__init__(parivox, instructions=instructions)
        self.transcript: list[tuple[str, str]] = []
        self.notes_saved_at = 0

    # -- room memory ------------------------------------------------------

    @function_tool
    async def recall_room_memory(self, context: RunContext) -> str:
        """List the facts this room has saved in earlier meetings: decisions,
        names, dates, numbers. Use it when someone asks what the room decided
        or knows, or what you remember."""
        client = self.parivox.client
        if client is None:
            return "Room memory is only available inside Parivox."
        try:
            result = await client.list_room_memories(self.parivox.speaker_identity)
        except ParivoxApiError as exc:
            return exc.message or "Room memory is unavailable right now."
        if not result.memories:
            return "This room's memory is empty."
        return "Room memory:\n" + "\n".join(f"- {memory.text}" for memory in result.memories)

    @function_tool
    async def remember_for_room(self, context: RunContext, fact: str) -> str:
        """Save one fact to this room's memory so it is kept for future
        meetings. Call this only when a participant explicitly asks you to
        remember or note something for the room. Only the room's owner or a
        co-owner may change room memory; if refused, tell them why.

        Args:
            fact: One short statement that makes sense on its own, at most 500
                characters, for example "The launch date is May 3."
        """
        client = self.parivox.client
        if client is None:
            return "Room memory is only available inside Parivox."
        try:
            memory = await client.add_room_memory(self.parivox.speaker_identity, fact)
        except ParivoxApiError as exc:
            return exc.message or "I couldn't save that to room memory."
        return f"Saved to room memory: {memory.text}"

    # -- notes ------------------------------------------------------------

    @function_tool
    async def save_meeting_notes(self, context: RunContext) -> str:
        """Write notes for the meeting so far and save them to the room's Shared
        Files. Use it when someone asks you to save, write up or share the
        notes."""
        return await self.save_notes()

    async def save_notes(self) -> str:
        if not self.transcript:
            return "Nothing has been said yet, so there is nothing to write up."
        client = self.parivox.client
        if client is None:
            return "Shared Files are only available inside Parivox."
        notes = await write_notes(self.transcript)
        filename = f"Meeting notes {meeting_date(self.parivox.job.host_timezone)}.md"
        try:
            saved = await client.add_shared_file(self.parivox.job.room_id, filename, notes)
        except ParivoxApiError as exc:
            return exc.message or "I couldn't save the notes to Shared Files."
        self.notes_saved_at = len(self.transcript)
        return f"Saved {saved.filename} to Shared Files."


async def write_notes(transcript: list[tuple[str, str]]) -> str:
    chat = ChatContext()
    chat.add_message(role="system", content=NOTES_PROMPT)
    chat.add_message(role="user", content="\n".join(f"{speaker}: {text}" for speaker, text in transcript))
    async with inference.LLM(model=LLM_MODEL) as llm:
        response = await llm.chat(chat_ctx=chat).collect()
    return response.text.strip()


def meeting_date(timezone: str) -> str:
    try:
        zone = ZoneInfo(timezone) if timezone else None
    except ZoneInfoNotFoundError:
        zone = None
    return datetime.now(zone).strftime("%Y-%m-%d %H%M")


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


def build_stt(keyterms: tuple[str, ...]) -> inference.STT | str:
    """Deepgram can be told the words it is likely to mishear (names, jargon),
    which are set as "Key terms" in the agent editor."""
    if keyterms and STT_MODEL.startswith("deepgram/"):
        return inference.STT(model=STT_MODEL, extra_kwargs={"keyterm": list(keyterms)})
    return STT_MODEL


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    parivox = ParivoxRoom(ctx)
    scribe = RoomScribe(parivox)
    session = AgentSession(
        stt=build_stt(parivox.job.persona.keyterms),
        llm=LLM_MODEL,
        tts=TTS_MODEL,
        vad=ctx.proc.userdata["vad"],
    )

    # Keep a speaker-labelled transcript for the notes. It keeps growing while
    # the agent is hushed: quiet is not the same as not listening.
    @session.on("user_input_transcribed")
    def _on_transcribed(event) -> None:
        if not event.is_final or not event.transcript.strip():
            return
        speaker = parivox.room.remote_participants.get(parivox.speaker_identity)
        scribe.transcript.append(((speaker.name or speaker.identity) if speaker else "Participant", event.transcript))

    async def _save_notes_on_exit() -> None:
        if len(scribe.transcript) > scribe.notes_saved_at:
            logger.info("Meeting over: %s", await scribe.save_notes())
        await parivox.aclose()

    ctx.add_shutdown_callback(_save_notes_on_exit)

    await parivox.start(session, scribe)
    await parivox.set_suggested_responses(SUGGESTIONS)
    if not parivox.job.persona.intro_prompt and not parivox.hushed:
        session.generate_reply(instructions="Greet the room in one short sentence and say you're taking notes.")


if __name__ == "__main__":
    cli.run_app(server)
