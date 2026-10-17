"""``ParivoxRoom``: run a LiveKit ``AgentSession`` as a Parivox room agent.

It does the work every Parivox agent needs and no agent should have to write:

- publishes the agent's presence within the client's deadline, so the
  meeting shows it as an agent and does not cancel the dispatch;
- registers the hush, unhush and terminate controls the meeting UI calls;
- listens to whoever is speaking, not just the first person who joined;
- ignores Parivox's transcriber and translator participants;
- ends the job shortly after the last person leaves;
- wires the quiet-mode and handoff capabilities and the suggestion buttons.

Translation needs nothing from the agent: Parivox runs it as a separate
participant that the agent already ignores.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from livekit import rtc
from livekit.agents import AgentSession, JobContext, StopResponse, room_io
from livekit.agents.llm import ChatContext, ChatMessage

from .capabilities.handoff import Handoff
from .capabilities.quiet_mode import REENGAGEMENT_INSTRUCTIONS, QuietMode
from .client import ParivoxClient
from .metadata import ParivoxJob
from .protocol import (
    MAX_SUGGESTED_RESPONSES,
    RPC_HANDOFF_ACK,
    RPC_HUSH,
    RPC_SUGGESTED_RESPONSE_SELECTED,
    RPC_TERMINATE,
    RPC_UNHUSH,
    PresenceState,
    build_attributes,
    is_parivox_service,
    parse_payload,
)

logger = logging.getLogger("parivox_agents")

EMPTY_ROOM_GRACE_SECONDS = 20.0

SuggestionHandler = Callable[[str, str], Awaitable[None]]
"""Called with (text, participant identity) when someone taps a suggestion."""


class ParivoxRoom:
    """One agent's presence in one Parivox room.

    Create it first, build your ``Agent`` with ``parivox.tools()`` (or subclass
    ``ParivoxAgent``, which does that), then call ``start``.
    """

    def __init__(
        self,
        ctx: JobContext,
        job: ParivoxJob | None = None,
        *,
        quiet_mode: bool = True,
        handoff: bool = True,
    ) -> None:
        self.ctx = ctx
        self.job = job or ParivoxJob.from_metadata(ctx.job.metadata, room_name=ctx.job.room.name)
        self.client = ParivoxClient.from_job(self.job)
        """The callback API client, or ``None`` outside Parivox."""
        self.state = PresenceState(
            hushed=self.job.start_hushed or self.job.start_quiet,
            quiet_mode=self.job.start_quiet,
        )
        self.quiet_mode = QuietMode(self) if quiet_mode else None
        self.handoff = Handoff(self) if handoff else None
        self._session: AgentSession | None = None
        self._publish_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._empty_room_timer: asyncio.TimerHandle | None = None
        self._suggestion_handler: SuggestionHandler | None = None
        self._speaker_identity = ""

    # -- what the agent reads ---------------------------------------------

    @property
    def room(self) -> rtc.Room:
        return self.ctx.room

    @property
    def session(self) -> AgentSession:
        if self._session is None:
            raise RuntimeError("ParivoxRoom.start() has not been called yet.")
        return self._session

    @property
    def hushed(self) -> bool:
        return self.state.hushed

    @property
    def speaker_identity(self) -> str:
        """The participant the agent is listening to: who asked, for permission checks."""
        if self._session is not None:
            linked = self._session.room_io.linked_participant
            if linked is not None:
                return linked.identity
        return self._speaker_identity

    def humans(self) -> list[rtc.RemoteParticipant]:
        """The people in the room: no agents, transcribers or translators."""
        return [p for p in self.room.remote_participants.values() if self._is_human(p)]

    def tools(self) -> list:
        """Function tools for the enabled capabilities. Pass them to your ``Agent``."""
        tools: list = []
        if self.quiet_mode is not None:
            tools += self.quiet_mode.tools()
        if self.handoff is not None:
            tools += self.handoff.tools()
        return tools

    def instructions(self) -> str:
        """Prompt text the enabled capabilities need. Append it to your instructions."""
        parts = []
        if self.quiet_mode is not None:
            parts.append(
                f"Your name is {self.job.name}. If you are asked to be quiet, call enter_quiet_mode "
                f'right away; people wake you by saying "Okay {self.job.name}".'
            )
        if self.handoff is not None and self.handoff.available:
            parts.append(self.handoff.instructions())
        return "\n\n".join(part for part in parts if part)

    # -- lifecycle --------------------------------------------------------

    async def start(
        self,
        session: AgentSession,
        agent: Any,
        *,
        room_options: room_io.RoomOptions | None = None,
        greet: bool = True,
    ) -> None:
        """Join the room, start ``session`` with ``agent``, and go live.

        Pass ``greet=False`` to skip the greeting written in the agent editor.
        """
        await self.ctx.connect()
        # Presence first: the meeting client cancels a dispatch whose agent has
        # not announced itself within 15 seconds, and session start can be slow.
        await self.publish()
        self._wire_room_events()

        options = room_options or room_io.RoomOptions()
        # People come and go in a meeting; the session must outlive whoever it
        # happened to be listening to. The room's own lifecycle ends the job.
        options.close_on_disconnect = False
        await session.start(agent, room=self.room, room_options=options)
        self._session = session
        self._wire_session_events()

        if self.state.hushed:
            self._apply_output(enabled=False)
        self._register_rpcs()
        if not self._listen_to(self.job.host_identity):
            self._listen_to_anyone()

        self.state.control_ready = True
        await self.publish()
        logger.info(
            "Parivox agent %s live in %s (hushed=%s).", self.job.agent_id or "local-dev", self.room.name, self.hushed
        )

        if greet and not self.hushed and self.job.persona.intro_prompt:
            session.generate_reply(instructions=self.job.persona.intro_prompt)

    async def publish(self) -> None:
        """Republish the agent's attributes after changing ``state``."""
        async with self._publish_lock:
            try:
                await self.room.local_participant.set_attributes(build_attributes(self.job, self.state))
            except Exception:
                logger.exception("Could not publish agent attributes.")

    async def aclose(self) -> None:
        if self._empty_room_timer is not None:
            self._empty_room_timer.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self.client is not None:
            await self.client.aclose()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Run ``coro`` in the background, kept alive until it finishes or the job ends."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- hush, quiet mode and suggestions ---------------------------------

    async def hush(self, *, quiet_mode: bool = False) -> None:
        """Stop speaking at once and stay silent, still listening.

        A host hush is lifted only by ``unhush``; ``quiet_mode=True`` also lets
        the wake phrase lift it.
        """
        self.state.hushed = True
        self.state.quiet_mode = self.state.quiet_mode or quiet_mode
        if self._session is not None:
            self._apply_output(enabled=False)
            self._session.interrupt()
        await self.publish()

    async def unhush(self) -> None:
        self.state.hushed = False
        self.state.quiet_mode = False
        if self._session is not None:
            self._apply_output(enabled=True)
        await self.publish()

    async def set_suggested_responses(self, responses: list[str]) -> None:
        """Show up to eight tap-to-say buttons on the agent's tile; ``[]`` clears them."""
        cleaned = [text.strip() for text in responses if text and text.strip()]
        self.state.suggested_responses = cleaned[:MAX_SUGGESTED_RESPONSES]
        await self.publish()

    def on_suggestion(self, handler: SuggestionHandler) -> SuggestionHandler:
        """Replace what happens when someone taps a suggestion. Usable as a decorator.

        By default the agent answers the tapped text as if the person had said it.
        """
        self._suggestion_handler = handler
        return handler

    async def before_reply(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """Call from ``Agent.on_user_turn_completed``; ``ParivoxAgent`` does.

        Keeps a hushed agent from answering (the turn still lands in its
        context) and re-engages after a wake phrase.
        """
        if self.quiet_mode is not None:
            self.quiet_mode.on_user_transcript(new_message.text_content or "", True)
            if self.quiet_mode.consume_wake():
                turn_ctx.add_message(role="system", content=REENGAGEMENT_INSTRUCTIONS)
                return
        if self.hushed:
            raise StopResponse()

    # -- internals --------------------------------------------------------

    def _apply_output(self, *, enabled: bool) -> None:
        output = self.session.output
        output.set_audio_enabled(enabled)
        output.set_transcription_enabled(enabled)

    def _is_human(self, participant: rtc.RemoteParticipant) -> bool:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return False
        if is_parivox_service(participant.attributes):
            return False
        # An avatar provider publishing on behalf of an agent is not a person.
        return not (participant.attributes or {}).get("lk.publish_on_behalf")

    def _listen_to(self, identity: str) -> bool:
        """Switch the agent's ears to ``identity`` if that person is here.

        RoomIO waits forever for an identity that never joins, so only switch
        to someone present.
        """
        participant = self.room.remote_participants.get(identity) if identity else None
        if participant is None or not self._is_human(participant) or self._session is None:
            return False
        self._speaker_identity = identity
        self._session.room_io.set_participant(identity)
        return True

    def _listen_to_anyone(self) -> bool:
        return any(self._listen_to(p.identity) for p in self.humans())

    def _wire_room_events(self) -> None:
        room = self.room

        @room.on("active_speakers_changed")
        def _on_active_speakers(speakers: list[rtc.Participant]) -> None:
            for speaker in speakers:
                if self._listen_to(speaker.identity):
                    return

        @room.on("participant_connected")
        def _on_connected(participant: rtc.RemoteParticipant) -> None:
            if self._is_human(participant) and self._empty_room_timer is not None:
                self._empty_room_timer.cancel()
                self._empty_room_timer = None

        @room.on("participant_disconnected")
        def _on_disconnected(participant: rtc.RemoteParticipant) -> None:
            if participant.identity == self._speaker_identity:
                self._listen_to_anyone()
            if not self.humans() and self._empty_room_timer is None:
                logger.info("Last person left; ending in %.0fs unless someone returns.", EMPTY_ROOM_GRACE_SECONDS)
                self._empty_room_timer = asyncio.get_running_loop().call_later(
                    EMPTY_ROOM_GRACE_SECONDS, self.ctx.shutdown, "room empty"
                )

    def _wire_session_events(self) -> None:
        if self.quiet_mode is None:
            return
        quiet_mode = self.quiet_mode

        @self.session.on("user_input_transcribed")
        def _on_transcribed(event: Any) -> None:
            quiet_mode.on_user_transcript(event.transcript, event.is_final)

    def _register_rpcs(self) -> None:
        local = self.room.local_participant

        @local.register_rpc_method(RPC_HUSH)
        async def _hush(data: rtc.RpcInvocationData) -> str:
            logger.info("Hushed by %s.", data.caller_identity)
            await self.hush()
            return "hushed"

        @local.register_rpc_method(RPC_UNHUSH)
        async def _unhush(data: rtc.RpcInvocationData) -> str:
            logger.info("Unhushed by %s.", data.caller_identity)
            await self.unhush()
            return "unhushed"

        @local.register_rpc_method(RPC_TERMINATE)
        async def _terminate(data: rtc.RpcInvocationData) -> str:
            logger.info("Removed from the room by %s.", data.caller_identity)
            self.ctx.shutdown(reason="removed by host")
            return "terminating"

        @local.register_rpc_method(RPC_SUGGESTED_RESPONSE_SELECTED)
        async def _suggestion(data: rtc.RpcInvocationData) -> str:
            text = str(parse_payload(data.payload).get("text") or "").strip()
            if not text:
                return "ignored_empty"
            if self.hushed:
                return "ignored_hushed"
            self._listen_to(data.caller_identity)
            if self._suggestion_handler is not None:
                await self._suggestion_handler(text, data.caller_identity)
            else:
                self.session.interrupt()
                self.session.generate_reply(user_input=text)
            return "accepted"

        if self.handoff is not None:
            handoff = self.handoff

            @local.register_rpc_method(RPC_HANDOFF_ACK)
            async def _handoff_ack(data: rtc.RpcInvocationData) -> str:
                return handoff.on_ack(data.payload)
