"""Quiet mode: the agent goes silent when asked, and wakes on "Okay <name>".

Someone says "give us a minute" or "be quiet"; the model calls
``enter_quiet_mode``; the agent stops talking but keeps listening, so it still
knows what was said. Saying "Okay <name>" brings it back, and whatever follows
the wake phrase in the same breath is answered.

The meeting client shows the state from ``nexus.quiet_mode`` and tells people
the wake phrase from ``nexus.spoken_name``. A host's hush button is different:
it is lifted only by the host, never by the wake phrase.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from livekit.agents import RunContext, function_tool

if TYPE_CHECKING:
    from ..room import ParivoxRoom

logger = logging.getLogger("parivox_agents")

WAKE_PREFIXES = ("okay", "ok")
FUZZY_NAME_THRESHOLD = 0.7
REENTRY_COOLDOWN_SECONDS = 15.0

REENGAGEMENT_INSTRUCTIONS = (
    "You were in quiet mode and the participant just said your wake phrase. "
    "Quiet mode is now off. Give a brief, natural acknowledgement and respond "
    "to anything else they said."
)

_QUIET_REQUEST = re.compile(
    r"\b(?:"
    r"(?:be|stay|go|get|keep)\s+(?:quiet|silent)"
    r"|(?:mute|hush)\s*(?:yourself)?"
    r"|(?:stop|don't|do\s+not)\s+(?:talk(?:ing)?|speak(?:ing)?)"
    r"|(?:hold\s+on|wait)(?:\s+(?:a|one)\s+(?:moment|minute|second|sec))?"
    r"|give\s+(?:me|us)\s+(?:a|one)\s+(?:moment|minute|second|sec)"
    r"|quiet\s+mode"
    r")\b",
    re.IGNORECASE,
)


def is_quiet_request(text: str) -> bool:
    """Whether ``text`` asks the agent to be quiet.

    The model decides when to call ``enter_quiet_mode``; this check stops it
    from going quiet on its own initiative, which looks like a crash.
    """
    return bool(_QUIET_REQUEST.search(" ".join(text.split())))


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def match_wake_phrase(text: str, names: str | Iterable[str]) -> str | None:
    """If ``text`` contains "Okay <name>", return what follows it, else ``None``.

    Punctuation and case are ignored ("OK, Scribe." matches "Scribe"), and a
    close mis-transcription of a one-word name still matches ("Okay Scrib").
    The remainder comes from the original text so it reads naturally.
    """
    wanted = [_normalize(name) for name in ([names] if isinstance(names, str) else names)]
    wanted = [name for name in wanted if name]
    if not wanted:
        return None
    words = text.split()
    normalized = [_normalize(word) for word in words]
    for index, word in enumerate(normalized):
        if word not in WAKE_PREFIXES:
            continue
        rest = normalized[index + 1 :]
        for name in wanted:
            name_words = name.split()
            candidate = rest[: len(name_words)]
            if not candidate:
                continue
            exact = candidate == name_words
            fuzzy = len(name_words) == 1 and (
                SequenceMatcher(None, candidate[0], name_words[0]).ratio() >= FUZZY_NAME_THRESHOLD
            )
            if exact or fuzzy:
                remainder = " ".join(words[index + 1 + len(name_words) :])
                return remainder.lstrip(" ,.;:!?").strip()
    return None


class QuietMode:
    """The quiet-mode capability. ``ParivoxRoom`` creates and wires it."""

    def __init__(self, room: ParivoxRoom) -> None:
        self._room = room
        self._exited_at = 0.0
        self._wake_pending = False
        self._recent_user_text = ""

    @property
    def wake_names(self) -> list[str]:
        job = self._room.job
        names = [job.name, job.persona.name, job.display_name]
        seen: set[str] = set()
        unique = []
        for name in names:
            if name and name.lower() not in seen:
                seen.add(name.lower())
                unique.append(name)
        return unique

    def tools(self) -> list:
        quiet_mode = self

        @function_tool
        async def enter_quiet_mode(context: RunContext) -> str:
            """Immediately go silent when the user asks you to be quiet, hold on,
            give them a minute, wait, mute yourself, or otherwise signals they need
            you to stop talking. Do NOT say anything before calling this tool: no
            acknowledgment, no goodbye, nothing. Just call this tool immediately.
            The user can resume the conversation by saying "Okay" and your name.
            """
            return await quiet_mode.enter_from_tool()

        return [enter_quiet_mode]

    async def enter_from_tool(self) -> str:
        if not is_quiet_request(self._recent_user_text):
            logger.info("Ignoring enter_quiet_mode: the last user turn did not ask for quiet.")
            return "Quiet mode was not requested. Respond to what the participant actually asked."
        if self._room.hushed:
            return "Already quiet."
        if time.monotonic() - self._exited_at < REENTRY_COOLDOWN_SECONDS:
            # The wake-phrase turn can reach the model with the earlier
            # enter_quiet_mode call still in context; without this the agent
            # wakes and goes straight back to sleep.
            return "Quiet mode was just exited. You are not in quiet mode; respond normally."
        await self.enter()
        return f'You are now in quiet mode. Say nothing more. They can say "Okay {self._room.job.name}" to resume.'

    async def enter(self) -> None:
        self._recent_user_text = ""
        await self._room.hush(quiet_mode=True)
        logger.info("Entered quiet mode.")

    def on_user_transcript(self, text: str, is_final: bool) -> None:
        """Called for every user transcript. Wakes the agent from quiet mode."""
        if not is_final or not text.strip():
            return
        self._recent_user_text = text
        if not self._room.state.quiet_mode or self._wake_pending:
            return
        remainder = match_wake_phrase(text, self.wake_names)
        if remainder is None:
            return
        logger.info("Wake phrase heard; leaving quiet mode.")
        self._wake_pending = True
        self._exited_at = time.monotonic()
        self._room.spawn(self._room.unhush())

    def consume_wake(self) -> bool:
        """True once after a wake phrase, so the next reply re-engages."""
        pending, self._wake_pending = self._wake_pending, False
        return pending
