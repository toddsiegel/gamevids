"""Clip detection: turn a camera's activity curve into candidate clips.

The curve is smoothed, and every stretch above `threshold` is a burst of
play. Bursts closer together than `merge_gap` join into one, bursts shorter
than `min_duration` are dropped, and what's left is padded so a clip opens
before the play starts and closes after it settles. Padded clips closer than
`merge_gap` are merged too, so no two clips are nearer than that. A clip
never crosses a gap between recordings.

A clip's score is the peak of the smoothed curve inside it, so 0.12 means
12% of the zone was moving at the busiest moment.

The detector is one function from a curve to scored segments, so a
model-based detector can replace it without changing how clips are stored.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime

import numpy as np

from gamevids.activity import ActivityCurve, moving_average


class DetectError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetectSettings:
    threshold: float = 0.04  # fraction of the zone moving, after smoothing
    smoothing: float = 2.0  # seconds averaged before thresholding
    min_duration: float = 2.0  # seconds above threshold for a burst to count
    merge_gap: float = 4.0  # seconds; bursts or clips closer than this become one clip
    pad_before: float = 3.0  # seconds added before each clip
    pad_after: float = 2.0  # seconds added after each clip

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise DetectError(f"{field.name} must be a number, not {value!r}")
            if value < 0:
                raise DetectError(f"{field.name} can't be negative")
        if not 0 < self.threshold < 1:
            raise DetectError("threshold must be between 0 and 1 (a fraction of the zone)")


DEFAULT_SETTINGS = DetectSettings()


@dataclass(frozen=True)
class Segment:
    """What a detector finds: a stretch of one camera's wall clock and how busy it was."""

    start: datetime
    end: datetime
    score: float

    @property
    def duration(self) -> float:
        return (self.end - self.start).total_seconds()


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """`(start, stop)` index pairs of each run of True values."""
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


Span = tuple[int, int, float]  # start index, stop index (exclusive), peak


def _merge(spans: list[Span], gap: float) -> list[Span]:
    """Join time-ordered spans that are at most `gap` samples apart."""
    merged: list[Span] = []
    for start, stop, peak in spans:
        if merged and start - merged[-1][1] <= gap:
            last = merged.pop()
            merged.append((last[0], max(last[1], stop), max(last[2], peak)))
        else:
            merged.append((start, stop, peak))
    return merged


def _detect_span(smoothed: np.ndarray, rate: float, settings: DetectSettings) -> list[Span]:
    """Clips in one recording's stretch of the smoothed curve."""
    gap = settings.merge_gap * rate
    bursts = _merge(
        [(a, b, float(smoothed[a:b].max())) for a, b in _runs(smoothed > settings.threshold)],
        gap,
    )
    before, after = round(settings.pad_before * rate), round(settings.pad_after * rate)
    padded = [
        (max(0, a - before), min(len(smoothed), b + after), peak)
        for a, b, peak in bursts
        if b - a >= settings.min_duration * rate
    ]
    # Padding can bring clips close again, so merge once more: no two clips
    # end up nearer than the merge gap.
    return _merge(padded, gap)


def detect_clips(
    curve: ActivityCurve, settings: DetectSettings = DEFAULT_SETTINGS
) -> list[Segment]:
    """Candidate clips from one camera's curve, in time order."""
    rate = curve.rate
    window = max(1, round(settings.smoothing * rate))
    smoothed = moving_average(curve.values.astype(np.float64), window)
    segments = []
    # Each run of real samples is one recording; NaN marks the gaps between them.
    for span_start, span_stop in _runs(~np.isnan(curve.values)):
        for start, stop, peak in _detect_span(smoothed[span_start:span_stop], rate, settings):
            segments.append(
                Segment(curve.time(span_start + start), curve.time(span_start + stop), peak)
            )
    return segments


@dataclass(frozen=True)
class Detection:
    """How a camera's clips were found."""

    settings: DetectSettings
    curve_scanned_at: datetime  # the scan the clips came from
    detected_at: datetime


@dataclass(frozen=True)
class Clip:
    id: int
    camera_id: int
    start: datetime  # as detected
    end: datetime
    trim_in: datetime | None  # None keeps the detected start
    trim_out: datetime | None
    score: float
    status: str  # candidate, kept or discarded
    starred: bool

    @property
    def duration(self) -> float:
        return ((self.trim_out or self.end) - (self.trim_in or self.start)).total_seconds()

    @property
    def triaged(self) -> bool:
        return (
            self.status != "candidate"
            or self.starred
            or self.trim_in is not None
            or self.trim_out is not None
        )


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _time(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def save_clips(
    conn: sqlite3.Connection,
    camera_id: int,
    segments: list[Segment],
    settings: DetectSettings,
    curve: ActivityCurve,
    *,
    replace_triaged: bool = False,
) -> None:
    """Replace a camera's clips with `segments`.

    Refuses if any of the camera's clips have been triaged, unless
    `replace_triaged` is set, since that work would be lost.
    """
    if not replace_triaged:
        triaged = sum(c.triaged for c in load_clips(conn, camera_id))
        if triaged:
            raise DetectError(
                f"{triaged} of this camera's clips have been triaged; replacing them loses that"
            )
    with conn:
        conn.execute("DELETE FROM clip WHERE camera_id = ?", (camera_id,))
        conn.executemany(
            "INSERT INTO clip (camera_id, start, end, score) VALUES (?, ?, ?, ?)",
            [(camera_id, _ts(s.start), _ts(s.end), s.score) for s in segments],
        )
        conn.execute(
            "INSERT OR REPLACE INTO detection (camera_id, settings, curve_scanned_at, detected_at)"
            " VALUES (?, ?, ?, ?)",
            (
                camera_id,
                json.dumps(asdict(settings)),
                _ts(curve.scanned_at),
                _ts(datetime.now(UTC)),
            ),
        )


def load_clips(conn: sqlite3.Connection, camera_id: int) -> list[Clip]:
    rows = conn.execute(
        "SELECT * FROM clip WHERE camera_id = ? ORDER BY start, id", (camera_id,)
    ).fetchall()
    return [
        Clip(
            id=r["id"],
            camera_id=r["camera_id"],
            start=datetime.fromisoformat(r["start"]),
            end=datetime.fromisoformat(r["end"]),
            trim_in=_time(r["trim_in"]),
            trim_out=_time(r["trim_out"]),
            score=r["score"],
            status=r["status"],
            starred=bool(r["starred"]),
        )
        for r in rows
    ]


def load_detection(conn: sqlite3.Connection, camera_id: int) -> Detection | None:
    row = conn.execute("SELECT * FROM detection WHERE camera_id = ?", (camera_id,)).fetchone()
    if row is None:
        return None
    return Detection(
        settings=DetectSettings(**json.loads(row["settings"])),
        curve_scanned_at=datetime.fromisoformat(row["curve_scanned_at"]),
        detected_at=datetime.fromisoformat(row["detected_at"]),
    )
