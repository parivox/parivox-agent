# Deploying to LiveKit Cloud

[LiveKit Cloud agent hosting](https://docs.livekit.io/agents/ops/deployment/) builds the repo's `Dockerfile`, runs it next to your project, and scales it with demand.

## 1. Create the deployment

Install the [LiveKit CLI](https://docs.livekit.io/home/cli/) and sign in with `lk cloud auth`. Then, from the repo root:

```bash
lk agent create --secrets-file .env
```

This builds and starts the agent and writes `livekit.toml`, which records your project and agent id. Commit `livekit.toml` in your fork so later deploys find it.

Set the production name in `.env` (or with `lk agent update-secrets`) before you create it:

```bash
AGENT_NAME=acme-scribe
```

Later changes deploy with:

```bash
lk agent deploy
```

## 2. Register the agent in Parivox

In Parivox, create (or edit) the agent and set **Runs on → Self-hosted worker name** to the same `AGENT_NAME`. The prompt, greeting, skills and handoff contact are all edited there and take effect on the next meeting without a redeploy.

## 3. Check it

Add the agent to a meeting in one of your rooms, then:

```bash
lk agent logs
```

You should see `Parivox agent <id> live in pr-…`.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Adding the agent says it can only join rooms hosted on its owner's LiveKit project | The room isn't on your project: turn on **Use my LiveKit config** in Settings, and use one of your own rooms. |
| The agent is added but never appears, and disappears from the list after 15 s | No worker is registered under that name on your project, or the worker crashed before publishing presence. Check `lk agent logs` and that the names match exactly. |
| Room memory or Shared Files say they're unavailable | The job had no callback token (not dispatched by Parivox), or the room is off the record. |
| The agent answers while hushed | Your agent overrides `on_user_turn_completed` without calling `super()`. |

## Secrets

Keep keys in LiveKit Cloud secrets (`--secrets-file`), never in the image. `.env` is in `.gitignore` and `.dockerignore`.
