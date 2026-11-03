# Game Vids — Spec & Build Plan

A desktop app for turning full-game footage of a hockey goalie into short, watchable game videos, built so labeling, a highlights library, sharing and analysis can be layered on later without reprocessing games.

## 1. Context

**Footage (measured from `~/Videos/Hockey`):**
- Two DJI Osmo Action 5 Pro cameras, mounted low on the glass behind each net, wide lens. The scoreboard is visible in both views.
- 1920×1080 at 119.88 fps, HEVC Main, ~55 Mbps (~25 GB per camera-hour), AAC stereo audio.
- Recordings split into ~42-minute / 17.2 GB chapters: `DJI_<YYYYMMDDhhmmss>_<NNNN>_D.MP4`.
- Each file carries the camera serial in its DJI metadata stream (e.g. `82JANA9MLDSCN32`) and a wall-clock `creation_time` (1-second resolution). Camera timecodes are *not* synced between cameras.
- Keyframes every ~1 s.
- Older games were split by period and re-muxed with ffmpeg/LosslessCut. Those files have lost the serial, and the period is in the filename (`… - 1st Period.mp4`). Several games have LosslessCut projects (`*-proj.llc`) with hand-picked D-zone segments.

**Machine:** Linux, 16 threads, 30 GB RAM, AMD Radeon 860M iGPU. ffmpeg has VA-API, and hardware HEVC decode runs at ~880 fps (~7× real time). Disk is 913 GB with ~330 GB free.

**Users:** a single editor (the parent). The son, coaches, family and friends only watch. Nothing is hard-coded to one goalie, because other families may use the app later.

## 2. First version — workflow

1. **Add a game.** Pick the video files (both cameras, any number of chapters).
   - The app groups the files by camera serial and stitches each camera's chapters into one continuous timeline, ordered by `creation_time` (back-to-back chapters).
   - You enter a free-text **game title**, shown on the title card (e.g. `2026-10-17 vs Lafayette`). The date is prefilled from the file metadata.
2. **Draw the D-zone** polygon on a still frame for each camera. It's prefilled from the previous game (rinks are the same size and the cameras sit in roughly the same spot), so it usually needs only a nudge.
3. **Extract clips.** A background scan computes a motion-activity curve inside each camera's polygon and turns it into candidate clips using **fixed** detection settings (in a config file, not the UI). You can draw the next camera's zone while the first one scans.
4. **Mark period boundaries** on an activity timeline showing both cameras.
   - The app proposes breaks from intermission lulls and you drag to confirm.
   - You pick which camera is behind your son's net in P1; the app alternates ends for P2, P3 and OT (overridable per period).
   - Camera chapter splits are never treated as period breaks.
5. **Triage** each candidate clip. Keyboard-first, mouse as secondary.
   - Clips auto-play at 2× (adjustable). Keys: keep, discard, trim in/out, step frames, undo.
   - Triage progress is saved, so it can be resumed later.
   - **⭐ star** (optional, per clip): marks a clip as a highlight. In this version stars are only saved, for the future highlights library. They don't change the game video.
6. **Assemble** one video per game:
   - A ~3 s title card with the game title.
   - All kept clips from both cameras in **chronological order** (interleaved by wall-clock time), with a short (~0.3 s) dip-to-black between clips.
   - Output: **1080p60 H.264 + AAC MP4**, rink audio kept.

**Saved from day one (so later phases never require reprocessing):** games, camera timelines, D-zone polygons, activity curves, candidate and kept clips (source time ranges and trims), periods and son's-end assignment, stars, and triage state.

**Out of the first version:** sensitivity tuning UI, export size presets, raw-footage archiving (done by hand), per-clip captions, labels, the clip library, and reels.

### Draft triage keys (remappable later)

| Key | Action |
|---|---|
| Space | Play / pause |
| J / K / L | Shuttle back / pause / forward |
| ← / → | Step one frame |
| I / O | Set in / out point |
| Enter | Keep and go to next |
| X | Discard and go to next |
| S | Toggle ⭐ |
| U | Undo |
| [ / ] | Playback speed down / up |

## 3. Architecture

