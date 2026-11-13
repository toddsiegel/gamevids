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
uv run gamevids-ui         # takes --db and --config like the CLI
```

**Add a game** with *Add Game…* (Ctrl+N). Pick the game's folder or its files (more can be added before saving). The files are grouped by camera as `gamevids probe` would, D-zone exports are skipped, and the title and date are filled in from the folder name and the files. A file with no camera serial (an older re-encode, say) gets a row of its own: put it on a camera or leave it out, check its period, and set its start time. When the file lost its start time too, putting it on a camera with other files suggests a start from the camera's timecode. The same works from the command line with `gamevids game add --assign FILE CAMERA [--start FILE HH:MM:SS]`.

Pick a game on the left to play its first camera. Each camera's files play back-to-back as one timeline: thin ticks on the seek bar mark where the camera split a file, and heavy marks show where it was stopped and restarted (the gap is cut out). Switching cameras keeps the same moment on the wall clock. The keys are listed under the player.

**Draw the D-zones** on the *D-Zones* tab (Alt+2; a new game opens there). Each camera shows a still frame with its zone over it. A camera with no zone yet starts from the nearest game's zone (the same camera if it was there), or from a default shape for the first game, so it usually needs only a nudge. Drag corners to move them, double-click an edge to add one, right-click a corner to remove it, or *Redraw* to place the corners one by one. *Another Frame* shows a different moment if players hide the ice. *Save and Scan* (Ctrl+S) saves the zone, starts that camera's scan in the background and moves on to the next camera, so you can draw it while the first one scans. Scans run one at a time (at about 8× real time here) and show their progress under the frame and in the status bar. When a scan finishes, the camera's candidate clips are found with the `[detect]` settings below, unless some of its clips were already triaged; those are kept, and `gamevids detect --force` replaces them. Quitting during a scan stops it, and nothing partial is saved.

**Check the periods** on the *Periods* tab (Alt+3). Once every camera is scanned, the periods are proposed from the quiet spells between them, as `gamevids periods` does, and saved. The timeline under the video shows each camera's activity in its own lane, with quiet spells shaded and a marker at each period start. Click a marker (or a row of the table, or `[` / `]`) to jump the video there, click a lane to watch that camera at that moment, and drag a marker to move a start while the video follows it. For an exact start, play to the puck drop and press M (*Start Here*). *Add Period* adds overtime at the playhead, *Remove Last* drops the last period, and *Propose Again* starts over. Choose the camera behind our goalie's net in P1 and later periods alternate ends, or change any later period on its own; their lanes are tinted green. Every change is saved at once, and Ctrl+Z undoes it.

**Triage the clips** on the *Triage* tab (Alt+4). Only our goalie's clips are up for triage: those that start during a period on the camera behind our goalie's net (chosen on the *Periods* tab), listed on the right in wall-clock order. Each clip plays at 2× from its in point and pauses at its out point. Press Enter to keep it or X to discard it, and the next one plays. S stars a clip for the highlights library, I and O move its start and end to the playhead, and U (or Ctrl+Z) undoes the last change and goes back to that clip. Space plays and pauses (at the end of a clip it plays it again), J jumps back 2 s, K pauses, L plays and then speeds up, ← and → step one frame, and [ and ] change the speed. The bar under the video spans the clip plus 10 s either side: the filled part is what goes in the game video and the dashed outline is what detection found, so a clip can be extended into the footage around it. Click a clip in the list to go back to it. Every change is saved at once, and the tab opens at the first clip not yet kept or discarded, so you can stop anytime. `gamevids triage <game>` lists the same clips with what's been decided.

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
