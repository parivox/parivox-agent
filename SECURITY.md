# Security

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/parivox/parivox-agent/security/advisories/new) rather than a public issue. We aim to acknowledge reports within three business days.

## Handling credentials

- Your LiveKit API secret and provider keys belong in `.env` locally and in LiveKit Cloud secrets when deployed. Never commit them or bake them into an image; `.env` is excluded by `.gitignore` and `.dockerignore`.
- The meeting token Parivox gives each job is scoped to one agent in one room and expires within 8 hours. Don't log it or send it anywhere except Parivox.
- Parivox only hands a job your stored provider keys when you are the person using your agent. Treat any key your agent receives as yours alone, and never log it.