- **`gamevids` (core):** a pure Python package with no Qt imports. It handles probing, camera grouping and chapter stitching, the database, the activity scan, clip detection, period proposals, triage state and export. It also exposes a small CLI (`gamevids …`) so every core feature can be tested without the UI.
- **`gamevids_ui`:** PySide6/Qt desktop app that embeds **libmpv** for playback, giving smooth, frame-accurate seeking on 1080p120 HEVC with VA-API. Stitched camera timelines play through mpv EDL so chapter boundaries are seamless.
- **Storage:** SQLite (`library.db`) in an app data folder (configurable). Source videos stay where they are, and the app never modifies or moves them.
- **Video tooling:** ffmpeg/ffprobe via subprocess. The scan uses VA-API decode at reduced resolution. Export uses VA-API H.264 encode, with libx264 as a fallback.
- **Motion detection:** OpenCV/NumPy frame differencing inside the polygon mask on downscaled frames at a reduced sample rate. The detector is a swappable stage that outputs segments with a score, so a model-based detector can replace it later.
- **Tooling:** `uv` for environment and dependencies, `pytest`, `ruff`.
- **Platform:** Linux first. Qt and mpv keep a Mac/Windows port possible.

### Core data model (first version)

- **Game:** id, title, date, created_at, plus nullable fields for later phases (opponent, home/away, tournament, venue, team, season).
- **Camera:** id, game_id, serial (nullable for legacy files), label (A/B), clock offset.
- **SourceFile:** id, camera_id, path, start wall-clock, duration, fps, order.
- **Zone:** camera_id, polygon (normalized coordinates).
- **ActivityCurve:** camera_id, sample rate, values (stored as a blob).
- **Period:** game_id, number (1, 2, 3, OT…), start wall-clock, son's-end camera_id.
- **Clip:** id, camera_id, source range (detected start/end), trim in/out, score, status (candidate/kept/discarded), starred, triage order.
- **Export:** game_id, path, settings, created_at.

Later phases add Label, Tag, ShotEvent, Reel, Share, etc. as new tables, without changing these.

## 4. Build plan — small, testable steps

Each step ends with something you can run and check yourself. Steps 1–6 are core and CLI only; the UI starts at step 7.

| # | Step | How you test it |
|---|---|---|
| 1 | **Project skeleton.** `uv` project, `gamevids` and `gamevids_ui` packages, `pytest`, `ruff`, a `gamevids --version` CLI. | `uv run gamevids --version`; `uv run pytest` passes. |
| 2 | **Probe and group files.** Read duration, fps, codec, `creation_time` and DJI serial; group files by camera; order and stitch chapters; detect legacy period-split files. | `gamevids probe "<game folder>"` prints cameras, chapters and stitched durations for a DJI game and a legacy game. |
| 3 | **Database and games.** SQLite schema and migrations; create a game from files plus a title, list games, show a game. | `gamevids game add --title "…" <files>`, then `gamevids game list` / `game show`. |
| 4 | **Activity scan.** VA-API decode at low resolution, polygon mask (polygon passed as an argument for now), frame differencing, store the curve, progress output. | `gamevids scan <game>` finishes a full camera in minutes and writes a PNG plot of the activity curve to eyeball. |
| 5 | **Clip detection plus benchmark.** Turn the curve into candidate clips with the fixed settings (threshold, minimum duration, merge gap, padding). Add an eval command that compares the output to existing LosslessCut `.llc` cuts and reports recall and the clip count. | `gamevids detect <game>` lists clips; `gamevids eval <game> <file.llc>` shows how many of your hand cuts were caught. |
| 6 | **Period proposals.** Find intermission lulls on each camera's curve and propose period starts; set son's end for P1 and alternate. | `gamevids periods <game>` prints proposed breaks to check against the video. |
| 7 | **App shell and player.** Qt window, game list, embedded mpv playing a stitched camera timeline. | Open a game, scrub smoothly, play across a chapter boundary with no hiccup. |
| 8 | **Add Game screen.** File picker, the grouping from step 2 shown for confirmation, title entry with the date prefilled, legacy files assigned to a camera/period by hand. | Add a real game entirely from the UI. |
| 9 | **D-zone editor.** Polygon drawing on a still frame per camera, prefilled from the previous game; save it, then start the scan in the background with a progress display. | Draw zones for both cameras and watch both scans complete. |
| 10 | **Period timeline.** Activity curves for both cameras on one timeline, draggable period markers from step 6, son's-end picker. | Confirm the periods for a real game and click a marker to jump the player there. |
| 11 | **Triage screen.** Candidate list plus player, auto-play at 2×, keep/discard/trim/star/undo keys, saved progress. | Triage a full game by keyboard, quit partway, and resume where you left off. |
| 12 | **Assemble and export.** Title card, chronological merge of both cameras, dip-to-black transitions, 1080p60 H.264 + AAC, progress, open the output folder. | Export a game and watch it on a phone or laptop. |
| 13 | **End-to-end polish.** Run one new game and a few old games through the whole flow; adjust the fixed detection defaults using the step 5 eval; fix rough edges. | One new game done start to finish. Check the time against the ~15 min target. |

