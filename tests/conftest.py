import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from gamevids.grouping import CHAPTER_SPLIT_BYTES
from gamevids.probe import MediaInfo

GAME_START = datetime(2026, 10, 24, 17, 53, 46, tzinfo=UTC)
FULL_CHAPTER = CHAPTER_SPLIT_BYTES + 12_000_000


def make_info(
    name: str,
    *,
    start: float | None = 0.0,
    duration: float = 600.0,
    serial: str | None = "CAMA",
    size: int = 1_000_000_000,
) -> MediaInfo:
    """A probed file whose start is `start` seconds after GAME_START (None = unknown)."""
    return MediaInfo(
        path=Path("/footage") / name,
        duration=duration,
        size=size,
        fps=Fraction(120000, 1001),
        width=1920,
        height=1080,
        video_codec="hevc",
        audio_codec="aac",
        creation_time=None if start is None else GAME_START + timedelta(seconds=start),
        encoder="DJI OsmoAction5 Pro",
        serial=serial,
    )


@pytest.fixture
def make_clip(tmp_path):
    """Write a tiny H.264 clip with ffmpeg and return its path."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")

    def make(name: str, *, seconds: float = 1.0, creation_time: str | None = None) -> Path:
        path = tmp_path / name
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30"]
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds)]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
        if creation_time:
            cmd += ["-metadata", f"creation_time={creation_time}"]
        subprocess.run([*cmd, str(path)], check=True)
        return path

    return make
