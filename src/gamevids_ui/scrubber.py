"""The seek bar under the player, with marks where files join."""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

MARGIN = 8  # pixels on each side, so the playhead can reach both ends


class Scrubber(QWidget):
    """Click or drag to seek.

    `seek_requested(position, exact)` fires while dragging with exact=False,
    so the player can jump to keyframes and keep up, and once more with
    exact=True when the button is released.
    """

    seek_requested = Signal(float, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.duration = 0.0
        self.position = 0.0
        self.chapter_joins: list[float] = []
        self.recording_joins: list[float] = []
        self._dragging = False

    def sizeHint(self) -> QSize:
        return QSize(400, 28)

    def set_timeline(
        self, duration: float, chapter_joins: list[float], recording_joins: list[float]
    ) -> None:
        self.duration = duration
        self.chapter_joins = chapter_joins
        self.recording_joins = recording_joins
        self.position = 0.0
        self.update()

    def set_position(self, position: float) -> None:
        if not self._dragging and position != self.position:
            self.position = position
            self.update()

    def _track(self) -> QRectF:
        return QRectF(MARGIN, self.height() / 2 - 3, max(self.width() - 2 * MARGIN, 1), 6)

    def x_for(self, position: float) -> float:
        track = self._track()
        if self.duration <= 0:
            return track.left()
        return track.left() + track.width() * position / self.duration

    def position_at(self, x: float) -> float:
        track = self._track()
        fraction = min(max((x - track.left()) / track.width(), 0.0), 1.0)
        return fraction * self.duration

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.duration > 0:
            self._dragging = True
            self._scrub(event.position().x(), exact=False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._scrub(event.position().x(), exact=False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._scrub(event.position().x(), exact=True)

    def _scrub(self, x: float, *, exact: bool) -> None:
        position = self.position_at(x)
        if exact or position != self.position:
            self.position = position
            self.update()
            self.seek_requested.emit(position, exact)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        track = self._track()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.color(QPalette.ColorRole.Mid))
        painter.drawRoundedRect(track, 3, 3)
        if self.duration > 0:
            played = QRectF(track.topLeft(), QPointF(self.x_for(self.position), track.bottom()))
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(played, 3, 3)

        # Chapter splits play seamlessly: a thin tick. Recording joins hide a
        # gap where the camera was stopped: a taller, heavier mark.
        top, bottom = 2.0, self.height() - 2.0
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 1))
        for join in self.chapter_joins:
            x = self.x_for(join)
            painter.drawLine(QPointF(x, track.top() - 4), QPointF(x, track.bottom() + 4))
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 3))
        for join in self.recording_joins:
            x = self.x_for(join)
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))

        if self.duration > 0:
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Base), 2))
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            painter.drawEllipse(QPointF(self.x_for(self.position), self.height() / 2), 7, 7)
