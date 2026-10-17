# Local development

## Run on your machine

```bash
uv run examples/room_scribe/agent.py dev
```

`dev` registers the worker with the LiveKit project in `.env` and reloads it when you save a file. The worker connects **out** to LiveKit and to Parivox, so it works from a laptop behind NAT without a tunnel.

## Keep dev and production apart

LiveKit sends each job to any worker registered under the requested name. If your deployed worker and your laptop both register as `acme-scribe`, meetings get whichever answers first. Use two names and two agent records:

| Parivox agent | Self-hosted worker name | Where it runs |
| --- | --- | --- |
| Scribe | `acme-scribe` | `lk agent deploy` |
| Scribe (dev) | `acme-scribe-dev` | your laptop, with `AGENT_NAME=acme-scribe-dev` |

Add "Scribe (dev)" to a meeting when you want your local code.

## Without Parivox

The agent also runs in rooms Parivox didn't dispatch it to. `ParivoxJob.from_metadata` returns empty defaults, `parivox.job.is_parivox` is `False`, and `parivox.client` is `None`, so the callback tools say they're unavailable instead of failing. This is handy for:

- `uv run examples/room_scribe/agent.py console`, which talks to you through your terminal's microphone and speakers;
- the [LiveKit Agents Playground](https://agents-playground.livekit.io), with an explicit dispatch to your agent name.

To try Parivox-specific behaviour there, put a JSON object with the fields in [protocol.md](protocol.md#joining) in the dispatch metadata, such as `{"resolvedPersona": {"avatar_participant_name": "Scribe", "intro_prompt": "Say hi."}}`.

## Tests

```bash
uv run pytest
```

`tests/conftest.py` has fakes for the LiveKit room, job context and session, so you can test agent behaviour (hush, suggestions, tool results) without a server.
