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

## Settings

Clip detection reads its settings from the `[detect]` section of `~/.config/gamevids/config.toml` (or `$GAMEVIDS_CONFIG`, or `--config`). The file is optional, and any setting left out keeps its default:

```toml
[detect]
threshold = 0.04     # fraction of the D-zone moving, after smoothing
smoothing = 2.0      # seconds averaged before applying the threshold
min_duration = 2.0   # seconds above the threshold for a burst to count
merge_gap = 4.0      # seconds; bursts or clips closer than this become one clip
pad_before = 3.0     # seconds added before each clip
pad_after = 2.0      # seconds added after each clip
```
