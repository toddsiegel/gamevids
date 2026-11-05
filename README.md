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

Period proposals (`gamevids periods`) read the `[periods]` section of the same file:

```toml
[periods]
smoothing = 5.0          # seconds averaged on each camera first
quiet_level = 0.015      # every D-zone below this fraction moving counts as quiet
merge_gap = 15.0         # seconds; quiet spells this close become one...
blip_level = 0.04        # ...unless any D-zone gets this busy between them
min_quiet = 30.0         # seconds; a shorter quiet spell can't be a break
lead = 10.0              # seconds a period starts before its quiet spell ends
min_period = 480.0       # seconds; the shortest a period can be
evenness = 0.25          # seconds of quiet given up per second between the longest and shortest period
warmup_level = 0.02      # every D-zone above this fraction moving counts as warmup
warmup_window = 30.0     # seconds; every zone busy for over half of this is warmup
min_warmup = 60.0        # seconds of warmup needed to count
```
