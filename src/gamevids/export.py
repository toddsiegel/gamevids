"""Assemble a game's video: a title card, then every kept clip in wall-clock order.

Kept clips from every camera are interleaved by when they happened, so the
video follows our goalie from end to end as the periods change. Each clip
fades out to black and the next fades in, a short dip that marks the cut.
Two kept clips on the same camera that overlap (trimming can stretch a clip
into its neighbour) play as one shot, so no moment is shown twice.

The video is built in steps that each run one ffmpeg: the title card and
every shot are encoded to their own file with identical settings, then the
files are joined without re-encoding. Each step reports progress, the work
happens in a hidden folder beside the output, and the output only replaces
an earlier video once the whole export has succeeded.

Decoding uses VA-API when it can. Encoding uses libx264, which on our
footage is both faster than VA-API's H.264 encoder and makes files half
the size at the same quality.
"""

import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

from gamevids.detect import Clip, load_clips
from gamevids.frames import VideoError
from gamevids.games import Game

SUFFIX = " - DZone"  # names this kind of export, so other kinds for a game won't clash

Progress = Callable[[float, float], None]  # seconds of video made, seconds in all


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportSettings:
    width: int = 1920
    height: int = 1080
    fps: Fraction = Fraction(60000, 1001)  # every other frame of 119.88 fps footage
    crf: int = 20  # libx264 quality; lower is better and bigger
    preset: str = "veryfast"
    audio_bitrate: str = "160k"
    title_seconds: float = 3.0
    fade: float = 0.15  # seconds faded each side of a cut; the dip to black is twice this

    def as_json(self) -> str:
        values = asdict(self)
        values["fps"] = str(self.fps)
        return json.dumps(values)


DEFAULT_SETTINGS = ExportSettings()


def default_dir() -> Path:
    """Where game videos go unless told otherwise: `~/Videos/Game Vids`."""
    return Path.home() / "Videos" / "Game Vids"


def file_name(title: str) -> str:
    """`<title> - DZone.mp4`, with characters a file name can't hold replaced."""
    safe = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "-", title).strip(" .") or "game"
    return f"{safe}{SUFFIX}.mp4"


def default_path(game: Game) -> Path:
    return default_dir() / file_name(game.title)


# -- planning -------------------------------------------------------------------


@dataclass(frozen=True)
class Piece:
    """Part of a shot that comes from one file."""

    path: Path
    offset: float  # seconds into the file
    duration: float
    audio: bool  # the file has an audio track


@dataclass(frozen=True)
class Shot:
    """One continuous stretch of one camera's footage in the video."""

    camera: str  # label
    start: datetime  # wall clock
    end: datetime
    clip_ids: tuple[int, ...]
    pieces: tuple[Piece, ...] = field(repr=False)

    @property
    def duration(self) -> float:
        return sum(p.duration for p in self.pieces)


def kept_clips(conn: sqlite3.Connection, game: Game) -> list[Clip]:
    """Every kept clip in the game, from every camera."""
    return [
        clip
        for camera in game.grouping.cameras
        for clip in load_clips(conn, game.camera_ids[camera.label])
        if clip.status == "kept"
    ]


def _pieces(game: Game, label: str, start: datetime, end: datetime) -> tuple[Piece, ...]:
    pieces = []
    for chapter in game.camera(label).chapters:
        low, high = max(start, chapter.start), min(end, chapter.end)
        if high > low:
            pieces.append(
                Piece(
                    chapter.file.path,
                    (low - chapter.start).total_seconds(),
                    (high - low).total_seconds(),
                    chapter.file.audio_codec is not None,
                )
            )
    return tuple(pieces)


def plan_shots(game: Game, clips: list[Clip]) -> list[Shot]:
    """The kept clips among `clips` as shots, in wall-clock order across cameras."""
    labels = {camera_id: label for label, camera_id in game.camera_ids.items()}
    kept = sorted(
        (c for c in clips if c.status == "kept"), key=lambda c: (c.in_point, c.camera_id, c.id)
    )
    spans: list[tuple[str, datetime, datetime, tuple[int, ...]]] = []
    for clip in kept:
        label = labels[clip.camera_id]
        if spans and spans[-1][0] == label and clip.in_point <= spans[-1][2]:
            _, start, end, ids = spans.pop()
            spans.append((label, start, max(end, clip.out_point), (*ids, clip.id)))
        else:
            spans.append((label, clip.in_point, clip.out_point, (clip.id,)))
    shots = []
    for label, start, end, ids in spans:
        pieces = _pieces(game, label, start, end)
        if not pieces:
            raise ExportError(f"camera {label} has no footage for clip {ids[0]}")
        shots.append(Shot(label, start, end, ids, pieces))
    return shots


def video_length(shots: list[Shot], settings: ExportSettings = DEFAULT_SETTINGS) -> float:
    """Seconds the video will run: the title card and every shot."""
    return settings.title_seconds + sum(s.duration for s in shots)


# -- encoding -------------------------------------------------------------------


