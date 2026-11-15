"""The D-Zones page: draw each camera's D-zone on a still frame, then scan.

A camera with no zone of its own starts from the nearest game's zone (or a
default shape), so it usually needs only a nudge. Each edit is saved as soon as
it's made (a drag once it's let go, a redraw once it's finished), so a zone is
where it was left after switching games or quitting. Scanning starts that
camera's scan in the background and moves on to the next camera, so its zone
can be drawn while the first one scans.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from gamevids.activity import load_curve
from gamevids.detect import load_clips, load_detection
from gamevids.frames import VideoError, still_frame
from gamevids.games import Game
from gamevids.grouping import CameraGroup
from gamevids.report import hms
from gamevids.timeline import camera_timeline
from gamevids.zones import (
    Polygon,
    ZoneError,
    check_polygon,
    load_zone,
    save_zone,
    suggest_zone,
)
from gamevids_ui.scans import ScanJob, ScanQueue
from gamevids_ui.zone_canvas import ZoneCanvas

STILL_WIDTH = 1280
# Where along a camera's footage "Another Frame" looks next, as fractions of it.
STILL_AT = (0.5, 0.3, 0.7, 0.15, 0.85)

HELP = (
    "Drag a corner to move it · double-click an edge to add a corner · right-click a corner "
    "to remove it · Ctrl+Z undo · Ctrl+S scan"
)
DRAWING_HELP = (
    "Click to place each corner · click the first corner, double-click or press Enter to "
    "finish · Backspace removes the last corner · Esc gives up"
)

StillFunction = Callable[[Path, float, int, int], np.ndarray]


class StillThread(QThread):
    """Grabs one frame with ffmpeg. Read `image` or `error` once it finishes."""

    def __init__(
        self,
        camera_id: int,
        path: Path,
        at: float,
        size: tuple[int, int],
        grab: StillFunction,
        parent=None,
    ):
        super().__init__(parent)
        self.camera_id = camera_id
        self.path, self.at, self.size, self.grab = path, at, size, grab
        self.image: QImage | None = None
        self.error: str | None = None

    def run(self) -> None:
        width, height = self.size
        try:
            rgb = np.ascontiguousarray(self.grab(self.path, self.at, width, height))
        except (VideoError, OSError) as e:
            self.error = str(e)
            return
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        self.image = image.copy()  # own the pixels; `rgb` goes away with this thread


@dataclass
class CameraState:
    """What the page knows about one camera of the game."""

    camera: CameraGroup
    camera_id: int
    saved: Polygon | None  # the zone in the library
    edited: Polygon  # what the canvas shows
    source: str  # where `edited` came from, before any edit
    scanned: Polygon | None = None  # the zone of the camera's saved activity curve
    still_index: int = 0
    image: QImage | None = None
    still_error: str | None = None
    missing: list[Path] | None = None

    @property
    def label(self) -> str:
        return self.camera.label


class ZonesPage(QWidget):
    def __init__(
        self,
        conn: sqlite3.Connection,
        scans: ScanQueue,
        parent=None,
        *,
        grab: StillFunction = still_frame,
    ):
        super().__init__(parent)
        self.conn = conn
        self.scans = scans
        self.grab = grab
        self.game: Game | None = None
        self.cameras: list[CameraState] = []
        self.current: CameraState | None = None
        self._stills: dict[int, StillThread] = {}  # camera id -> frame being grabbed
        self._loaded = False

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.currentChanged.connect(self._tab_picked)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        heading = QHBoxLayout()
        heading.addWidget(self.tabs)
        heading.addSpacing(12)
        heading.addWidget(self.source_label, 1)

        self.canvas = ZoneCanvas()
        self.canvas.changed.connect(self._canvas_changed)
        self.help = QLabel(HELP)
        self.help.setWordWrap(True)
        self.help.setEnabled(False)  # greyed out

        self.redraw_button = QPushButton("Redraw")
        self.redraw_button.setToolTip("Clear the zone and place its corners one by one")
        self.redraw_button.clicked.connect(self.canvas.start_drawing)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setToolTip("Go back to the zone last scanned, or the suggested one")
        self.reset_button.clicked.connect(self.reset)
        self.frame_button = QPushButton("Another Frame")
        self.frame_button.setToolTip("Show a frame from elsewhere in the footage")
        self.frame_button.clicked.connect(self.another_frame)
        self.save_button = QPushButton("Scan")
        self.save_button.clicked.connect(self.save_and_scan)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        buttons = QHBoxLayout()
        for button in (self.redraw_button, self.reset_button, self.frame_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            buttons.addWidget(button)
        buttons.addWidget(self.problem, 1)
        buttons.addWidget(self.save_button)

        self.scan_box = QGroupBox("Scans")
        self.scan_grid = QGridLayout(self.scan_box)
        self.scan_rows: dict[int, tuple[QLabel, QProgressBar, QLabel, QPushButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(heading)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.help)
        layout.addLayout(buttons)
        layout.addWidget(self.scan_box)

        QShortcut(QKeySequence.StandardKey.Save, self, self.save_and_scan)
        scans.changed.connect(self._scan_changed)
        self.clear()

    # -- loading -----------------------------------------------------------------

    def clear(self) -> None:
        self.game = None
        self.cameras = []
        self.current = None
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.blockSignals(False)
        self.canvas.set_polygon(())
        self.canvas.set_image(None, "Pick a game on the left.")
        self.source_label.clear()
        self._build_scan_rows()
        self._update_controls()

    def set_game(self, game: Game) -> None:
        """Show `game`. Frames are only grabbed once the page is shown (see `showEvent`)."""
        self.game = game
        self.cameras = []
        self._stills = {}  # frames still coming for the last game are ignored
        for camera in game.grouping.cameras:
            camera_id = game.camera_ids[camera.label]
            saved = load_zone(self.conn, camera_id)
            if saved is not None:
                edited, source = saved, "Saved zone."
            else:
                suggestion = suggest_zone(self.conn, camera_id)
                edited = suggestion.polygon
                if suggestion.game_id is None:
                    source = "A starting shape: drag the corners onto the ice, then scan."
                else:
                    title = self.conn.execute(
                        "SELECT title FROM game WHERE id = ?", (suggestion.game_id,)
                    ).fetchone()["title"]
                    source = f"From {title}, camera {suggestion.label}: check it fits, then scan."
            missing = camera_timeline(camera).missing_files()
            curve = load_curve(self.conn, camera_id)
            self.cameras.append(
                CameraState(
                    camera,
                    camera_id,
                    saved,
                    edited,
                    source,
                    scanned=curve.polygon if curve else None,
                    missing=missing or None,
                )
            )
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for state in self.cameras:
            self.tabs.addTab(f"Camera {state.label}")
        self.tabs.blockSignals(False)
        self._build_scan_rows()
        self._loaded = False
        first = next((s for s in self.cameras if self._to_do(s)), None)
        self.show_camera(self.cameras.index(first) if first else 0)
        if self.isVisible():
            self._load_stills()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_stills()

    def _load_stills(self) -> None:
        if self._loaded or not self.cameras:
            return
        self._loaded = True
        # The camera on screen first, so its frame arrives soonest.
        for state in sorted(self.cameras, key=lambda s: s is not self.current):
            if state.image is None:
                self._grab(state)

    def _grab(self, state: CameraState) -> None:
        if state.missing:
            return
        timeline = camera_timeline(state.camera)
        position = STILL_AT[state.still_index % len(STILL_AT)] * timeline.duration
        segment = timeline.segment_at(position)
        info = next(c.file for c in state.camera.chapters if c.file.path == segment.path)
        size = (STILL_WIDTH, round(STILL_WIDTH * info.height / info.width / 2) * 2)
        thread = StillThread(
            state.camera_id, segment.path, position - segment.position, size, self.grab, self
        )
        thread.finished.connect(self._grabbed)  # a slot of this page, so it runs on the GUI thread
        self._stills[state.camera_id] = thread
        state.still_error = None
        thread.start()
        if state is self.current:
            self._show_image()

    def _grabbed(self) -> None:
        thread = self.sender()
        assert isinstance(thread, StillThread)
        thread.deleteLater()
        if self._stills.get(thread.camera_id) is not thread:
            return  # a newer frame was asked for, or another game picked, meanwhile
        del self._stills[thread.camera_id]
        state = next(s for s in self.cameras if s.camera_id == thread.camera_id)
        state.image = thread.image
        state.still_error = thread.error
        if state is self.current:
            self._show_image()

    def _show_image(self) -> None:
        state = self.current
        if state is None:
            return
        if state.missing:
            names = "\n".join(str(p) for p in state.missing)
            self.canvas.set_image(None, f"Can't find camera {state.label}'s footage:\n{names}")
        elif state.image is not None:
            self.canvas.set_image(state.image)
        elif state.still_error:
            self.canvas.set_image(None, f"Couldn't show a frame:\n{state.still_error}")
        else:
            self.canvas.set_image(None, "Loading a frame…")

    def another_frame(self) -> None:
        state = self.current
        if state is not None and not state.missing:
            state.still_index += 1
            self._grab(state)

    def shutdown(self) -> None:
        self._stills = {}
        for thread in self.findChildren(StillThread):  # including ones no longer wanted
            thread.wait()

    # -- editing -----------------------------------------------------------------

    def show_camera(self, index: int) -> None:
        if not 0 <= index < len(self.cameras):
            return
        if self.current is not None and not self.canvas.drawing:
            self.current.edited = self.canvas.polygon()
        self.current = self.cameras[index]
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(index)
        self.tabs.blockSignals(False)
        self.canvas.set_polygon(self.current.edited)
        self.source_label.setText(self.current.source)
        self._show_image()
        self._update_controls()

    def _tab_picked(self, index: int) -> None:
        self.show_camera(index)

    def _canvas_changed(self) -> None:
        state = self.current
        if state is None:
            return
        if not self.canvas.drawing:
            state.edited = self.canvas.polygon()
            if not self.canvas.dragging:
                self._autosave(state)
        self._update_controls()

    def _autosave(self, state: CameraState) -> None:
        """Save the zone being edited, unless it can't be (`zone_problem` says why)."""
        if state.edited == state.saved:
            return
        try:
            save_zone(self.conn, state.camera_id, state.edited)
        except ZoneError:
            return
        state.saved = state.edited
        state.source = "Saved zone."
        if state is self.current:
            self.source_label.setText(state.source)
        self._show_scan(state)  # whether it's been scanned with this zone

    def reset(self) -> None:
        state = self.current
        if state is None:
            return
        self.canvas.cancel_drawing()
        if state.scanned is not None:
            state.edited = state.scanned
        else:
            state.edited = suggest_zone(self.conn, state.camera_id).polygon
        self.canvas.set_polygon(state.edited)
        self._autosave(state)
        self._update_controls()

    def zone_problem(self) -> str | None:
        if self.canvas.drawing:
            return "Finish drawing the zone first."
        try:
            check_polygon(self.canvas.polygon())
        except ZoneError as e:
            return f"The zone can't be saved: {e}."
        return None

    def save_and_scan(self) -> None:
        state = self.current
        if state is None or self.game is None or not self.save_button.isEnabled():
            return
        state.edited = self.canvas.polygon()
        self._autosave(state)  # a suggested zone taken as it is
        polygon = state.edited
        self.scans.add(
            ScanJob(state.camera_id, self.game.id, self.game.title, state.camera, polygon)
        )
        # On to the next camera that still needs a zone or a scan, to draw it while this one scans.
        rest = self.cameras[self.cameras.index(state) + 1 :] + self.cameras
        following = next((s for s in rest if self._to_do(s)), None)
        if following is not None:
            self.show_camera(self.cameras.index(following))
        else:
            self._update_controls()

    def _update_controls(self) -> None:
        state = self.current
        has_camera = state is not None and not state.missing
        drawing = self.canvas.drawing
        self.help.setText(DRAWING_HELP if drawing else HELP)
        self.redraw_button.setEnabled(has_camera and not drawing)
        self.reset_button.setEnabled(has_camera)
        self.frame_button.setEnabled(has_camera)
        problem = self.zone_problem() if has_camera else None
        ready = has_camera and problem is None
        if ready and state is not None:
            ready = self.canvas.polygon() != state.saved or self._needs_scan(state)
        self.save_button.setEnabled(ready)
        if state is not None and state.missing:
            problem = "The footage is missing, so this camera can't be scanned."
        elif problem is None and state is not None and self.canvas.polygon() != state.saved:
            problem = "Not saved yet."
        self.problem.setText(problem or "")

    # -- scans -------------------------------------------------------------------

    def _to_do(self, state: CameraState) -> bool:
        """True when the camera still needs its zone checked, or a scan with it."""
        return state.saved is None or self._needs_scan(state)

    def _needs_scan(self, state: CameraState) -> bool:
        """True unless the camera is scanning, or was last scanned, with its saved zone."""
        job = self.scans.job(state.camera_id)
        if job is not None:
            return job.polygon != state.saved
        result = self.scans.results.get(state.camera_id)
        if result is not None and (result.error or result.stopped):
            return True
        return state.scanned != state.saved

    def _build_scan_rows(self) -> None:
        while self.scan_grid.count():
            item = self.scan_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.scan_rows = {}
        for row, state in enumerate(self.cameras):
            name = QLabel(f"Camera {state.label}")
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedWidth(240)
            text = QLabel()
            stop = QPushButton("Stop")
            stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            stop.clicked.connect(lambda _=False, c=state.camera_id: self.scans.cancel(c))
            for column, widget in enumerate((name, bar, text, stop)):
                self.scan_grid.addWidget(widget, row, column)
            self.scan_grid.setColumnStretch(2, 1)
            self.scan_rows[state.camera_id] = (name, bar, text, stop)
            self._show_scan(state)
        self.scan_box.setVisible(bool(self.cameras))

    def _show_scan(self, state: CameraState) -> None:
        _, bar, text, stop = self.scan_rows[state.camera_id]
        job = self.scans.job(state.camera_id)
        result = self.scans.results.get(state.camera_id)
        stop.setVisible(job is not None)
        bar.setVisible(job is not None)
        if job is not None:
            bar.setValue(round(job.fraction * 1000))
            if job is self.scans.running:
                speed = f", {job.speed:.1f}× real time" if job.speed else ""
                text.setText(f"Scanning: {hms(job.done)} of {hms(job.total)}{speed}")
            else:
                text.setText("Waiting for the scan before it")
            return
        if result is not None and result.error:
            text.setText(f"Scan failed: {result.error}")
            return
        if result is not None and result.stopped:
            text.setText("Scan stopped; nothing was saved.")
            return
        text.setText(self._scan_summary(state))

    def _scan_summary(self, state: CameraState) -> str:
        curve = load_curve(self.conn, state.camera_id)
        state.scanned = curve.polygon if curve else None
        if curve is None:
            return "Not scanned yet." if state.saved else "Not scanned yet; save a zone to scan."
        when = curve.scanned_at.astimezone().strftime("%Y-%m-%d %H:%M")
        summary = f"Scanned {when}"
        detection = load_detection(self.conn, state.camera_id)
        if detection is None:
            summary += " · no clips found yet"
        else:
            count = len(load_clips(self.conn, state.camera_id))
            summary += f" · {count} clip{'' if count == 1 else 's'}"
            if detection.curve_scanned_at != curve.scanned_at:
                summary += " from an earlier scan, kept because some were triaged"
        if state.saved is not None and curve.polygon != state.saved:
            summary += " · the zone has changed since; scan again"
        return summary

    def _scan_changed(self, camera_id: int) -> None:
        state = next((s for s in self.cameras if s.camera_id == camera_id), None)
        if state is not None:
            self._show_scan(state)
            if state is self.current:
                self._update_controls()
