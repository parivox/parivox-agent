"""Provider API keys: your own environment first, then the keys stored in Parivox.

A self-hosted agent normally brings its own keys in ``.env`` or in your LiveKit
Cloud secrets. If you would rather keep them in Parivox (Settings → Custom API Keys),
Parivox hands the job a short-lived token to fetch them, but only when you,
the agent's owner, are the one using the agent. When a co-host adds your agent
to their room, no token is issued, so their keys never reach your code and
your environment's keys are the only ones available.

You don't need either when you use LiveKit Inference (``"openai/gpt-4.1-mini"``
style model strings), which bills through your LiveKit project.
"""

from __future__ import annotations

import logging
import os

import aiohttp

from .metadata import ParivoxJob

logger = logging.getLogger("parivox_agents")

PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "gladia": "GLADIA_API_KEY",
    "elevenlabs": "ELEVEN_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

FETCH_TIMEOUT_SECONDS = 5.0


class ProviderKeys:
    """Look up a provider's API key for this job."""

    def __init__(self, parivox_keys: dict[str, str] | None = None) -> None:
        self._parivox_keys = dict(parivox_keys or {})

    @classmethod
    async def load(cls, job: ParivoxJob) -> ProviderKeys:
        """Fetch the owner's Parivox-stored keys when the job carries a token.

        Never raises: a failed fetch is logged and leaves the environment as
        the only source.
        """
        if not job.runtime_secret_token:
            return cls()
        api_url = str(job.raw.get("agentRuntimeSecretApiUrl") or job.api_url).rstrip("/")
        if not api_url:
            return cls()
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f"{api_url}/api/agent-runtime-secrets",
                    headers={
                        "Authorization": f"Bearer {job.runtime_secret_token}",
                        "Accept": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
                ) as response,
            ):
                if response.status >= 400:
                    logger.warning("Parivox key fetch refused (HTTP %s).", response.status)
                    return cls()
                body = await response.json(content_type=None)
        except Exception as exc:
            logger.warning("Parivox key fetch failed: %s", exc)
            return cls()
        keys: dict[str, str] = {}
        raw = body.get("apiKeys") if isinstance(body, dict) else None
        for provider, entry in (raw or {}).items():
            value = entry.get("value") if isinstance(entry, dict) else None
            if isinstance(value, str) and value.strip():
                keys[str(provider).strip().lower()] = value.strip()
        logger.info("Loaded Parivox-stored keys for: %s", ", ".join(sorted(keys)) or "none")
        return cls(keys)

    def get(self, provider: str) -> str | None:
        """The key for ``provider`` (e.g. ``"openai"``): environment first, then Parivox."""
        provider_id = provider.strip().lower()
        env_var = PROVIDER_ENV_VARS.get(provider_id)
        if env_var:
            from_env = os.getenv(env_var, "").strip()
            if from_env:
                return from_env
        return self._parivox_keys.get(provider_id)

    def require(self, provider: str) -> str:
        value = self.get(provider)
        if not value:
            env_var = PROVIDER_ENV_VARS.get(provider.strip().lower(), f"{provider.upper()}_API_KEY")
            raise RuntimeError(f"No {provider} API key: set {env_var} or store one in Parivox.")
        return value

    @property
    def parivox_providers(self) -> list[str]:
        """Providers whose keys came from Parivox, for logging. Never log the keys."""
        return sorted(self._parivox_keys)
