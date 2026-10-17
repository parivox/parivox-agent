# Builds examples/room_scribe for LiveKit Cloud (`lk agent create` / `lk agent deploy`).
# To deploy your own agent, change the two paths marked below.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

ARG UID=10001
RUN adduser --disabled-password --gecos "" --home /app --uid "${UID}" appuser
WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --extra examples

# Your agent's code.
COPY examples/room_scribe ./examples/room_scribe

RUN chown -R appuser:appuser /app
USER appuser

# Fetch the voice-activity model at build time, not on the first call.
RUN uv run --no-sync examples/room_scribe/agent.py download-files

# Your agent's entry point.
CMD ["uv", "run", "--no-sync", "examples/room_scribe/agent.py", "start"]
