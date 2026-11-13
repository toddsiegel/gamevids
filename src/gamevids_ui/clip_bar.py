"""The seek bar under the triage player, zoomed to one clip.

It spans the clip plus the footage a trim can reach on each side. The part
between the in and out points (what goes in the game video) is filled, the
range detection found is outlined, and the reach beyond it is dimmed.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

MARGIN = 8  # pixels on each side, so the playhead can reach both ends

Span = tuple[datetime, datetime]


class ClipBar(QWidget):
    """Click or drag to seek, as on the `Scrubber`.

    `seek_requested(moment, exact)` fires while dragging with exact=False,
    and once more with exact=True when the button is released.
    """

    seek_requested = Signal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reach: Span | None = None  # the bar's whole span
        self.detected: Span | None = None
        self.kept: Span | None = None  # in point to out point
        self.playhead: datetime | None = None
        self._dragging = False

    def sizeHint(self) -> QSize:
        return QSize(400, 32)

    def set_clip(self, reach: Span | None, detected: Span | None, kept: Span | None) -> None:
        """Show a clip, or nothing when `reach` is None."""
        self.reach, self.detected, self.kept = reach, detected, kept
        self.update()

    def set_playhead(self, moment: datetime | None) -> None:
        if not self._dragging and moment != self.playhead:
            self.playhead = moment
            self.update()

    def _track(self) -> QRectF:
        return QRectF(MARGIN, self.height() / 2 - 5, max(self.width() - 2 * MARGIN, 1), 10)

    @property
    def _length(self) -> float:
        assert self.reach is not None
        return (self.reach[1] - self.reach[0]).total_seconds()

    def x_for(self, moment: datetime) -> float:
        track = self._track()
        if self.reach is None or self._length <= 0:
            return track.left()
        fraction = (moment - self.reach[0]).total_seconds() / self._length
        return track.left() + track.width() * min(max(fraction, 0.0), 1.0)

    def moment_at(self, x: float) -> datetime:
        assert self.reach is not None
        track = self._track()
        fraction = min(max((x - track.left()) / track.width(), 0.0), 1.0)
        return self.reach[0] + timedelta(seconds=fraction * self._length)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.reach is not None:
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
        moment = self.moment_at(x)
        if exact or moment != self.playhead:
            self.playhead = moment
            self.update()
            self.seek_requested.emit(moment, exact)

    def _span(self, span: Span) -> QRectF:
        track = self._track()
        left, right = self.x_for(span[0]), self.x_for(span[1])
        return QRectF(left, track.top(), max(right - left, 1), track.height())

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        track = self._track()

        painter.setPen(Qt.PenStyle.NoPen)
        # A faint wash of the text colour shows against light and dark themes alike.
        reach = QColor(palette.color(QPalette.ColorRole.Text))
        reach.setAlphaF(0.2)
        painter.setBrush(reach)
        painter.drawRoundedRect(track, 3, 3)
        if self.reach is None:
            return
        if self.kept is not None:
            painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
            painter.drawRect(self._span(self.kept))
        if self.detected is not None:
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._span(self.detected).adjusted(0, -3, 0, 3))
        if self.kept is not None:
            # Brackets at the in and out points.
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Text), 2))
            top, bottom = 2.0, self.height() - 2.0
            for moment, hook in ((self.kept[0], 4), (self.kept[1], -4)):
                x = self.x_for(moment)
                painter.drawLine(QPointF(x, top), QPointF(x, bottom))
                painter.drawLine(QPointF(x, top), QPointF(x + hook, top))
                painter.drawLine(QPointF(x, bottom), QPointF(x + hook, bottom))
        if self.playhead is not None:
            # Filled with the text colour so it shows against the kept span's fill.
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Base), 2))
            painter.setBrush(palette.color(QPalette.ColorRole.Text))
            painter.drawEllipse(QPointF(self.x_for(self.playhead), self.height() / 2), 7, 7)
