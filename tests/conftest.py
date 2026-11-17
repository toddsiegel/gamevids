import gc
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from gamevids.activity import ActivityCurve, ScanSettings
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
    timecode: str | None = None,
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
        timecode=timecode,
    )


PLAY_RATE = 10.0
QUIET, BUSY = 0.002, 0.05

# A 3-period game in seconds from GAME_START: warmup, a pregame lull, then
# 15-minute periods split by 90 s and 100 s breaks, with a 60 s stoppage in P2.
GAME = [
    (0, 240, "warmup"),
    (240, 300, "quiet"),
    (300, 1200, "play"),
    (1200, 1290, "quiet"),
    (1290, 1700, "play"),
    (1700, 1760, "quiet"),
    (1760, 2190, "play"),
    (2190, 2290, "quiet"),
    (2290, 3200, "play"),
]


def make_play_curve(camera: int, script, *, offset: float = 0.0) -> ActivityCurve:
    """Camera `camera`'s curve for `script`, starting `offset` seconds into it.

    Kinds: "warmup" keeps every zone busy, "play" keeps one zone busy at a
    time (the ends take turns every 10 s), "quiet" keeps every zone quiet,
    and "off" means the camera wasn't recording.
    """
    end = script[-1][1]
    times = offset + np.arange(round((end - offset) * PLAY_RATE)) / PLAY_RATE
    values = np.full(len(times), QUIET, dtype=np.float32)
    for start, stop, kind in script:
        inside = (times >= start) & (times < stop)
        if kind == "warmup":
            values[inside] = BUSY
        elif kind == "play":
            ours = (times // 10) % 2 == camera
            values[inside & ours] = BUSY
        elif kind == "off":
            values[inside] = np.nan
    return ActivityCurve(
        GAME_START + timedelta(seconds=offset),
        PLAY_RATE,
        values,
        ((0, 0), (1, 0), (1, 1), (0, 1)),
        ScanSettings(),
        datetime(2026, 11, 5, tzinfo=UTC),
    )


@pytest.fixture
def denver(monkeypatch):
    """Run in Denver's time zone, where GAME_START is 11:53:46."""
    monkeypatch.setenv("TZ", "America/Denver")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def collect_garbage(request):
    """Free each Qt test's leftover widgets before the next test starts.

    Left to the cyclic collector, a shut-down player can be freed in the
    middle of another player starting mpv, which crashes the run.
    """
    yield
    if "app" in request.fixturenames:
        gc.collect()


@pytest.fixture(autouse=True)
def library(tmp_path, monkeypatch) -> Path:
    """Point every test at its own empty library and no config file, never the real ones."""
    path = tmp_path / "data" / "library.db"
    monkeypatch.setenv("GAMEVIDS_DB", str(path))
    monkeypatch.setenv("GAMEVIDS_CONFIG", str(tmp_path / "config" / "config.toml"))
    return path


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
