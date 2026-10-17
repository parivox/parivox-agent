# Callback API

Every Parivox dispatch gives the job a **meeting token**: a bearer token scoped to one agent in one room, valid for up to 8 hours. With it, the agent can use Parivox on behalf of the people in the meeting. `ParivoxClient` wraps it:

```python
client = parivox.client  # None outside Parivox
memories = await client.list_room_memories(parivox.speaker_identity)
```

## Who the agent acts as

Methods that read or change room data take `requester_identity`, the LiveKit identity of the person who asked. Use `parivox.speaker_identity`, the person the agent is listening to. Parivox checks **that person's** role in the room, so the agent can never do more than they could themselves: any member may read room memory, but only the room's owner or a co-owner may change it.

When Parivox refuses, `ParivoxApiError.message` explains why, in words you can pass to the model to say to the room.

## Room memory

Room memory holds short facts a room keeps across meetings: decisions, names, dates, numbers. People see and edit the same facts in the room's settings in Parivox.

| Method | Who may call it |
| --- | --- |
| `list_room_memories(requester_identity)` → `RoomMemoryList` (`room_name`, `memories`, `requester_can_change`) | any member |
| `add_room_memory(requester_identity, text)` → `RoomMemory` (at most 500 characters) | owner, co-owner |
| `update_room_memory(requester_identity, memory_id, text)` | owner, co-owner |
| `delete_room_memory(requester_identity, memory_id)` | owner, co-owner |

Only save a fact when someone asks; agents that save on their own initiative fill the room with noise.

## Shared Files

`add_shared_file(room_id, filename, content, *, mime_type=None, index_in_knowledge_base=False, visibility="user")` adds a file of up to 5 MB to the room's Shared Files panel. Pass `parivox.job.room_id`. The agent writes as the room, not as a guest, but Parivox still refuses when the room is off the record or the host has turned Shared Files off.

Set `index_in_knowledge_base=True` to let agents in later meetings search the file.

## Your stored provider keys

`await ProviderKeys.load(parivox.job)` reads provider keys (OpenAI, Deepgram and others) your agent should use. It checks the worker's environment first, then the keys you stored in Parivox under **Settings → Custom API Keys**. Parivox only issues the token for the stored keys when **you**, the agent's owner, are the one using the agent. When someone else adds your agent to their room, only your environment's keys are available, so nobody's keys reach code they don't control.

## Stability

These methods are the stable surface. Today they call the same routes Parivox's own agents use; they will move to a versioned `/api/v1/agent/*` API, and only `parivox_agents/client.py` will change.
