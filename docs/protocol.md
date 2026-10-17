# In-room protocol (version 1)

This is how an agent and the Parivox meeting client talk inside a LiveKit room. `parivox_agents` implements all of it; read this if you are writing an agent in another language, or want to know what `ParivoxRoom` does for you.

Everything uses standard LiveKit features: **participant attributes** (agent → client state) and **RPC** (client → agent commands, and agent → client requests). Attribute names keep the `nexus.` prefix for compatibility with Parivox's own agents.

## Joining

Parivox dispatches a job to your worker by its agent name. The job's `metadata` is JSON; the fields an agent uses are:

| Field | Meaning |
| --- | --- |
| `agentId` | The Parivox agent record. Publish it as `nexus.agent_id`. |
| `agentInstanceId` | This dispatch. Publish it as `nexus.instance_id`. |
| `roomName` | The LiveKit room. Scheduled rooms are `pr-<roomId>`, permanent-room meetings `pm-<meetingId>`. |
| `hostIdentity` | The identity of the person who added the agent. |
| `dispatchDisplayName` | The agent's name as shown when it was added. |
| `resolvedPersona` | The agent as edited in Parivox: `avatar_participant_name`, `system_prompt`, `intro_prompt`, `end_of_meeting_prompt`, `skills`, `skill_config`, `keyterms`, `visual_representation_type`, `agent_image_url`. |
| `startHushed`, `startQuiet` | Join silent: hushed by the host, or in quiet mode. |
| `startVideoHidden` | The host joined the agent with video hidden. Echo it as `nexus.start_video_hidden`. |
| `hostTimezone` | IANA time zone of the host, for dates the agent writes. |
| `handoffContact` | Optional `{email, phone}` override for handoff. |
| `agentMeetingApiUrl`, `agentMeetingToken` | Callback API origin and bearer token. See [callback-api.md](callback-api.md). |
| `agentRuntimeSecretToken` | Present only when the agent's owner is the actor; fetches their stored provider keys. |

Unknown fields may appear; ignore them.

## Presence

Publish these attributes as soon as you connect. **The client cancels the dispatch if `nexus.type` has not appeared 15 seconds after the agent was added**, so publish before starting slow work such as model connections.

| Attribute | Value |
| --- | --- |
| `nexus.type` | `ai_avatar` |
| `nexus.protocol_version` | `1` |
| `nexus.agent_id` | from `agentId` |
| `nexus.instance_id` | from `agentInstanceId` |
| `nexus.spoken_name` | the name people say to wake the agent; the client shows it in "Say *Okay Scribe*" |
| `nexus.avatar_name` | the name on the tile |
| `nexus.avatar_provider` | `audio_only` for an agent without a video avatar |
| `nexus.visual_representation_type` | `image`, `video_loop` or `livekit_audio_visualizer`: what the tile shows |
| `nexus.agent_image_url` | optional image for the tile |
| `nexus.skills` | JSON array of the persona's skill ids |
| `nexus.control_ready` | `true` once the RPC handlers below are registered. The client hides the hush and remove controls until then. |
| `nexus.hushed` | `true` or `false` |
| `nexus.quiet_mode` | `true` while in quiet mode (a hush the wake phrase can lift) |
| `nexus.suggested_responses` | JSON array of up to 8 strings, shown as tap-to-say buttons; `[]` for none |

All values are strings. Republish the whole set whenever something changes.

## Controls (client → agent RPC)

Register these on the agent's local participant. Payloads are JSON objects; tolerate missing fields and garbage.

| Method | Payload | Agent does | Returns |
| --- | --- | --- | --- |
| `nexus.avatar.hush` | `{mode?: "soft" \| "hard", traceId?}` | Stop speaking now, stay silent, keep listening. Publish `nexus.hushed=true`. | `hushed` |
| `nexus.avatar.unhush` | `{traceId?}` | Speak again; also leaves quiet mode. | `unhushed` |
| `nexus.avatar.terminate` | `{}` | Leave the room and end the job. | `terminating` |
| `nexus.suggested_response.selected` | `{text, speakerName?}` | Treat `text` as said by the caller. Ignore while hushed. | `accepted`, `ignored_hushed`, `ignored_empty` |
| `nexus.handoff.ack` | `{handoffId, roomName, hostIdentity, schemaVersion}` | Mark that handoff as received by a client. | `ack_received` |

## Handoff (agent → client RPC)

To page a human, call `nexus.handoff.initiated` on each person in the room, host first, with:

```json
{
  "handoffId": "hf_<random>",
  "agentId": "<agentId>",
  "roomName": "<roomName>",
  "reason": "<= 280 characters",
  "summary": "<= 4000 characters",
  "spokenName": "a human specialist",
  "humanIdentity": "paged-human",
  "schemaVersion": "1",
  "timeToWaitForHumanSeconds": 30,
  "handoffContact": {"email": "...", "phone": "..."}
}
```

The host's client replies with `nexus.handoff.ack` and pages the contact through Parivox. Retry up to three times, waiting two seconds for the ack each time. If no ack arrives, send `nexus.handoff.failed` to the host with `{handoffId, roomName, schemaVersion, reason: "ack_timeout"}`.

## Other participants

Parivox may add a transcriber (`nexus.type=transcript_service`) and translators (`nexus.type=translation_service`, publishing tracks named `tx:<lang>`), and other agents (`nexus.type=ai_avatar`). Don't listen to them or count them as people. Translation needs nothing else from your agent.

## Leaving

End the job on `nexus.avatar.terminate`, when the room is deleted, and shortly after the last person leaves. `ParivoxRoom` waits 20 seconds, so a dropped connection that rejoins doesn't end the meeting's agent.