## 5. Later phases (in order) and decisions already made

1. **Labels:** one event type per clip (hotkeys 1–9: Save/SOG, Goal Against, Goal For, Missed Net, Scramble/Rebound, Puck Handling, Breakaway, Penalty Shot, Other; editable), plus free-form tags with autocomplete and an optional note. Structured game metadata (date, vs/@ opponent, optional tournament, venue, our team, season auto-set Aug–Jul). Each tournament game is still its own game.
2. **Clip library and highlight reels:** clip grid with hover-scrub thumbnails and filters (season, game, opponent, tournament, venue, period, D-zone/O-zone, event type, tags, ⭐); saved filters as smart collections. Reels are saved, editable, re-exportable clip lists; add a clip with `A` or drag. Reels open with an auto title card and give each clip a caption (e.g. `P2 · Save · vs Lafayette`), both togglable, plus a per-reel audio mute. Export presets: Text (720p30, under ~100 MB), Email (25 MB target, 2-pass), Full (1080p60), with a size estimate before encoding. A sensitivity tuning UI: a live activity curve with a draggable threshold and knobs for minimum duration, merge gap and padding.
3. **Sharing:** Cloudflare R2 plus a Worker on a free `workers.dev` subdomain. Watch-only player page, unguessable links, optional per-share password, no public index. Links expire by default, and each can be extended, made indefinite or revoked from the app.
4. **Slow-mo and text:** uses the native 120 fps (0.5× at 60 fps, 0.25× at 30 fps, no interpolation). Each clip picks a replay segment (normal speed, then the marked range replayed slow) or an inline speed ramp. Audio is muted during slow-mo. Adds text overlays and title cards.
5. **Shot charting:** zone overlay on the video, fitted per camera per game by dragging 4 handles onto rink landmarks (with lens-distortion correction). The fitted overlay also replaces the hand-drawn D-zone. To chart a shot, click the shooter at release; that records the shot time, pixel position (training data), zone and rink coordinates.
   - Each clip holds 0..N shot events.
   - Shot fields, all optional: shot type; danger (Low/Med/High); net target (click a net diagram); save type and rebound outcome; situation.
   - Charting happens in a single pass with triage.
6. **Telestration:** freeze-frame (pause N seconds, draw arrows/circles/text/spotlight, resume) and timed static overlays. Edits belong to a clip's slot in a reel and can be copied between reels.
7. **Analytics:** shot maps, save % by zone, and so on.
8. **Machine learning:**
   - Player detection in the zone to cut false positives.
   - Scoreboard reading for goals, period boundaries and the game clock.
   - Synced two-angle views using wall-clock time plus audio or scoreboard alignment.
   - All of it trained and evaluated on the charted data.

## 6. Defaults and assumptions

- Detection settings are fixed in a config file for the first version and tuned with the step 5 eval against existing `.llc` cuts.
- Clips reference the source files, so nothing is extracted to disk until export. When raw footage is later moved by hand, the app will ask for the new location (relink).
- Existing games will eventually be reprocessed from their full-length files, not from the `D Zone` exports.
- Both cameras are scanned and triaged. In the first version, far-end clips you keep appear in the game video in chronological order.
