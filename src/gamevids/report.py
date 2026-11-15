"""Plain-text reports for the CLI."""

from collections.abc import Sequence
from datetime import datetime

from gamevids.detect import Clip, DetectSettings
from gamevids.games import Game, GameContents, GameSummary
from gamevids.grouping import Chapter, FileKind, Grouping
from gamevids.periods import Period, PeriodSettings, Proposal, period_ends
from gamevids.probe import MediaInfo
from gamevids.triage import Triage

HEADERS = ("CAM", "START", "END", "LENGTH", "PERIOD", "NOTE", "FILE")


def hms(seconds: float) -> str:
    whole = round(seconds)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")


def media_format(info: MediaInfo) -> str:
    """e.g. `1920x1080 @ 119.88 fps, hevc + aac`"""
    fps = float(info.fps)
    audio = f" + {info.audio_codec}" if info.audio_codec else ", no audio"
    return f"{info.width}x{info.height} @ {fps:.2f} fps, {info.video_codec}{audio}"


def period_name(period: str | None) -> str:
    """A file's period as shown: `P1`, `P2`, `OT`, or blank."""
    if period is None:
        return ""
    return period if period == "OT" else f"P{period}"


def _row(camera: str, chapter: Chapter, note: str = "", *, timed: bool = True) -> tuple:
    if chapter.kind is FileKind.OTHER:
        note = ", ".join(n for n in (note, "renamed") if n)
    start, end = (clock(chapter.start), clock(chapter.end)) if timed else ("", "")
    length = hms(chapter.file.duration)
    return (camera, start, end, length, period_name(chapter.period), note, chapter.file.path.name)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    return [
        "  ".join(str(cell).ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in [headers, *rows]
    ]


def format_grouping(grouping: Grouping, heading: list[str] | None = None) -> str:
    """One row per file, then a key to the cameras. `heading` replaces the date line."""
    rows = []
    for camera in grouping.cameras:
        for i, recording in enumerate(camera.recordings):
            for j, chapter in enumerate(recording.chapters):
                note = ""
                if i and not j:
                    gap = (recording.start - camera.recordings[i - 1].end).total_seconds()
                    note = f"after {hms(gap)} gap"
                rows.append(_row(camera.label, chapter, note))
    for chapter in grouping.unassigned:
        timed = chapter.file.creation_time is not None
        rows.append(_row("?", chapter, "no serial" if timed else "no start time", timed=timed))
    for info in grouping.exports:
        rows.append(("-", "", "", hms(info.duration), "", "D-zone export, skipped", info.path.name))

    if heading is None:
        heading = [f"Date: {grouping.date.astimezone():%Y-%m-%d}"] if grouping.date else []
    lines = [*heading, ""] if heading else []
    lines += table(HEADERS, rows)

    if grouping.cameras:
        lines += ["", "Cameras:"]
        for camera in grouping.cameras:
            formats = "; ".join(sorted({media_format(c.file) for c in camera.chapters}))
            footage = f"{hms(camera.duration)} of footage"
            lines.append(f"  {camera.label}  {camera.serial}  {footage}  {formats}")
    if grouping.unassigned:
        lines += ["", "Files marked ? have no camera serial; assign them by hand."]
    if grouping.warnings:
        lines += ["", "Warnings:"] + [f"  {w}" for w in grouping.warnings]

    return "\n".join(lines)


def format_game_list(games: list[GameSummary]) -> str:
    if not games:
        return "No games yet. Add one with `gamevids game add`."
    headers = ("ID", "DATE", "TITLE", "CAMERAS", "FILES", "FOOTAGE")
    rows = [
        (str(g.id), g.date.isoformat(), g.title, str(g.cameras), str(g.files), hms(g.footage))
        for g in games
    ]
    return "\n".join(table(headers, rows))


def format_game(game: Game) -> str:
    folders = sorted(
        {str(c.file.path.parent) for cam in game.grouping.cameras for c in cam.chapters}
    )
    heading = [f"Game {game.id}: {game.title}", f"Date: {game.date.isoformat()}"]
    if len(folders) == 1:
        heading.append(f"Folder: {folders[0]}")
    else:
        heading += ["Folders:"] + [f"  {f}" for f in folders]
    return format_grouping(game.grouping, heading)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def removal_items(contents: GameContents) -> list[str]:
    """What removing a game deletes from the library, one line per kind of data."""
    c = contents
    items = [
        f"{_count(c.cameras, 'camera')} and the list of their {_count(c.files, 'file')} "
        f"({hms(c.footage)} of footage)"
    ]
    if c.zones:
        items.append(f"D-zones drawn on {_count(c.zones, 'camera')}")
    if c.scans:
        items.append(f"Scans of {_count(c.scans, 'camera')}")
    if c.clips:
        decided = [
            f"{c.kept} kept" if c.kept else "",
            f"{c.discarded} discarded" if c.discarded else "",
            f"{c.starred} starred" if c.starred else "",
        ]
        triage = ", ".join(d for d in decided if d) or "none triaged yet"
        items.append(f"{_count(c.clips, 'clip')}: {triage}")
    if c.periods:
        items.append(_count(c.periods, "period"))
    if c.exports:
        items.append(f"The record of {_count(c.exports, 'export')}")
    return items


def removal_note(contents: GameContents) -> str:
    """What removing a game leaves alone."""
    if len(contents.folders) == 1:
        note = f"The video files stay in {contents.folders[0]}"
    else:
        note = "The video files stay where they are"
    if contents.exports:
        note += ", and exported game videos stay where they were saved"
    return note + "."


def _seconds(value: float) -> str:
    return f"{value:g} s"


def format_detect_settings(settings: DetectSettings) -> str:
    return (
        f"threshold {settings.threshold * 100:g}%, smoothing {_seconds(settings.smoothing)}, "
        f"min duration {_seconds(settings.min_duration)}, "
        f"merge gap {_seconds(settings.merge_gap)}, "
        f"padding {_seconds(settings.pad_before)} before and {_seconds(settings.pad_after)} after"
    )


def format_clips(game: Game, clips: dict[str, list[Clip]], triage_speed: float = 2.0) -> str:
    """Every clip in time order across cameras, then a total for each camera."""
    ordered = sorted(
        ((label, clip) for label, cam_clips in clips.items() for clip in cam_clips),
        key=lambda pair: (pair[1].start, pair[0]),
    )
    if not ordered:
        return "No clips found."
    headers = ("ID", "CAM", "START", "END", "LENGTH", "PEAK")
    rows = [
        (
            str(clip.id),
            label,
            clock(clip.start),
            clock(clip.end),
            hms(clip.duration),
            f"{clip.score * 100:.1f}%",
        )
        for label, clip in ordered
    ]
    lines = table(headers, rows)

    lines.append("")
    total = 0.0
    for label, cam_clips in clips.items():
        length = sum(c.duration for c in cam_clips)
        total += length
        footage = game.camera(label).duration
        share = f" ({length / footage:.0%})" if footage else ""
        counts = f"{_count(len(cam_clips), 'clip')}, {hms(length)} of {hms(footage)} footage"
        lines.append(f"Camera {label}: {counts}{share}")
    if len(clips) > 1:
        lines.append(f"Total: {_count(len(ordered), 'clip')}, {hms(total)}")
    lines.append(f"About {hms(total / triage_speed)} to watch at {triage_speed:g}x.")
    return "\n".join(lines)


def period_note(period: Period, proposal: Proposal | None) -> str:
    """Where a period's start came from, or blank when the proposal can't say."""
    if period.moved:
        return "set by hand"
    if proposal is None:
        return ""
    if spell := proposal.spell_at(period.start):
        return f"quiet {clock(spell.start)}-{clock(spell.end)} ({hms(spell.duration)})"
    if period.start == proposal.warmup_end:
        return "at the end of the warmup"
    if period.start == proposal.footage_start:
        return "at the start of the footage"
    return ""


def format_periods(
    game: Game,
    periods: list[Period],
    clips: dict[str, list[Clip]],
    proposal: Proposal | None,
) -> str:
    """One row per period with its length, our goalie's camera and clip count.

    `proposal` explains where each start came from and lists the quiet spells
    left unused, which are the likeliest places a missed break hides.
    """
    if not periods:
        return "No periods yet."
    labels = {camera_id: label for label, camera_id in game.camera_ids.items()}
    _, footage_end = game.grouping.footage
    starts = [p.start for p in periods]
    all_clips = [clip for cam_clips in clips.values() for clip in cam_clips]

    rows = []
    for period, end in zip(periods, period_ends(periods, footage_end), strict=True):
        note = period_note(period, proposal)
        goalie = labels.get(period.goalie_camera_id, "-")
        inside = sum(period.start <= c.start < end for c in all_clips)
        count = str(inside) if clips else ""
        length = hms((end - period.start).total_seconds())
        rows.append((period.name, clock(period.start), length, goalie, count, note))
    lines = table(("PERIOD", "START", "LENGTH", "GOALIE", "CLIPS", "NOTE"), rows)

    lines.append("")
    early = sum(c.start < starts[0] for c in all_clips)
    if early:
        lines.append(f"{_count(early, 'clip')} start before P1, in the warmup.")
    lines.append(f"The footage ends at {clock(footage_end)}.")

    if proposal:
        others = sorted(
            (
                s
                for s in proposal.quiet
                if s.end < proposal.footage_end and not any(s.start <= t <= s.end for t in starts)
            ),
            key=lambda s: s.duration,
            reverse=True,
        )[:5]
        if others:
            lines += ["", "Other quiet spells, longest first:"]
            lines += [f"  {clock(s.start)}-{clock(s.end)}  {hms(s.duration)}" for s in others]
    return "\n".join(lines)


def format_period_settings(settings: PeriodSettings) -> str:
    return (
        f"quiet below {settings.quiet_level * 100:g}% for {_seconds(settings.min_quiet)}, "
        f"periods at least {hms(settings.min_period)}, "
        f"warmup above {settings.warmup_level * 100:g}% for {_seconds(settings.min_warmup)}"
    )


def _trim(clip: Clip) -> str:
    """How far trimming moved a clip's edges, e.g. `in +1.5 s, out -2.0 s`."""
    moves = []
    if clip.trim_in is not None:
        moves.append(f"in {(clip.trim_in - clip.start).total_seconds():+.1f} s")
    if clip.trim_out is not None:
        moves.append(f"out {(clip.trim_out - clip.end).total_seconds():+.1f} s")
    return ", ".join(moves)


def triage_summary(triage: Triage) -> str:
    """e.g. `12 of 40 clips triaged: 8 kept (0:02:31, 3 starred), 4 discarded.`"""
    kept = [c for c in triage.clips if c.status == "kept"]
    discarded = sum(c.status == "discarded" for c in triage.clips)
    starred = sum(c.starred for c in kept)
    length = hms(sum(c.duration for c in kept))
    return (
        f"{triage.decided} of {_count(len(triage.clips), 'clip')} triaged: {len(kept)} kept "
        f"({length}, {starred} starred), {discarded} discarded."
    )


def format_triage(game: Game, triage: Triage) -> str:
    """Our goalie's clips in triage order, with what's been decided and what's next."""
    labels = {camera_id: label for label, camera_id in game.camera_ids.items()}
    if not triage.clips:
        return "No clips at our goalie's end."
    rows = [
        (
            str(number),
            str(clip.id),
            period.name,
            labels.get(clip.camera_id, "?"),
            clock(clip.in_point),
            hms(clip.duration),
            clip.status,
            "*" if clip.starred else "",
            _trim(clip),
        )
        for number, (clip, period) in enumerate(zip(triage.clips, triage.periods, strict=True), 1)
    ]
    headers = ("#", "ID", "PERIOD", "CAM", "START", "LENGTH", "STATUS", "STAR", "TRIM")
    lines = table(headers, rows)

    lines += ["", triage_summary(triage)]
    if triage.current is not None:
        lines.append(
            f"Next: #{triage.index + 1} (clip {triage.current.id}) "
            f"at {clock(triage.current.in_point)}."
        )
    return "\n".join(lines)
