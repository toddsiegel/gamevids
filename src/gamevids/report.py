"""Plain-text reports for the CLI."""

from collections.abc import Sequence
from datetime import datetime

from gamevids.detect import Clip, DetectSettings
from gamevids.games import Game, GameSummary
from gamevids.grouping import Chapter, FileKind, Grouping
from gamevids.periods import Period, PeriodSettings, Proposal
from gamevids.probe import MediaInfo

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
    footage_end = max(c.end for c in game.grouping.cameras)
    starts = [p.start for p in periods]
    ends = [*starts[1:], footage_end]
    all_clips = [clip for cam_clips in clips.values() for clip in cam_clips]

    rows = []
    for period, end in zip(periods, ends, strict=True):
        if period.moved:
            note = "set by hand"
        elif proposal and (spell := proposal.spell_at(period.start)):
            note = f"quiet {clock(spell.start)}-{clock(spell.end)} ({hms(spell.duration)})"
        elif proposal and period.start == proposal.warmup_end:
            note = "at the end of the warmup"
        elif proposal and period.start == proposal.footage_start:
            note = "at the start of the footage"
        else:
            note = ""
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
