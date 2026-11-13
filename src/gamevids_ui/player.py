"""Plays one camera of a game as a single stitched timeline."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from gamevids.games import Game
from gamevids.report import clock, hms
from gamevids.timeline import Timeline, camera_timeline
from gamevids_ui.mpv_widget import MpvWidget
from gamevids_ui.scrubber import Scrubber

POLL_MS = 33  # how often the controls catch up with mpv
JOIN_LEAD = 5.0  # seconds before a file join that PgUp/PgDn land on

KEYS_HELP = (
    "Space play/pause · ←/→ one frame · Shift+←/→ 5 s · Ctrl+←/→ 1 min · "
    "PgUp/PgDn previous/next file join · C next camera"
)


def precise(seconds: float) -> str:
    """`h:mm:ss.mmm`, to tell frames apart at 120 fps."""
    whole, ms = divmod(round(seconds * 1000), 1000)
    return f"{hms(whole)}.{ms:03d}"


class Player(QWidget):
    """The video, with a seek bar, camera picker and keys under it unless `controls` is False.

    Without its controls a page drives the player itself and binds its own keys.
    """

    # The camera playing and the wall-clock moment on screen, as playback moves.
    clock_changed = Signal(str, object)

    def __init__(self, parent=None, *, controls: bool = True):
        super().__init__(parent)
        self.game: Game | None = None
        self.timelines: dict[str, Timeline] = {}
        self.timeline: Timeline | None = None
        self._shown: tuple[str, datetime] | None = None  # last sent by clock_changed

        self.video = MpvWidget()
        self.mpv = self.video.mpv
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setAutoFillBackground(True)
        screen = QWidget()
        stack = QStackedLayout(screen)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self.message)  # on top; hidden while a camera plays
        stack.addWidget(self.video)

        self.play_button = QPushButton("Play")
        self.play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_button.clicked.connect(self.toggle_pause)
        self.scrubber = Scrubber()
        self.scrubber.seek_requested.connect(self.seek)
        self.time_label = QLabel()
        self.time_label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.time_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.camera_box = QComboBox()
        self.camera_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.camera_box.currentTextChanged.connect(self._camera_picked)
        self.file_label = QLabel()
        keys = QLabel(KEYS_HELP)
        keys.setWordWrap(True)
        keys.setEnabled(False)  # greyed out

        transport = QHBoxLayout()
        transport.addWidget(self.play_button)
        transport.addWidget(self.scrubber, 1)
        transport.addWidget(self.time_label)
        details = QHBoxLayout()
        details.addWidget(QLabel("Camera"))
        details.addWidget(self.camera_box)
        details.addSpacing(12)
        details.addWidget(self.file_label, 1)

        self.controls = QWidget()
        below = QVBoxLayout(self.controls)
        below.setContentsMargins(0, 0, 0, 0)
        below.addLayout(transport)
        below.addLayout(details)
        below.addWidget(keys)
        self.controls.setVisible(controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(screen, 1)
        layout.addWidget(self.controls)

        keymap = [
            ("Space", self.toggle_pause),
            ("Right", self.step_forward),
            ("Left", self.step_back),
            ("Shift+Right", lambda: self.skip(5)),
            ("Shift+Left", lambda: self.skip(-5)),
            ("Ctrl+Right", lambda: self.skip(60)),
            ("Ctrl+Left", lambda: self.skip(-60)),
            ("PgDown", self.next_join),
            ("PgUp", self.previous_join),
            ("C", self.next_camera),
        ]
        for key, action in keymap if controls else []:
            QShortcut(QKeySequence(key), self, action)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(POLL_MS)
        self.clear("Pick a game on the left.")

    # -- loading ---------------------------------------------------------------

    def clear(self, message: str) -> None:
        """Stop playback and show `message` in place of the video."""
        self.video.stop()
        self.timeline = None
        self.scrubber.set_timeline(0, [], [])
        self.time_label.clear()
        self.file_label.clear()
        self.message.setText(message)
        self.message.show()

    def set_game(self, game: Game) -> None:
        self.game = game
        self.timelines = {c.label: camera_timeline(c) for c in game.grouping.cameras}
        self.timeline = None  # a new game starts at its beginning
        self.camera_box.blockSignals(True)
        self.camera_box.clear()
        self.camera_box.addItems(list(self.timelines))
        self.camera_box.blockSignals(False)
        if self.timelines:
            self.show_camera(self.camera_box.currentText())
        else:
            self.clear(f"{game.title} has no cameras.")

    def show_camera(self, label: str, moment: datetime | None = None) -> None:
        """Play camera `label` at `moment`, or at the moment the camera before it was at."""
        moment = moment or self.wall_clock()
        timeline = self.timelines[label]
        if missing := timeline.missing_files():
            names = "\n".join(str(p) for p in missing)
            self.clear(f"Can't find camera {label}'s footage:\n{names}")
            return
        self.timeline = timeline
        self.scrubber.set_timeline(
            timeline.duration, timeline.chapter_joins, timeline.recording_joins
        )
        start = timeline.position(moment) if moment else 0.0
        self.scrubber.set_position(start)
        self.message.hide()
        self.video.load(timeline.edl(), start)

    def show_moment(self, moment: datetime, label: str | None = None, exact: bool = True) -> None:
        """Seek to wall-clock `moment`, switching to camera `label` first if it's given.

        A moment where the camera wasn't recording shows the next footage it has.
        """
        if label is not None and label in self.timelines and label != self.label:
            self.camera_box.blockSignals(True)
            self.camera_box.setCurrentText(label)
            self.camera_box.blockSignals(False)
            self.show_camera(label, moment)
        elif self.timeline is not None:
            self.seek(self.timeline.position(moment), exact)

    @property
    def label(self) -> str | None:
        """The camera playing, if any."""
        return self.timeline.label if self.timeline else None

    def _camera_picked(self, label: str) -> None:
        if label and label in self.timelines:
            self.show_camera(label)

    def next_camera(self) -> None:
        count = self.camera_box.count()
        if count > 1:
            self.camera_box.setCurrentIndex((self.camera_box.currentIndex() + 1) % count)

    # -- playback --------------------------------------------------------------

    def position(self) -> float | None:
        """Seconds into the current timeline, or None when nothing is playing."""
        if self.timeline is None:
            return None
        return self.mpv.time_pos

    def wall_clock(self) -> datetime | None:
        position = self.position()
        if position is None or self.timeline is None:
            return None
        return self.timeline.wall_clock(position)

    def pause(self) -> None:
        if self.timeline is not None:
            self.mpv.pause = True

    def step_forward(self) -> None:
        """One frame on, pausing."""
        if self.timeline is not None:
            self.mpv.command_async("frame-step")

    def step_back(self) -> None:
        """One frame back, pausing."""
        if self.timeline is not None:
            self.mpv.command_async("frame-back-step")

    def toggle_pause(self) -> None:
        if self.timeline is not None:
            self.mpv.command_async("cycle", "pause")

    def seek(self, position: float, exact: bool = True) -> None:
        if self.timeline is None:
            return
        if self.mpv.time_pos is None:
            # Still opening, or waiting for the widget to be shown: mpv drops a seek
            # that arrives before the file is open, so open it at `position` instead.
            self.video.load(self.timeline.edl(), position)
            return
        precision = "exact" if exact else "keyframes"
        self.mpv.command_async("seek", f"{position:.3f}", f"absolute+{precision}")

    def skip(self, seconds: float) -> None:
        position = self.position()
        if position is not None and self.timeline is not None:
            self.seek(min(max(position + seconds, 0.0), self.timeline.duration))

    def _joins(self) -> list[float]:
        assert self.timeline is not None
        return sorted(self.timeline.chapter_joins + self.timeline.recording_joins)

    def next_join(self) -> None:
        """Jump to just before the next file join, to watch playback cross it."""
        position = self.position()
        if position is None:
            return
        later = [j for j in self._joins() if j - JOIN_LEAD > position + 0.5]
        if later:
            self.seek(later[0] - JOIN_LEAD)

    def previous_join(self) -> None:
        position = self.position()
        if position is None:
            return
        earlier = [j for j in self._joins() if j - JOIN_LEAD < position - 0.5]
        if earlier:
            self.seek(earlier[-1] - JOIN_LEAD)

    def _refresh(self) -> None:
        position = self.position()
        if position is None or self.timeline is None:
            self.play_button.setText("Play")
            return
        self.play_button.setText("Play" if self.mpv.pause else "Pause")
        self.scrubber.set_position(position)
        moment = self.timeline.wall_clock(position)
        if self._shown != (self.timeline.label, moment):
            self._shown = (self.timeline.label, moment)
            self.clock_changed.emit(self.timeline.label, moment)
        self.time_label.setText(
            f"{precise(position)} / {hms(self.timeline.duration)}   {clock(moment)}"
        )
        segment = self.timeline.segment_at(position)
        index = self.timeline.segments.index(segment)
        self.file_label.setText(
            f"{segment.path.name}  (file {index + 1} of {len(self.timeline.segments)})"
        )

    def shutdown(self) -> None:
        self._timer.stop()
        self.video.shutdown()