def _encode_args(settings: ExportSettings) -> list[str]:
    """Output settings shared by every part, so the parts join without re-encoding."""
    keyframes = str(round(2 * settings.fps))
    return [
        *("-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf)),
        *("-profile:v", "high", "-pix_fmt", "yuv420p", "-g", keyframes),
        *("-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"),
        *("-color_range", "tv", "-video_track_timescale", str(settings.fps.numerator)),
        *("-c:a", "aac", "-b:a", settings.audio_bitrate, "-ar", "48000", "-ac", "2"),
    ]


def _fades(duration: float, fade: float, kind: str = "") -> str:
    fade = min(fade, duration / 2)
    out = max(duration - fade, 0.0)
    return f"{kind}fade=t=in:d={fade:.3f},{kind}fade=t=out:st={out:.3f}:d={fade:.3f}"


def _frame(settings: ExportSettings, duration: float) -> str:
    """Filters that fit any footage to the output's size and rate, then fade it."""
    w, h = settings.width, settings.height
    return (
        f"fps={settings.fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:-1:-1:black,setsar=1,format=yuv420p,trim=duration={duration:.6f},"
        + _fades(duration, settings.fade)
    )


def _sound(duration: float, settings: ExportSettings) -> str:
    """Filters that make audio exactly `duration` long, stereo at 48 kHz, faded."""
    return (
        "aresample=48000,aformat=channel_layouts=stereo,"
        f"apad=whole_dur={duration:.6f},atrim=duration={duration:.6f},"
        + _fades(duration, settings.fade, "a")
    )


def _run(cmd: list[str], cwd: Path, report: Callable[[float], None] | None = None) -> None:
    """Run ffmpeg in `cwd`, calling `report` with seconds of output made so far.

    `report` may raise to stop ffmpeg; the exception is passed on.
    """
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-progress", "pipe:1", "-nostats", *cmd]
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=errors)
        except FileNotFoundError as e:
            raise VideoError("ffmpeg is not installed") from e
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                key, _, value = line.decode(errors="replace").strip().partition("=")
                if key == "out_time_us" and report and value.lstrip("-").isdigit():
                    report(max(int(value), 0) / 1e6)
            if proc.wait() != 0:
                errors.seek(0)
                message = errors.read().decode(errors="replace").strip()
                raise VideoError(f"ffmpeg failed: {message or proc.returncode}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def title_lines(title: str, settings: ExportSettings = DEFAULT_SETTINGS) -> tuple[list[str], int]:
    """The title card's lines and font size in pixels.

    A title shrinks to fit on one line, down to two thirds of full size, and
    wraps at word breaks beyond that. Widths are estimated, at 0.62 of the
    font size per character, to fill at most 90% of the frame.
    """
    full = round(settings.height / 15)
    smallest = round(full * 2 / 3)
    room = 0.9 * settings.width / 0.62  # characters that fit, times the font size
    title = " ".join(title.split())
    if len(title) * full <= room:
        return [title], full
    if len(title) * smallest <= room:
        return [title], math.floor(room / len(title))
    width = int(room // smallest)
    lines: list[str] = []
    for word in title.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines, smallest


def encode_title(
    title: str,
    output: Path,
    settings: ExportSettings = DEFAULT_SETTINGS,
    report: Callable[[float], None] | None = None,
) -> None:
    """Encode the title card: white text centred on black, with silence."""
    lines, size = title_lines(title, settings)
    seconds = settings.title_seconds
    w, h = settings.width, settings.height
    # One drawtext per line, since drawtext shows a newline as a missing glyph. Each
    # line's text goes in a file beside the output, so no title needs escaping for ffmpeg.
    pitch = round(size * 1.4)
    top = (h - pitch * len(lines)) / 2
    texts = []
    for number, line in enumerate(lines):
        text_file = output.with_name(f"{output.stem}-{number}.txt")
        text_file.write_text(line)
        middle = round(top + pitch * (number + 0.5))
        texts.append(
            f"drawtext=textfile={text_file.name}:font=Sans:fontsize={size}:fontcolor=white:"
            f"x=(w-text_w)/2:y={middle}-ascent/2"
        )
    text = ",".join(texts)
    cmd = [
        *("-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={settings.fps}:d={seconds}"),
        *("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"),
        *("-filter_complex", f"[0:v]{text},{_frame(settings, seconds)}[v];"),
        *("-map", "[v]", "-map", "1:a", "-af", _sound(seconds, settings)),
        *_encode_args(settings),
        output.name,
    ]
    _run(cmd, output.parent, report)


def _shot_command(
    shot: Shot, output: Path, settings: ExportSettings, *, hwaccel: bool
) -> list[str]:
    cmd: list[str] = []
    for piece in shot.pieces:
        if hwaccel:
            cmd += ["-hwaccel", "vaapi"]
        cmd += ["-ss", f"{piece.offset:.6f}", "-t", f"{piece.duration:.6f}"]
        cmd += ["-i", str(piece.path)]
    count = len(shot.pieces)
    audio = all(p.audio for p in shot.pieces)
    if not audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    duration = shot.duration
    if count == 1:
        graph = f"[0:v:0]{_frame(settings, duration)}[v]"
        if audio:
            graph += f";[0:a:0]{_sound(duration, settings)}[a]"
    else:
        streams = "".join(f"[{i}:v:0]" + (f"[{i}:a:0]" if audio else "") for i in range(count))
        joined = f"concat=n={count}:v=1:a={int(audio)}"
        if audio:
            graph = f"{streams}{joined}[cv][ca];[cv]{_frame(settings, duration)}[v]"
            graph += f";[ca]{_sound(duration, settings)}[a]"
        else:
            graph = f"{streams}{joined}[cv];[cv]{_frame(settings, duration)}[v]"
    if not audio:
        graph += f";[{count}:a]{_sound(duration, settings)}[a]"
    return [
        *cmd,
        *("-filter_complex", graph, "-map", "[v]", "-map", "[a]"),
        *_encode_args(settings),
        output.name,
    ]


def encode_shot(
    shot: Shot,
    output: Path,
    settings: ExportSettings = DEFAULT_SETTINGS,
    report: Callable[[float], None] | None = None,
    *,
    hwaccel: bool = True,
) -> None:
    """Encode one shot, decoding with VA-API and falling back to software if that fails."""
    missing = [p.path for p in shot.pieces if not p.path.exists()]
    if missing:
        raise ExportError(f"footage not found: {missing[0]}")
    try:
        _run(_shot_command(shot, output, settings, hwaccel=hwaccel), output.parent, report)
    except VideoError:
        if not hwaccel:
            raise
        _run(_shot_command(shot, output, settings, hwaccel=False), output.parent, report)


def join_parts(parts: list[Path], output: Path, work: Path) -> None:
    """Join encoded parts into `output` without re-encoding, ready to stream."""
    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    cmd = ["-f", "concat", "-i", listing.name, "-map", "0", "-c", "copy"]
    cmd += ["-movflags", "+faststart", str(output.absolute())]
    _run(cmd, work)


# -- the whole export -----------------------------------------------------------


@dataclass(frozen=True)
class ExportResult:
    path: Path
    duration: float  # seconds
    clips: int  # kept clips in the video
    shots: int
    size: int  # bytes


def export_video(
    title: str,
    shots: list[Shot],
    output: Path,
    settings: ExportSettings = DEFAULT_SETTINGS,
    progress: Progress | None = None,
    *,
    hwaccel: bool = True,
) -> ExportResult:
    """Make the video at `output`, replacing any file there only once it's done.

    `progress` is called with seconds of video made and seconds in all; it may
    raise to stop the export, which leaves no trace and passes the exception on.
    """
    if not shots:
        raise ExportError("no kept clips to export")
    output.parent.mkdir(parents=True, exist_ok=True)
    total = video_length(shots, settings)
    done = 0.0

    def report_from(base: float, length: float) -> Callable[[float], None]:
        def report(seconds: float) -> None:
            if progress:
                progress(base + min(seconds, length), total)

        return report

    with tempfile.TemporaryDirectory(prefix=".gamevids-export-", dir=output.parent) as tmp:
        work = Path(tmp)
        if progress:
            progress(0.0, total)
        title_part = work / "part-0000.mp4"
        encode_title(title, title_part, settings, report_from(0.0, settings.title_seconds))
        parts = [title_part]
        done = settings.title_seconds
        for number, shot in enumerate(shots, start=1):
            part = work / f"part-{number:04d}.mp4"
            encode_shot(shot, part, settings, report_from(done, shot.duration), hwaccel=hwaccel)
            parts.append(part)
            done += shot.duration
        joined = work / "video.mp4"
        join_parts(parts, joined, work)
        os.replace(joined, output)
    if progress:
        progress(total, total)
    return ExportResult(
        output,
        total,
        sum(len(s.clip_ids) for s in shots),
        len(shots),
        output.stat().st_size,
    )


# -- the record of exports ------------------------------------------------------


@dataclass(frozen=True)
class Export:
    id: int
    game_id: int
    path: Path
    settings: str  # JSON
    clips: int
    duration: float
    created_at: datetime


def save_export(
    conn: sqlite3.Connection,
    game_id: int,
    result: ExportResult,
    settings: ExportSettings = DEFAULT_SETTINGS,
) -> int:
    with conn:
        export_id = conn.execute(
            "INSERT INTO export (game_id, path, settings, clips, duration, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                game_id,
                str(result.path.resolve()),
                settings.as_json(),
                result.clips,
                result.duration,
                datetime.now(UTC).isoformat(),
            ),
        ).lastrowid
    assert export_id is not None
    return export_id


def load_exports(conn: sqlite3.Connection, game_id: int) -> list[Export]:
    """The game's exports, newest first."""
    rows = conn.execute(
        "SELECT * FROM export WHERE game_id = ? ORDER BY created_at DESC, id DESC", (game_id,)
    ).fetchall()
    return [
        Export(
            r["id"],
            r["game_id"],
            Path(r["path"]),
            r["settings"],
            r["clips"],
            r["duration"],
            datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]
