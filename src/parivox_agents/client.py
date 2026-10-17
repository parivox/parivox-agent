"""A typed client for the Parivox agent callback API.

Every Parivox dispatch carries a meeting token scoped to one agent in one room.
With it the agent can read and change the room's memory and add files to the
room's Shared Files, with the same permissions as the person it is acting for.

The method names here are the stable surface. The routes underneath are the
ones Parivox's own agents use today and will move to ``/api/v1/agent/*``;
when they do, only this module changes.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from typing import Any, Literal

import aiohttp

from .metadata import ParivoxJob

DEFAULT_TIMEOUT_SECONDS = 10.0


class ParivoxApiError(Exception):
    """A callback API request that Parivox refused or could not complete.

    ``message`` is Parivox's own explanation when it gave one, written to be
    safe to read out to the room (for example "Only the room owner or a
    co-owner can change room memory.").
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Parivox API error {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True)
class RoomMemory:
    memory_id: str
    text: str


@dataclass(frozen=True)
class RoomMemoryList:
    room_name: str
    memories: list[RoomMemory]
    requester_can_change: bool | None
    """Whether the participant asked for may change room memory; ``None`` if unknown."""


@dataclass(frozen=True)
class SharedFile:
    filename: str
    object_key: str


class ParivoxClient:
    """Call Parivox on behalf of this job.

    Methods that act for a person take ``requester_identity``: the LiveKit
    identity of the participant who asked. Parivox checks that person's role in
    the room, so the agent can never do more than they could.
    """

    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @classmethod
    def from_job(cls, job: ParivoxJob, **kwargs: Any) -> ParivoxClient | None:
        """A client for this job, or ``None`` when Parivox gave it no callback token."""
        if not job.has_callback_api:
            return None
        return cls(job.api_url, job.meeting_token, **kwargs)

    async def aclose(self) -> None:
        if self._session is not None and self._owns_session:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> ParivoxClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- room memory ------------------------------------------------------

    async def list_room_memories(self, requester_identity: str) -> RoomMemoryList:
        """Every fact in the room's memory. Any member of the room may read."""
        body = await self._request("GET", "/api/agent/room-memory", params={"requesterIdentity": requester_identity})
        memories = [
            RoomMemory(memory_id=str(item.get("memoryId") or ""), text=str(item.get("text") or ""))
            for item in body.get("memories") or []
            if isinstance(item, dict)
        ]
        can_change = body.get("requesterCanChange")
        return RoomMemoryList(
            room_name=str(body.get("roomName") or ""),
            memories=memories,
            requester_can_change=can_change if isinstance(can_change, bool) else None,
        )

    async def add_room_memory(self, requester_identity: str, text: str) -> RoomMemory:
        """Save one fact (up to 500 characters). Only owners and co-owners may write."""
        body = await self._request(
            "POST", "/api/agent/room-memory", json={"requesterIdentity": requester_identity, "text": text}
        )
        return self._memory(body, text)

    async def update_room_memory(self, requester_identity: str, memory_id: str, text: str) -> RoomMemory:
        body = await self._request(
            "PATCH",
            "/api/agent/room-memory",
            json={"requesterIdentity": requester_identity, "memoryId": memory_id, "text": text},
        )
        return self._memory(body, text)

    async def delete_room_memory(self, requester_identity: str, memory_id: str) -> None:
        await self._request(
            "DELETE",
            "/api/agent/room-memory",
            json={"requesterIdentity": requester_identity, "memoryId": memory_id},
        )

    # -- shared files -----------------------------------------------------

    async def add_shared_file(
        self,
        room_id: str,
        filename: str,
        content: str | bytes,
        *,
        mime_type: str | None = None,
        index_in_knowledge_base: bool = False,
        visibility: Literal["user", "agent", "both"] = "user",
    ) -> SharedFile:
        """Add a file (up to 5 MB) to the room's Shared Files panel.

        ``room_id`` is ``ParivoxJob.room_id``. The upload is refused while the
        room is off the record or the host has turned Shared Files off.
        """
        data = content.encode("utf-8") if isinstance(content, str) else content
        body = await self._request(
            "POST",
            "/api/dropbox/agent-upload",
            json={
                "roomId": room_id,
                "filename": filename,
                "mimeType": mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                "contentBase64": base64.b64encode(data).decode("ascii"),
                "indexInKb": index_in_knowledge_base,
                "visibility": visibility,
            },
        )
        return SharedFile(
            filename=str(body.get("filename") or filename),
            object_key=str(body.get("objectKey") or ""),
        )

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _memory(body: dict[str, Any], fallback_text: str) -> RoomMemory:
        memory = body.get("memory") if isinstance(body.get("memory"), dict) else {}
        return RoomMemory(
            memory_id=str(memory.get("memoryId") or ""),
            text=str(memory.get("text") or fallback_text),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        try:
            async with self._session.request(
                method,
                f"{self._api_url}{path}",
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=self._timeout,
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = None
                if not isinstance(body, dict):
                    body = {}
                if response.status >= 400:
                    message = str(body.get("error") or body.get("message") or response.reason or "")
                    raise ParivoxApiError(response.status, message)
                return body
        except aiohttp.ClientError as exc:
            raise ParivoxApiError(0, f"Parivox could not be reached: {exc}") from exc
