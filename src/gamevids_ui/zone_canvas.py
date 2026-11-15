"""A still frame with the D-zone polygon drawn over it, for editing by mouse.

- Drag a corner to move it. Corners stop at the frame's edges.
- Double-click an edge to add a corner there.
- Right-click a corner, or hover it and press Delete, to remove it.
- `start_drawing()` clears the zone; click to place corners, then click the
  first corner, double-click or press Enter to close it. Esc gives up.
- Ctrl+Z undoes the last change.

Corners are kept as fractions of the frame, as `gamevids.zones` stores them.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from gamevids.zones import Point, Polygon

HANDLE = 6.0  # corner radius in pixels
GRAB = 10.0  # how near the mouse must be to grab a corner or an edge
MARGIN = 12.0  # around the frame, so corners on its edges can be grabbed
ASPECT = 16 / 9  # until a frame is shown
ZONE_COLOR = QColor(255, 214, 0)
SHADE = QColor(0, 0, 0, 140)  # over the part of the frame outside the zone


def _distance_to_segment(p: QPointF, a: QPointF, b: QPointF) -> tuple[float, float]:
    """Distance from `p` to segment `ab`, and how far along `ab` the nearest point is (0-1)."""
    ab = b - a
    length2 = QPointF.dotProduct(ab, ab)
    t = 0.0 if length2 == 0 else min(max(QPointF.dotProduct(p - a, ab) / length2, 0.0), 1.0)
    nearest = a + ab * t
    d = p - nearest
    return (d.x() ** 2 + d.y() ** 2) ** 0.5, t


class ZoneCanvas(QWidget):
    # The polygon was edited, or drawing started or finished. While a corner is
    # dragged this fires on every move, and once more when it's let go.
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)
        self.image: QImage | None = None
        self.message = ""
        self.points: list[Point] = []
        self.drawing = False
        self._undo: list[list[Point]] = []
        self._before_drawing: list[Point] = []
        self._dragging: int | None = None
        self._hover: int | None = None
        self._mouse: QPointF | None = None

    def sizeHint(self) -> QSize:
        return QSize(960, 540)

    # -- state -------------------------------------------------------------------

    def set_image(self, image: QImage | None, message: str = "") -> None:
        """Show `image`, or `message` in its place when there is none."""
        self.image = image
        self.message = message
        self.update()

    def set_polygon(self, points: Polygon) -> None:
        """Show `points` and forget the undo history (a different zone is being edited)."""
        self.points = list(points)
        self.drawing = False
        self._undo = []
        self._dragging = self._hover = None
        self.update()

    def polygon(self) -> Polygon:
        return tuple(self.points)

    @property
    def dragging(self) -> bool:
        return self._dragging is not None

    def start_drawing(self) -> None:
        if self.drawing:
            return
        self._before_drawing = list(self.points)
        self._remember()
        self.points = []
        self.drawing = True
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self._changed()

    def finish_drawing(self) -> bool:
        """Close the polygon being drawn; False when it has too few corners yet."""
        if not self.drawing or len(self.points) < 3:
            return False
        self.drawing = False
        self.unsetCursor()
        self._changed()
        return True

    def cancel_drawing(self) -> None:
        if self.drawing:
            self.drawing = False
            self.points = self._before_drawing
            self._undo.pop()
            self.unsetCursor()
            self._changed()

    def undo(self) -> None:
        if self._undo and not self.drawing:
            self.points = self._undo.pop()
            self._changed()

    def can_undo(self) -> bool:
        return bool(self._undo) and not self.drawing

    def _remember(self) -> None:
        self._undo.append(list(self.points))

    def _changed(self) -> None:
        self.update()
        self.changed.emit()

    # -- geometry ----------------------------------------------------------------

    def frame_rect(self) -> QRectF:
        """Where the frame is drawn: as large as fits in the margin, centred, keeping its shape."""
        aspect = self.image.width() / self.image.height() if self.image else ASPECT
        w, h = max(self.width() - 2 * MARGIN, 1), max(self.height() - 2 * MARGIN, 1)
        if w / h > aspect:
            fw, fh = h * aspect, h
        else:
            fw, fh = w, w / aspect
        return QRectF(MARGIN + (w - fw) / 2, MARGIN + (h - fh) / 2, fw, fh)

    def to_widget(self, point: Point) -> QPointF:
        r = self.frame_rect()
        return QPointF(r.left() + point[0] * r.width(), r.top() + point[1] * r.height())

    def to_frame(self, pos: QPointF) -> Point:
        """The fraction of the frame at widget position `pos`, clamped to the frame."""
        r = self.frame_rect()
        x = min(max((pos.x() - r.left()) / r.width(), 0.0), 1.0)
        y = min(max((pos.y() - r.top()) / r.height(), 0.0), 1.0)
        return (round(x, 4), round(y, 4))

    def corner_at(self, pos: QPointF) -> int | None:
        best, best_distance = None, GRAB
        for i, point in enumerate(self.points):
            d = self.to_widget(point) - pos
            distance = (d.x() ** 2 + d.y() ** 2) ** 0.5
            if distance <= best_distance:
                best, best_distance = i, distance
        return best

    def edge_at(self, pos: QPointF) -> int | None:
        """The index of the corner that starts the edge under `pos`, if any."""
        if len(self.points) < 2:
            return None
        best, best_distance = None, GRAB
        corners = [self.to_widget(p) for p in self.points]
        for i, a in enumerate(corners):
            distance, _ = _distance_to_segment(pos, a, corners[(i + 1) % len(corners)])
            if distance <= best_distance:
                best, best_distance = i, distance
        return best

    # -- mouse and keys ----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self.drawing:
            if event.button() == Qt.MouseButton.LeftButton:
                if len(self.points) >= 3 and self.corner_at(pos) == 0:
                    self.finish_drawing()
                else:
                    self.points.append(self.to_frame(pos))
                    self._changed()
            return
        corner = self.corner_at(pos)
        if corner is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._remember()
            self._dragging = corner
        elif event.button() == Qt.MouseButton.RightButton:
            self.remove_corner(corner)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        self._mouse = pos
        if self._dragging is not None:
            self.points[self._dragging] = self.to_frame(pos)
            self._changed()
            return
        hover = None if self.drawing else self.corner_at(pos)
        if hover != self._hover:
            self._hover = hover
            if hover is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            elif not self.drawing:
                self.unsetCursor()
        if self.drawing or hover is not None:
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging is not None and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = None
            if self._undo and self._undo[-1] == self.points:
                self._undo.pop()  # a click without a move
            else:
                self._changed()  # the drag is over

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.drawing:
            # The double-click's first press already placed a corner here.
            self.finish_drawing()
            return
        pos = event.position()
        if self.corner_at(pos) is not None:
            return
        edge = self.edge_at(pos)
        if edge is not None:
            self._remember()
            self.points.insert(edge + 1, self.to_frame(pos))
            self._changed()

    def leaveEvent(self, event) -> None:
        self._mouse = None
        self.update()

    def remove_corner(self, index: int) -> bool:
        if len(self.points) <= 3:
            return False
        self._remember()
        del self.points[index]
        self._hover = None
        self._changed()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if self.drawing and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_drawing()
        elif self.drawing and key == Qt.Key.Key_Escape:
            self.cancel_drawing()
        elif self.drawing and key == Qt.Key.Key_Backspace and self.points:
            self.points.pop()
            self._changed()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._hover is not None:
            self.remove_corner(self._hover)
        elif event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
        else:
            super().keyPressEvent(event)

    # -- painting ----------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(24, 24, 24))
        frame = self.frame_rect()
        if self.image is not None:
            painter.drawImage(frame, self.image)
        else:
            painter.fillRect(frame, QColor(48, 48, 48))
        if self.message:
            painter.setPen(QColor(230, 230, 230))
            painter.drawText(frame, Qt.AlignmentFlag.AlignCenter, self.message)

        corners = QPolygonF([self.to_widget(p) for p in self.points])
        if not self.drawing and len(self.points) >= 3:
            outside = QPainterPath()
            outside.setFillRule(Qt.FillRule.OddEvenFill)
            outside.addRect(frame)
            outside.addPolygon(corners)
            outside.closeSubpath()
            painter.fillPath(outside, SHADE)

        outline = QPen(QColor(0, 0, 0, 160), 4)
        line = QPen(ZONE_COLOR, 2)
        for pen in (outline, line):
            painter.setPen(pen)
            if self.drawing:
                painter.drawPolyline(corners)
                if self._mouse is not None and self.points:
                    painter.drawLine(corners[-1], self._mouse)
            else:
                painter.drawPolygon(corners)

        for i, corner in enumerate(corners):
            hot = i == self._hover or i == self._dragging or (self.drawing and i == 0)
            painter.setPen(QPen(QColor(0, 0, 0), 1.5))
            painter.setBrush(QBrush(QColor(255, 255, 255) if hot else ZONE_COLOR))
            radius = HANDLE + 2 if hot else HANDLE
            painter.drawEllipse(corner, radius, radius)
