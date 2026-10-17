import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from parivox_agents import ParivoxApiError, ParivoxClient, ParivoxJob, ProviderKeys


@pytest.fixture
async def parivox():
    """A stand-in for Parivox's callback API that records what it was sent."""
    seen: list[dict] = []

    async def room_memory(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer meeting-token"
        body = dict(request.query) if request.method == "GET" else await request.json()
        seen.append({"method": request.method, **body})
        if body.get("requesterIdentity") == "guest" and request.method != "GET":
            return web.json_response({"error": "Only the room owner or a co-owner can change room memory."}, status=403)
        if request.method == "GET":
            return web.json_response(
                {
                    "roomName": "Launch",
                    "requesterCanChange": True,
                    "memories": [{"memoryId": "m1", "text": "Launch is May 3."}],
                }
            )
        return web.json_response({"memory": {"memoryId": "m2", "text": body.get("text")}})

    async def upload(request: web.Request) -> web.Response:
        body = await request.json()
        seen.append(body)
        return web.json_response({"ok": True, "objectKey": "k/notes.md", "filename": body["filename"]})

    async def secrets(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer runtime-token"
        return web.json_response({"apiKeys": {"openai": {"value": "sk-from-parivox"}, "bogus": "x"}})

    app = web.Application()
    app.router.add_route("*", "/api/agent/room-memory", room_memory)
    app.router.add_post("/api/dropbox/agent-upload", upload)
    app.router.add_post("/api/agent-runtime-secrets", secrets)
    server = TestServer(app)
    await server.start_server()
    server.seen = seen
    yield server
    await server.close()


def job_for(server: TestServer, **extra) -> ParivoxJob:
    return ParivoxJob.from_metadata(
        {
            "agentId": "a1",
            "roomName": "pr-room1",
            "agentMeetingApiUrl": str(server.make_url("")),
            "agentMeetingToken": "meeting-token",
            **extra,
        }
    )


async def test_room_memory_round_trip(parivox):
    async with ParivoxClient.from_job(job_for(parivox)) as client:
        listed = await client.list_room_memories("host")
        assert listed.room_name == "Launch"
        assert listed.requester_can_change is True
        assert [m.text for m in listed.memories] == ["Launch is May 3."]

        saved = await client.add_room_memory("host", "Budget is $45k.")
        assert saved.memory_id == "m2"
        await client.update_room_memory("host", "m2", "Budget is $50k.")
        await client.delete_room_memory("host", "m2")

    assert [s["method"] for s in parivox.seen] == ["GET", "POST", "PATCH", "DELETE"]
    assert parivox.seen[0]["requesterIdentity"] == "host"
    assert parivox.seen[2] == {
        "method": "PATCH",
        "requesterIdentity": "host",
        "memoryId": "m2",
        "text": "Budget is $50k.",
    }


async def test_refusals_carry_parivox_message(parivox):
    async with ParivoxClient.from_job(job_for(parivox)) as client:
        with pytest.raises(ParivoxApiError) as raised:
            await client.add_room_memory("guest", "Nope")
    assert raised.value.status == 403
    assert raised.value.message == "Only the room owner or a co-owner can change room memory."


async def test_unreachable_parivox_is_an_api_error():
    async with ParivoxClient("http://127.0.0.1:9", "t", timeout=1) as client:
        with pytest.raises(ParivoxApiError) as raised:
            await client.list_room_memories("host")
    assert raised.value.status == 0


async def test_shared_file_upload(parivox):
    job = job_for(parivox)
    async with ParivoxClient.from_job(job) as client:
        saved = await client.add_shared_file(job.room_id, "notes.md", "# Notes")
    assert saved.object_key == "k/notes.md"
    sent = parivox.seen[0]
    assert sent["roomId"] == "room1"
    assert sent["mimeType"] == "text/markdown"
    assert base64.b64decode(sent["contentBase64"]) == b"# Notes"
    assert sent["visibility"] == "user" and sent["indexInKb"] is False


def test_no_client_outside_parivox():
    assert ParivoxClient.from_job(ParivoxJob.from_metadata(None)) is None


async def test_provider_keys_prefer_the_environment(parivox, monkeypatch):
    job = job_for(parivox, agentRuntimeSecretToken="runtime-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    keys = await ProviderKeys.load(job)
    assert keys.parivox_providers == ["openai"]
    assert keys.get("openai") == "sk-from-parivox"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert keys.get("OpenAI") == "sk-from-env"


async def test_provider_keys_without_a_token_use_only_the_environment(parivox, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    keys = await ProviderKeys.load(job_for(parivox))
    assert keys.parivox_providers == []
    assert keys.get("deepgram") is None
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        keys.require("deepgram")
