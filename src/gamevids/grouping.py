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

Files with no camera serial, or no creation time, can't be grouped on their
own. `assign_files` places them on a camera by hand, and `estimate_start`
suggests a start for a file that lost its creation time.
"""

import re
import string
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from gamevids.probe import MediaInfo

CHAPTER_TOLERANCE = timedelta(seconds=5)
CHAPTER_SPLIT_BYTES = 16 * 2**30
CHAPTER_SPLIT_MARGIN = 0.99  # a file this close to the limit was split by the camera

DJI_NAME = re.compile(r"^DJI_\d{14}_\d{4}_D\.MP4$", re.IGNORECASE)
EXPORT_NAME = re.compile(r"\bD[\s-]*Zone\b", re.IGNORECASE)
PERIOD_NAME = re.compile(r"\b([1-9])(?:st|nd|rd|th)\s+Period\b", re.IGNORECASE)
OT_NAME = re.compile(r"\b(OT|Overtime)\b", re.IGNORECASE)
TIMECODE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;.,](\d+)$")
DAY = 24 * 3600


class GroupingError(ValueError):
    pass


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
    serial: str | None  # None for a camera made by hand from files that lost theirs
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


def _stitch(chapters: list[Chapter], warnings: list[str]) -> list[Recording]:
    """Join one camera's chapters, each placed at its stated start, into recordings."""
    recordings: list[Recording] = []
    for chapter in sorted(chapters, key=lambda c: c.start):
        if recordings:
            previous = recordings[-1].chapters[-1]
            gap = chapter.start - previous.end
            if was_split(previous.file) and abs(gap) <= CHAPTER_TOLERANCE:
                recordings[-1].chapters.append(replace(chapter, start=previous.end))
                continue
            if gap < timedelta(0):
                warnings.append(
                    f"{chapter.file.path.name} starts {-gap.total_seconds():.0f}s before the "
                    "previous file on the same camera ends"
                )
        recordings.append(Recording([chapter]))
    return recordings


def group_files(files: list[MediaInfo]) -> Grouping:
    grouping = Grouping()
    by_serial: dict[str, list[Chapter]] = {}
    for info in files:
        kind, period = classify_name(info.path.name)
        if kind is FileKind.EXPORT:
            grouping.exports.append(info)
        elif info.serial and info.creation_time:
            by_serial.setdefault(info.serial, []).append(
                Chapter(info, info.creation_time, kind, period)
            )
        else:
            start = info.creation_time or datetime.min
            grouping.unassigned.append(Chapter(info, start, kind, period))

    cameras = [
        CameraGroup(serial, "", _stitch(chapters, grouping.warnings))
        for serial, chapters in by_serial.items()
    ]
    cameras.sort(key=lambda c: c.start)
    for label, camera in zip(string.ascii_uppercase, cameras, strict=False):
        camera.label = label
    grouping.cameras = cameras
    return grouping


@dataclass(frozen=True)
class Assignment:
    """Where to put a file that couldn't be grouped on its own."""

    camera: str  # a camera's label; a new letter makes a camera with no serial
    period: str | None  # "1", "2", "3", "OT", or None
    start: datetime | None = None  # replaces the file's creation time; needed when it has none


def camera_label(text: str) -> str:
    """`text` as a camera label, one letter A-Z."""
    label = text.strip().upper()
    if not (len(label) == 1 and label in string.ascii_uppercase):
        raise GroupingError(f"camera {text!r} isn't a letter A-Z")
    return label


def _stated_start(chapter: Chapter) -> datetime:
    """Where a chapter started before stitching moved it onto the previous chapter's end."""
    return chapter.file.creation_time or chapter.start


def assign_files(grouping: Grouping, assignments: dict[Path, Assignment]) -> Grouping:
    """A new grouping with unassigned files placed on cameras by hand.

    `assignments` is keyed by file path. Unassigned files missing from it stay
    unassigned, and every camera is stitched again, so a placed file can join
    a recording like any other.
    """
    unassigned_paths = {c.file.path for c in grouping.unassigned}
    if stray := [p.name for p in assignments if p not in unassigned_paths]:
        raise GroupingError(f"only files with no camera serial can be placed: {', '.join(stray)}")

    chapters = {
        c.label: [replace(ch, start=_stated_start(ch)) for ch in c.chapters]
        for c in grouping.cameras
    }
    serials = {c.label: c.serial for c in grouping.cameras}
    unassigned = []
    for chapter in grouping.unassigned:
        assignment = assignments.get(chapter.file.path)
        if assignment is None:
            unassigned.append(chapter)
            continue
        label = camera_label(assignment.camera)
        start = assignment.start or chapter.file.creation_time
        if start is None:
            raise GroupingError(f"{chapter.file.path.name} has no start time; give one")
        if start.tzinfo is None:
            raise GroupingError(f"{chapter.file.path.name}: give the start with a time zone")
        chapters.setdefault(label, [])
        serials.setdefault(label, None)
        chapters[label].append(replace(chapter, start=start, period=assignment.period))

    placed = Grouping(unassigned=unassigned, exports=list(grouping.exports))
    for label in sorted(chapters):
        recordings = _stitch(chapters[label], placed.warnings)
        placed.cameras.append(CameraGroup(serials[label], label, recordings))
    return placed


def timecode_seconds(info: MediaInfo) -> float | None:
    """The file's starting timecode as seconds after midnight on the camera's clock."""
    match = TIMECODE.match(info.timecode or "")
    if not match:
        return None
    hours, minutes, seconds, frames = (int(g) for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + frames / max(round(info.fps), 1)


def estimate_start(info: MediaInfo, camera: CameraGroup) -> datetime | None:
    """Guess when a file started from its timecode and the other files on `camera`.

    A camera's timecode runs on its own clock, offset from the wall clock but
    steady through a game, so any file on the camera with both a timecode and
    a creation time gives the offset. Returns None without such a file.
    """
    own = timecode_seconds(info)
    if own is None:
        return None
    guesses = []
    for chapter in camera.chapters:
        theirs = timecode_seconds(chapter.file)
        if theirs is None or chapter.file.creation_time is None:
            continue
        # Timecode wraps at midnight, so take the nearer of the two ways round.
        delta = (own - theirs + DAY / 2) % DAY - DAY / 2
        # Creation times are whole seconds, so the guess can't be any finer.
        guesses.append((abs(delta), chapter.file.creation_time + timedelta(seconds=round(delta))))
    return min(guesses, key=lambda g: g[0])[1] if guesses else None
