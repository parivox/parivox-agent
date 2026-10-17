"""``ParivoxAgent``: a LiveKit ``Agent`` that behaves well in a Parivox room.

Subclass it instead of ``Agent``. It adds the capability tools and their
prompt text, and stays silent while hushed. Everything else is plain LiveKit
Agents: add your own ``@function_tool`` methods, override hooks, and so on.
"""

from __future__ import annotations

from typing import Any

from livekit.agents import Agent
from livekit.agents.llm import ChatContext, ChatMessage

from .room import ParivoxRoom


class ParivoxAgent(Agent):
    def __init__(self, parivox: ParivoxRoom, *, instructions: str, tools: list | None = None, **kwargs: Any) -> None:
        """``instructions`` is your agent's own prompt; capability text is appended."""
        extra = parivox.instructions()
        super().__init__(
            instructions=f"{instructions}\n\n{extra}" if extra else instructions,
            tools=[*parivox.tools(), *(tools or [])],
            **kwargs,
        )
        self.parivox = parivox

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """If you override this, call ``await super().on_user_turn_completed(...)`` first."""
        await self.parivox.before_reply(turn_ctx, new_message)
