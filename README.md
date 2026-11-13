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

## Desktop app

The app plays video through libmpv, which comes from the system rather than from `uv`:

```sh
sudo apt install libmpv2   # Ubuntu / Debian
uv run gamevids-ui         # takes --db like the CLI
```

**Add a game** with *Add Game…* (Ctrl+N). Pick the game's folder or its files (more can be added before saving). The files are grouped by camera as `gamevids probe` would, D-zone exports are skipped, and the title and date are filled in from the folder name and the files. A file with no camera serial (an older re-encode, say) gets a row of its own: put it on a camera or leave it out, check its period, and set its start time. When the file lost its start time too, putting it on a camera with other files suggests a start from the camera's timecode. The same works from the command line with `gamevids game add --assign FILE CAMERA [--start FILE HH:MM:SS]`.

Pick a game on the left to play its first camera. Each camera's files play back-to-back as one timeline: thin ticks on the seek bar mark where the camera split a file, and heavy marks show where it was stopped and restarted (the gap is cut out). Switching cameras keeps the same moment on the wall clock. The keys are listed under the player.

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
