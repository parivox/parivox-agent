# Contributing

Thanks for helping make Parivox agents easier to build.

## Set up

```bash
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

CI runs the same three commands on Python 3.11 and 3.13.

## What goes where

- `src/parivox_agents/` is the package. Keep it small and dependency-light: it is what every Parivox agent imports. Behaviour that only one kind of agent wants belongs in an example.
- `examples/` holds complete, runnable agents. Each should read top to bottom in one sitting.
- `docs/protocol.md` is the specification for the in-room protocol. A change to attributes or RPCs changes it in the same pull request.

## Guidelines

- Docstrings on `@function_tool` functions are prompt text the model reads. Change them deliberately and test the behaviour, not just the wording.
- Agents must keep working outside Parivox (`console`, the LiveKit playground), so never assume a field of `ParivoxJob` is set.
- Add a test with the fakes in `tests/conftest.py` for any behaviour change.

## Pull requests

Open an issue first for anything large. Keep pull requests focused, and describe what a user of the package would notice.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
