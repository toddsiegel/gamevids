"""A camera's footage as one continuous playback timeline.

The player plays every file of a camera back-to-back through an mpv EDL, so
chapter splits play seamlessly. The gaps between recordings are cut out, so a
timeline position is "seconds of footage before this point", not wall-clock
time. `Timeline` converts between the two.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from gamevids.grouping import CameraGroup


@dataclass(frozen=True)
class Segment:
    path: Path
    position: float  # seconds into the timeline where this file starts
    duration: float
    start: datetime  # wall clock of the file's first frame
    recording: int  # index of the recording the file belongs to

    @property
    def end_position(self) -> float:
        return self.position + self.duration

    @property
    def end(self) -> datetime:
        return self.start + timedelta(seconds=self.duration)


@dataclass(frozen=True)
class Timeline:
    label: str
    segments: tuple[Segment, ...]

    @property
    def duration(self) -> float:
        return self.segments[-1].end_position if self.segments else 0.0

    @property
    def chapter_joins(self) -> list[float]:
        """Positions where the camera split a file and the next chapter carries on."""
        return [
            b.position
            for a, b in zip(self.segments, self.segments[1:], strict=False)
            if a.recording == b.recording
        ]

    @property
    def recording_joins(self) -> list[float]:
        """Positions where the camera was stopped and started again (a gap was cut out)."""
        return [
            b.position
            for a, b in zip(self.segments, self.segments[1:], strict=False)
            if a.recording != b.recording
        ]

    def segment_at(self, position: float) -> Segment:
        """The file playing at `position`, clamped to the timeline."""
        starts = [s.position for s in self.segments]
        index = max(bisect_right(starts, position) - 1, 0)
        return self.segments[index]

    def wall_clock(self, position: float) -> datetime:
        position = min(max(position, 0.0), self.duration)
        segment = self.segment_at(position)
        return segment.start + timedelta(seconds=position - segment.position)

    def position(self, moment: datetime) -> float:
        """Where `moment` plays on the timeline.

        A moment in a gap between recordings maps to the start of the next
        recording; one before the first or after the last file is clamped.
        """
        for segment in self.segments:
            if moment < segment.start:
                return segment.position
            if moment <= segment.end:
                return segment.position + (moment - segment.start).total_seconds()
        return self.duration

    def edl(self) -> str:
        """An `edl://` URL that plays every file back-to-back.

        Paths are length-prefixed (`%bytes%path`), so commas, semicolons and
        spaces in folder names need no escaping.
        """
        entries = []
        for segment in self.segments:
            path = str(segment.path)
            entries.append(f"%{len(path.encode())}%{path}")
        return "edl://" + ";".join(entries)

    def missing_files(self) -> list[Path]:
        return [s.path for s in self.segments if not s.path.exists()]


def camera_timeline(camera: CameraGroup) -> Timeline:
    segments = []
    position = 0.0
    for index, recording in enumerate(camera.recordings):
        for chapter in recording.chapters:
            segments.append(
                Segment(chapter.file.path, position, chapter.file.duration, chapter.start, index)
            )
            position += chapter.file.duration
    return Timeline(camera.label, tuple(segments))
