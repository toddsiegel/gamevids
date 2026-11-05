"""The activity scan: how much moves inside a camera's D-zone over the game.

Each camera's footage is sampled a few times a second at low resolution. A
sample's activity is the fraction of the zone's pixels that changed by more
than `pixel_threshold` grey levels since the previous sample. The median
change is subtracted first, so a whole-frame brightness shift (exposure,
flickering lights) doesn't count as motion.

The curve covers the camera's whole wall-clock span: sample `i` is at
`start + i / rate`, and samples between recordings are NaN.
"""

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from gamevids.frames import gray_frames
from gamevids.grouping import CameraGroup
from gamevids.zones import Polygon, polygon_from_json, polygon_mask, polygon_to_json

Progress = Callable[[float, float], None]  # (seconds of footage scanned, seconds in total)


class ScanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanSettings:
    rate: float = 10.0  # samples per second
    width: int = 320  # frames are scaled to this size before comparing
    height: int = 180
    pixel_threshold: int = 12  # grey levels a pixel must change by to count as moving


DEFAULT_SETTINGS = ScanSettings()


@dataclass
class ActivityCurve:
    start: datetime
    rate: float
    values: np.ndarray  # float32, 0-1; NaN where the camera wasn't recording
    polygon: Polygon
    settings: ScanSettings
    scanned_at: datetime

    def time(self, index: float) -> datetime:
        return self.start + timedelta(seconds=index / self.rate)

    @property
    def end(self) -> datetime:
        return self.time(len(self.values))


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    """Mean over `window` samples centred on each one, skipping NaNs (which stay NaN)."""
    valid = ~np.isnan(values)
    # np.convolve's "same" mode returns the longer input's length, so cap the window.
    kernel = np.ones(max(1, min(window, len(values))))
    sums = np.convolve(np.where(valid, values, 0.0), kernel, mode="same")
    counts = np.convolve(valid.astype(float), kernel, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = sums / counts
    out[~valid] = np.nan
    return out


def frame_activity(previous: np.ndarray, current: np.ndarray, threshold: int) -> float:
    """Fraction of pixels that changed by more than `threshold`, ignoring a uniform shift.

    Both arrays hold the same zone pixels from two samples.
    """
    diff = current.astype(np.int16) - previous.astype(np.int16)
    diff -= np.int16(np.median(diff))
    return np.count_nonzero(np.abs(diff) > threshold) / diff.size


def scan_camera(
    camera: CameraGroup,
    polygon: Polygon,
    settings: ScanSettings = DEFAULT_SETTINGS,
    *,
    hwaccel: bool = True,
    progress: Progress | None = None,
) -> ActivityCurve:
    rate = settings.rate
    mask = polygon_mask(polygon, settings.width, settings.height)
    if not mask.any():
        raise ScanError(f"camera {camera.label}'s zone is too small to cover any pixels")

    span = (camera.end - camera.start).total_seconds()
    values = np.full(math.ceil(span * rate), np.nan, dtype=np.float32)
    total = camera.duration
    done = 0.0
    every = max(1, round(rate))

    for recording in camera.recordings:
        # Chapters in a recording play back-to-back, so their samples run on
        # without a break and the zone is compared across the chapter split.
        index = round((recording.start - camera.start).total_seconds() * rate)
        previous = None
        for chapter in recording.chapters:
            frames = gray_frames(
                chapter.file.path, rate, settings.width, settings.height, hwaccel=hwaccel
            )
            for count, frame in enumerate(frames, start=1):
                pixels = frame[mask]
                if index < len(values):
                    values[index] = (
                        0.0
                        if previous is None
                        else frame_activity(previous, pixels, settings.pixel_threshold)
                    )
                previous = pixels
                index += 1
                if progress and count % every == 0:
                    progress(min(done + count / rate, total), total)
            done += chapter.file.duration
            if progress:
                progress(done, total)

    return ActivityCurve(
        start=camera.start,
        rate=rate,
        values=values,
        polygon=polygon,
        settings=settings,
        scanned_at=datetime.now(UTC),
    )


def save_curve(conn: sqlite3.Connection, camera_id: int, curve: ActivityCurve) -> None:
    columns = {
        "camera_id": camera_id,
        "start": curve.start.astimezone(UTC).isoformat(),
        "rate": curve.rate,
        "samples": curve.values.astype("<f4").tobytes(),
        "polygon": polygon_to_json(curve.polygon),
        "settings": json.dumps(asdict(curve.settings)),
        "scanned_at": curve.scanned_at.astimezone(UTC).isoformat(),
    }
    names = ", ".join(columns)
    placeholders = ", ".join(f":{name}" for name in columns)
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO activity_curve ({names}) VALUES ({placeholders})", columns
        )


def load_curve(conn: sqlite3.Connection, camera_id: int) -> ActivityCurve | None:
    row = conn.execute("SELECT * FROM activity_curve WHERE camera_id = ?", (camera_id,)).fetchone()
    if row is None:
        return None
    return ActivityCurve(
        start=datetime.fromisoformat(row["start"]),
        rate=row["rate"],
        values=np.frombuffer(row["samples"], dtype="<f4").astype(np.float32),
        polygon=polygon_from_json(row["polygon"]),
        settings=ScanSettings(**json.loads(row["settings"])),
        scanned_at=datetime.fromisoformat(row["scanned_at"]),
    )
