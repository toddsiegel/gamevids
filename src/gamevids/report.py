"""Plain-text reports for the CLI."""

from collections.abc import Sequence
from datetime import datetime

from gamevids.games import Game, GameSummary
from gamevids.grouping import Chapter, FileKind, Grouping
from gamevids.probe import MediaInfo

HEADERS = ("CAM", "START", "END", "LENGTH", "PERIOD", "NOTE", "FILE")


def hms(seconds: float) -> str:
    whole = round(seconds)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"


def clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")


def _format(info: MediaInfo) -> str:
    fps = float(info.fps)
    audio = f" + {info.audio_codec}" if info.audio_codec else ", no audio"
    return f"{info.width}x{info.height} @ {fps:.2f} fps, {info.video_codec}{audio}"


def _period(chapter: Chapter) -> str:
    if chapter.period is None:
        return ""
    return chapter.period if chapter.period == "OT" else f"P{chapter.period}"


def _row(camera: str, chapter: Chapter, note: str = "", *, timed: bool = True) -> tuple:
    if chapter.kind is FileKind.OTHER:
        note = ", ".join(n for n in (note, "renamed") if n)
    start, end = (clock(chapter.start), clock(chapter.end)) if timed else ("", "")
    length = hms(chapter.file.duration)
    return (camera, start, end, length, _period(chapter), note, chapter.file.path.name)


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
            formats = "; ".join(sorted({_format(c.file) for c in camera.chapters}))
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
