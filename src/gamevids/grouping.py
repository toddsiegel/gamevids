"""Group probed files into cameras and stitch each camera's chapters.

A camera's files fall into *recordings*: runs of chapters the camera split on
its own, which play back-to-back with no lost frames. When the camera was
stopped and started again (between periods, say), the next file starts a new
recording after a gap.

The camera splits a chapter when the file reaches 16 GiB, and the next
chapter's `creation_time` (1-second resolution) lands 0.5-2.6 s after the
previous chapter's end. Start times alone can't separate that from a
hand-trimmed period file a few seconds short of the next one, so a file
counts as the next chapter only when the previous file hit the size limit
*and* the gap is within `CHAPTER_TOLERANCE`. It is then placed exactly at the
previous chapter's end.
"""

import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from gamevids.probe import MediaInfo

CHAPTER_TOLERANCE = timedelta(seconds=5)
CHAPTER_SPLIT_BYTES = 16 * 2**30
CHAPTER_SPLIT_MARGIN = 0.99  # a file this close to the limit was split by the camera

DJI_NAME = re.compile(r"^DJI_\d{14}_\d{4}_D\.MP4$", re.IGNORECASE)
EXPORT_NAME = re.compile(r"\bD[\s-]*Zone\b", re.IGNORECASE)
PERIOD_NAME = re.compile(r"\b([1-9])(?:st|nd|rd|th)\s+Period\b", re.IGNORECASE)
OT_NAME = re.compile(r"\b(OT|Overtime)\b", re.IGNORECASE)


class FileKind(StrEnum):
    ORIGINAL = "original"  # straight off the camera
    PERIOD = "period"  # a legacy file split by period
    EXPORT = "export"  # a hand-cut D-zone export; never used as a source
    OTHER = "other"  # renamed, but no period in the name


def classify_name(name: str) -> tuple[FileKind, str | None]:
    """Return the file's kind and, for period files, the period ("1", "2", "OT")."""
    if DJI_NAME.match(name):
        return FileKind.ORIGINAL, None
    if EXPORT_NAME.search(name):
        return FileKind.EXPORT, None
    if m := PERIOD_NAME.search(name):
        return FileKind.PERIOD, m.group(1)
    if OT_NAME.search(name):
        return FileKind.PERIOD, "OT"
    return FileKind.OTHER, None


@dataclass(frozen=True)
class Chapter:
    file: MediaInfo
    start: datetime  # wall clock, after stitching
    kind: FileKind
    period: str | None

    @property
    def end(self) -> datetime:
        return self.start + timedelta(seconds=self.file.duration)


@dataclass
class Recording:
    chapters: list[Chapter]

    @property
    def start(self) -> datetime:
        return self.chapters[0].start

    @property
    def end(self) -> datetime:
        return self.chapters[-1].end

    @property
    def duration(self) -> float:
        return sum(c.file.duration for c in self.chapters)


@dataclass
class CameraGroup:
    serial: str
    label: str
    recordings: list[Recording]

    @property
    def chapters(self) -> list[Chapter]:
        return [c for r in self.recordings for c in r.chapters]

    @property
    def start(self) -> datetime:
        return self.recordings[0].start

    @property
    def end(self) -> datetime:
        return self.recordings[-1].end

    @property
    def duration(self) -> float:
        """Seconds of footage, not counting gaps between recordings."""
        return sum(r.duration for r in self.recordings)


@dataclass
class Grouping:
    cameras: list[CameraGroup] = field(default_factory=list)
    unassigned: list[Chapter] = field(default_factory=list)  # no serial or no start time
    exports: list[MediaInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def date(self) -> datetime | None:
        starts = [c.start for c in self.cameras]
        return min(starts) if starts else None


def was_split(info: MediaInfo) -> bool:
    """True when the camera ended this file because it hit the chapter size limit."""
    return info.size >= CHAPTER_SPLIT_BYTES * CHAPTER_SPLIT_MARGIN


def _stitch(files: list[MediaInfo], warnings: list[str]) -> list[Recording]:
    recordings: list[Recording] = []
    for info in sorted(files, key=lambda f: f.creation_time or datetime.min):
        assert info.creation_time is not None
        kind, period = classify_name(info.path.name)
        stated = info.creation_time
        if recordings:
            previous = recordings[-1].chapters[-1]
            previous_end = previous.end
            gap = stated - previous_end
            if was_split(previous.file) and abs(gap) <= CHAPTER_TOLERANCE:
                recordings[-1].chapters.append(Chapter(info, previous_end, kind, period))
                continue
            if gap < timedelta(0):
                warnings.append(
                    f"{info.path.name} starts {-gap.total_seconds():.0f}s before the previous "
                    "file on the same camera ends"
                )
        recordings.append(Recording([Chapter(info, stated, kind, period)]))
    return recordings


def group_files(files: list[MediaInfo]) -> Grouping:
    grouping = Grouping()
    by_serial: dict[str, list[MediaInfo]] = {}
    for info in files:
        kind, period = classify_name(info.path.name)
        if kind is FileKind.EXPORT:
            grouping.exports.append(info)
        elif info.serial and info.creation_time:
            by_serial.setdefault(info.serial, []).append(info)
        else:
            start = info.creation_time or datetime.min
            grouping.unassigned.append(Chapter(info, start, kind, period))

    cameras = [
        CameraGroup(serial, "", _stitch(group, grouping.warnings))
        for serial, group in by_serial.items()
    ]
    cameras.sort(key=lambda c: c.start)
    for label, camera in zip(string.ascii_uppercase, cameras, strict=False):
        camera.label = label
    grouping.cameras = cameras
    return grouping
