# Game Vids

Desktop app for turning full-game hockey goalie footage into short game videos. See [SPEC.md](SPEC.md) for the plan.

## Development

```sh
uv sync                  # create .venv and install dev tools
uv run gamevids --version
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Layout: `src/gamevids` is the core (no Qt, has the CLI); `src/gamevids_ui` is the Qt app.
