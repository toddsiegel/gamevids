"""Read the facts we need from a video file with ffprobe/ffmpeg."""

import json
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from gamevids.dji import serial_from_packet

VIDEO_SUFFIXES = {".mp4", ".mov"}


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    """What probing a file tells us.

    The library stores these fields, so a new field also needs a migration in
    `db.py` and updates to `_insert_file` and `_chapter` in `games.py`.
    """

    path: Path
    duration: float  # seconds
    size: int  # bytes
    fps: Fraction
    width: int
    height: int
    video_codec: str
    audio_codec: str | None
    creation_time: datetime | None  # UTC, 1-second resolution
    encoder: str | None
    serial: str | None  # DJI camera serial, when the metadata stream survived


@dataclass(frozen=True)
class _Parsed:
    """ffprobe output boiled down, plus where to find the DJI metadata."""

    info: MediaInfo
    djmd_stream: int | None


def parse_ffprobe(data: dict, path: Path) -> _Parsed:
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    tags = fmt.get("tags", {})

    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ProbeError(f"{path}: no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    djmd = next(
        (
            s["index"]
            for s in streams
            if s.get("codec_type") == "data" and s.get("codec_tag_string") == "djmd"
        ),
        None,
    )

    created = tags.get("creation_time") or video.get("tags", {}).get("creation_time")
    info = MediaInfo(
        path=path,
        duration=float(fmt.get("duration") or video["duration"]),
        size=int(fmt.get("size") or 0),
        fps=Fraction(video["r_frame_rate"]),
        width=int(video["width"]),
        height=int(video["height"]),
        video_codec=video["codec_name"],
        audio_codec=audio["codec_name"] if audio else None,
        creation_time=datetime.fromisoformat(created) if created else None,
        encoder=tags.get("encoder"),
        serial=None,
    )
    return _Parsed(info=info, djmd_stream=djmd)


def _run(cmd: list[str], path: Path) -> bytes:
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise ProbeError(f"{cmd[0]} is not installed") from e
    except subprocess.CalledProcessError as e:
        message = e.stderr.decode(errors="replace").strip()
        raise ProbeError(f"{path}: {message}") from e
    return result.stdout


def _first_packet(path: Path, stream: int) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{stream}"]
    cmd += ["-c", "copy", "-frames:d", "1", "-f", "data", "-"]
    return _run(cmd, path)


def probe_file(path: Path) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    parsed = parse_ffprobe(json.loads(_run(cmd, path)), path)
    if parsed.djmd_stream is None:
        return parsed.info
    serial = serial_from_packet(_first_packet(path, parsed.djmd_stream))
    return replace(parsed.info, serial=serial)


def find_videos(paths: list[Path]) -> list[Path]:
    """Expand folders (not recursively) into the video files they contain."""
    found = []
    for path in paths:
        if path.is_dir():
            found += sorted(
                p for p in path.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
            )
        else:
            found.append(path)
    return found
