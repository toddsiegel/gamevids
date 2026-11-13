"""Scans running in the background, one camera at a time.

A scan decodes a camera's whole footage on a worker thread. Only the
finished curve comes back to the GUI thread, which saves it, finds the
camera's candidate clips, and starts the next scan in the queue. Scans run
one at a time so the first camera finishes as early as it can, and both
cameras don't fight over the video decoder.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

from gamevids.activity import ActivityCurve, ScanError, save_curve, scan_camera
from gamevids.detect import DEFAULT_SETTINGS, DetectError, DetectSettings, detect_after_scan
from gamevids.frames import VideoError
from gamevids.grouping import CameraGroup
from gamevids.zones import Polygon

ScanFunction = Callable[..., ActivityCurve]  # called like `gamevids.activity.scan_camera`


class _Stopped(Exception):
    pass


@dataclass
class ScanJob:
    camera_id: int
    game_id: int
    game_title: str
    camera: CameraGroup
    polygon: Polygon
    done: float = 0.0  # seconds of footage scanned so far
    total: float = 0.0
    started: float | None = None  # time.monotonic() when it began

    @property
    def label(self) -> str:
        return self.camera.label

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def speed(self) -> float | None:
        """Seconds of footage scanned per second, once there's enough to tell."""
        if self.started is None:
            return None
        elapsed = time.monotonic() - self.started
        return self.done / elapsed if elapsed > 1 else None


@dataclass
class ScanResult:
    """How a camera's last scan in this session ended."""

    error: str | None = None
    clips: int | None = None  # candidate clips found; None if triaged clips were kept
    stopped: bool = False


class ScanThread(QThread):
    progress = Signal(float, float)  # seconds scanned, seconds in all

    def __init__(self, job: ScanJob, scan: ScanFunction, parent=None):
        super().__init__(parent)
        self.job = job
        self.scan = scan
        self.curve: ActivityCurve | None = None
        self.error: str | None = None
        self.stopped = False

    def _progress(self, done: float, total: float) -> None:
        if self.isInterruptionRequested():
            raise _Stopped
        self.progress.emit(done, total)

    def run(self) -> None:
        try:
            self.curve = self.scan(self.job.camera, self.job.polygon, progress=self._progress)
        except _Stopped:
            self.stopped = True
        except (ScanError, VideoError, OSError) as e:
            self.error = str(e)


class ScanQueue(QObject):
    """Scans cameras in the order asked, saving each curve and its clips as it finishes."""

    changed = Signal(int)  # camera id whose scan started, moved on, or ended
    finished = Signal(int)  # camera id whose scan ended, however it ended

    def __init__(
        self,
        conn: sqlite3.Connection,
        parent=None,
        *,
        scan: ScanFunction = scan_camera,
        detect_settings: DetectSettings = DEFAULT_SETTINGS,
    ):
        super().__init__(parent)
        self.conn = conn
        self.scan = scan
        self.detect_settings = detect_settings
        self.waiting: list[ScanJob] = []
        self.results: dict[int, ScanResult] = {}
        self._thread: ScanThread | None = None
        self._last_emit = 0.0

    @property
    def running(self) -> ScanJob | None:
        return self._thread.job if self._thread else None

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def job(self, camera_id: int) -> ScanJob | None:
        """The camera's scan if it's running or waiting."""
        running = self.running
        if running and running.camera_id == camera_id:
            return running
        return next((j for j in self.waiting if j.camera_id == camera_id), None)

    def add(self, job: ScanJob) -> None:
        """Scan `job`'s camera, replacing any scan of it that's running or waiting."""
        self.cancel(job.camera_id)
        self.results.pop(job.camera_id, None)
        job.total = job.camera.duration
        self.waiting.append(job)
        self.changed.emit(job.camera_id)
        self._start_next()

    def cancel(self, camera_id: int) -> None:
        before = len(self.waiting)
        self.waiting = [j for j in self.waiting if j.camera_id != camera_id]
        if len(self.waiting) != before:
            self.changed.emit(camera_id)
        if self._thread and self._thread.job.camera_id == camera_id:
            self._stop_thread()
            self.results[camera_id] = ScanResult(stopped=True)
            self.changed.emit(camera_id)
            self.finished.emit(camera_id)
            self._start_next()

    def stop_all(self) -> None:
        """Drop the waiting scans and stop the running one. Nothing partial is saved."""
        self.waiting = []
        self._stop_thread()

    def _stop_thread(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        thread.finished.disconnect(self._thread_finished)
        thread.requestInterruption()
        thread.wait()
        thread.deleteLater()

    def _start_next(self) -> None:
        if self._thread is not None or not self.waiting:
            return
        job = self.waiting.pop(0)
        job.started = time.monotonic()
        thread = ScanThread(job, self.scan, self)
        thread.progress.connect(self._progress)
        thread.finished.connect(self._thread_finished)
        self._thread = thread
        thread.start()
        self.changed.emit(job.camera_id)

    def _progress(self, done: float, total: float) -> None:
        if self._thread is None or self.sender() is not self._thread:
            return  # queued from a scan that has since been stopped
        job = self._thread.job
        job.done, job.total = done, total
        now = time.monotonic()
        if now - self._last_emit >= 0.25 or done >= total:
            self._last_emit = now
            self.changed.emit(job.camera_id)

    def _thread_finished(self) -> None:
        thread = self._thread
        assert thread is not None
        self._thread = None
        thread.deleteLater()
        job = thread.job
        result = ScanResult(error=thread.error, stopped=thread.stopped)
        if thread.curve is not None:
            try:
                save_curve(self.conn, job.camera_id, thread.curve)
                segments = detect_after_scan(
                    self.conn, job.camera_id, thread.curve, self.detect_settings
                )
                result.clips = None if segments is None else len(segments)
            except (sqlite3.Error, DetectError) as e:
                result.error = f"couldn't save the scan: {e}"
        self.results[job.camera_id] = result
        self.changed.emit(job.camera_id)
        self.finished.emit(job.camera_id)
        self._start_next()
