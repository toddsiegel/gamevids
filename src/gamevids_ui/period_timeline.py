"""Every camera's activity on one wall-clock timeline, with a marker at each period start.

Each camera gets a lane showing its smoothed activity curve, shaded where it
wasn't recording. Quiet spells (where every zone was quiet at once, the
likeliest breaks) are shaded across all lanes, and a green strip along the
top of a lane marks the periods with our goalie at that camera's end.

Drag a period's marker to move its start; click one to pick that period.
Click or drag anywhere else to seek the player to that moment, on the camera
whose lane you clicked.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from gamevids.activity import ActivityCurve, moving_average
from gamevids.periods import Period, QuietSpell, period_ends

SMOOTHING = 5.0  # seconds of activity averaged before drawing
LABEL_WIDTH = 36  # pixels left of the lanes for camera names
RIGHT = 10
HANDLES = 22  # pixels above the lanes for period names
AXIS = 20  # pixels below the lanes for times
LANE = 64  # height of each camera's lane
LANE_GAP = 6
GRAB = 6  # pixels either side of a marker that pick it up
DRAG_AFTER = 3  # pixels the mouse must move before a press on a marker drags it
MIN_GAP = timedelta(seconds=1)  # closest a start may be dragged to another period's
TICK_STEPS = [60, 120, 300, 600, 900, 1800, 3600]  # seconds between time labels

GOALIE = QColor("#1f9d55")
PLAYHEAD = QColor("#e5484d")


@dataclass
class Lane:
    label: str
    curve: ActivityCurve | None  # None until the camera is scanned
    recording: list[tuple[datetime, datetime]]  # when the camera was recording


class PeriodTimeline(QWidget):
    seek_requested = Signal(object, object, bool)  # camera label or None, moment, exact
    start_dragged = Signal(int, object, bool)  # period number, new start, released
    period_picked = Signal(int)  # period number whose marker was clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.lanes: list[Lane] = []
        self.start: datetime | None = None
        self.end: datetime | None = None
        self.periods: list[Period] = []
        self.goalies: dict[int, str | None] = {}  # period number -> camera label
        self.quiet: list[QuietSpell] = []
        self.selected: int | None = None
        self.playhead: tuple[str, datetime] | None = None
        self._smoothed: list[np.ndarray | None] = []
        self._top = 0.01
        self._columns: tuple[int, list[np.ndarray | None]] | None = None  # width, per lane
        self._press: tuple[int | None, float, str | None] | None = None  # marker, x, lane
        self._drag: tuple[int, datetime] | None = None  # period being dragged and where to

    # -- data --------------------------------------------------------------------

    def set_lanes(self, lanes: list[Lane], footage: tuple[datetime, datetime] | None) -> None:
        self.lanes = lanes
        self.start, self.end = footage or (None, None)
        self._smoothed = []
        for lane in lanes:
            if lane.curve is None:
                self._smoothed.append(None)
                continue
            window = max(1, round(SMOOTHING * lane.curve.rate))
            self._smoothed.append(moving_average(lane.curve.values.astype(np.float64), window))
        peaks = [np.nanpercentile(s, 99.5) for s in self._smoothed if s is not None and _any(s)]
        self._top = max([0.01, *peaks])
        self._columns = None
        self._press = self._drag = None
        self.updateGeometry()
        self.update()

    def set_periods(
        self, periods: list[Period], goalies: dict[int, str | None], selected: int | None
    ) -> None:
        self.periods = periods
        self.goalies = goalies
        self.selected = selected
        self.update()

    def set_quiet(self, spells: list[QuietSpell]) -> None:
        self.quiet = spells
        self.update()

    def set_playhead(self, label: str | None, moment: datetime | None) -> None:
        playhead = (label, moment) if label is not None and moment is not None else None
        if playhead != self.playhead:
            self.playhead = playhead
            self.update()

    # -- geometry ----------------------------------------------------------------

    def sizeHint(self) -> QSize:
        lanes = max(len(self.lanes), 1)
        return QSize(600, HANDLES + lanes * LANE + (lanes - 1) * LANE_GAP + AXIS)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, self.sizeHint().height())

    def _span(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return max((self.end - self.start).total_seconds(), 1.0)

    def _left(self) -> float:
        return float(LABEL_WIDTH)

    def _width(self) -> float:
        return max(self.width() - LABEL_WIDTH - RIGHT, 1)

    def x_for(self, moment: datetime) -> float:
        if self.start is None:
            return self._left()
        offset = (moment - self.start).total_seconds()
        return self._left() + self._width() * offset / self._span()

    def moment_at(self, x: float) -> datetime:
        """The moment under `x`, clamped to the footage."""
        assert self.start is not None
        fraction = min(max((x - self._left()) / self._width(), 0.0), 1.0)
        return self.start + timedelta(seconds=fraction * self._span())

    def lane_rect(self, index: int) -> QRectF:
        top = HANDLES + index * (LANE + LANE_GAP)
        return QRectF(self._left(), top, self._width(), LANE)

    def lane_at(self, y: float) -> str | None:
        for index, lane in enumerate(self.lanes):
            rect = self.lane_rect(index)
            if rect.top() - LANE_GAP / 2 <= y <= rect.bottom() + LANE_GAP / 2:
                return lane.label
        return None

    def _lanes_bottom(self) -> float:
        return self.lane_rect(len(self.lanes) - 1).bottom() if self.lanes else HANDLES

    def marker_at(self, x: float) -> int | None:
        """The period whose marker is within `GRAB` pixels of `x`, nearest first."""
        near = [(abs(self.x_for(p.start) - x), p.number) for p in self.periods]
        near = [(d, n) for d, n in near if d <= GRAB]
        return min(near)[1] if near else None

    def drag_limits(self, number: int) -> tuple[datetime, datetime]:
        """How far period `number`'s start can be dragged: between its neighbours."""
        assert self.start is not None and self.end is not None
        low, high = self.start, self.end - MIN_GAP
        index = next(i for i, p in enumerate(self.periods) if p.number == number)
        if index > 0:
            low = self.periods[index - 1].start + MIN_GAP
        if index + 1 < len(self.periods):
            high = self.periods[index + 1].start - MIN_GAP
        return low, high

    # -- mouse -------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        x, y = event.position().x(), event.position().y()
        marker = self.marker_at(x)
        self._press = (marker, x, self.lane_at(y))
        if marker is None:
            self.seek_requested.emit(self._press[2], self.moment_at(x), False)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._press is None:
            over = self.start is not None and self.marker_at(x) is not None
            self.setCursor(Qt.CursorShape.SizeHorCursor if over else Qt.CursorShape.ArrowCursor)
            return
        marker, pressed_x, lane = self._press
        if marker is None:
            self.seek_requested.emit(lane, self.moment_at(x), False)
        elif self._drag is not None or abs(x - pressed_x) >= DRAG_AFTER:
            self._drag_to(marker, x, released=False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        x = event.position().x()
        marker, _, lane = self._press
        self._press = None
        if marker is None:
            self.seek_requested.emit(lane, self.moment_at(x), True)
        elif self._drag is not None:
            self._drag_to(marker, x, released=True)
            self._drag = None
            self.update()
        else:
            self.period_picked.emit(marker)

    def _drag_to(self, number: int, x: float, *, released: bool) -> None:
        low, high = self.drag_limits(number)
        moment = min(max(self.moment_at(x), low), high)
        self._drag = (number, moment)
        self.update()
        self.start_dragged.emit(number, moment, released)

    # -- painting ----------------------------------------------------------------

    def _shown_periods(self) -> list[Period]:
        """The periods as drawn: with the one being dragged at its new start."""
        if self._drag is None:
            return self.periods
        number, moment = self._drag
        return [Period(p.number, moment) if p.number == number else p for p in self.periods]

    def _lane_columns(self) -> list[np.ndarray | None]:
        """Each lane's busiest smoothed activity in every pixel column, NaN where unrecorded."""
        width = int(self._width())
        if self._columns is not None and self._columns[0] == width:
            return self._columns[1]
        columns: list[np.ndarray | None] = []
        for lane, smoothed in zip(self.lanes, self._smoothed, strict=True):
            if smoothed is None or lane.curve is None or self.start is None:
                columns.append(None)
                continue
            offset = (lane.curve.start - self.start).total_seconds()
            times = offset + np.arange(len(smoothed)) / lane.curve.rate
            index = np.clip((times / self._span() * width).astype(int), 0, width - 1)
            out = np.full(width, np.nan)
            valid = ~np.isnan(smoothed)
            np.fmax.at(out, index[valid], smoothed[valid])
            columns.append(out)
        self._columns = (width, columns)
        return columns

    def resizeEvent(self, event) -> None:
        self._columns = None
        super().resizeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        text = palette.color(QPalette.ColorRole.Text)
        painter.fillRect(self.rect(), palette.color(QPalette.ColorRole.Base))
        if self.start is None or not self.lanes:
            painter.setPen(text)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No footage.")
            return

        periods = self._shown_periods()
        for index, (lane, column) in enumerate(zip(self.lanes, self._lane_columns(), strict=True)):
            self._paint_lane(painter, index, lane, column, periods)
        self._paint_quiet(painter)
        self._paint_axis(painter, text)
        self._paint_markers(painter, periods, text)
        self._paint_playhead(painter)

    def _paint_lane(
        self,
        painter: QPainter,
        index: int,
        lane: Lane,
        column: np.ndarray | None,
        periods: list[Period],
    ) -> None:
        palette = self.palette()
        rect = self.lane_rect(index)
        painter.fillRect(rect, palette.color(QPalette.ColorRole.AlternateBase))

        # Shade where this camera wasn't recording.
        gap = QColor(palette.color(QPalette.ColorRole.Mid))
        gap.setAlpha(110)
        edges = [self.start, *(t for span in lane.recording for t in span), self.end]
        for a, b in zip(edges[::2], edges[1::2], strict=True):
            if b > a:
                left, right = self.x_for(a), self.x_for(b)
                painter.fillRect(QRectF(left, rect.top(), right - left, rect.height()), gap)

        # Our goalie's end: the lane is tinted under the curve, with a strip along its
        # top, for each of their periods.
        assert self.end is not None
        tint = QColor(GOALIE)
        tint.setAlpha(40)
        for period, end in zip(periods, period_ends(periods, self.end), strict=True):
            if self.goalies.get(period.number) == lane.label:
                left, right = self.x_for(period.start), self.x_for(end)
                painter.fillRect(QRectF(left, rect.top(), right - left, rect.height()), tint)
                painter.fillRect(QRectF(left, rect.top(), right - left, 4), GOALIE)

        if column is not None:
            self._paint_curve(painter, rect, column)
        else:
            painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Not scanned yet")

        playing = self.playhead is not None and self.playhead[0] == lane.label
        font = QFont(self.font())
        font.setBold(playing)
        font.setPointSizeF(font.pointSizeF() * 1.2)
        painter.setFont(font)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(
            QRectF(0, rect.top(), LABEL_WIDTH - 8, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            lane.label,
        )
        painter.setFont(self.font())

    def _paint_curve(self, painter: QPainter, rect: QRectF, column: np.ndarray) -> None:
        color = self.palette().color(QPalette.ColorRole.Highlight)
        fill = QColor(color)
        fill.setAlpha(90)
        scale = (rect.height() - 6) / self._top
        path = QPainterPath()
        drawing = False
        for i, value in enumerate(column):
            x = rect.left() + i + 0.5
            if np.isnan(value):
                if drawing:
                    path.lineTo(x - 1, rect.bottom())
                    drawing = False
                continue
            y = rect.bottom() - min(value * scale, rect.height() - 6)
            if not drawing:
                path.moveTo(x, rect.bottom())
                drawing = True
            path.lineTo(x, y)
        if drawing:
            path.lineTo(rect.left() + len(column) - 0.5, rect.bottom())
        painter.setPen(QPen(color, 1))
        painter.setBrush(fill)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_quiet(self, painter: QPainter) -> None:
        shade = QColor(self.palette().color(QPalette.ColorRole.Text))
        shade.setAlpha(22)
        top, bottom = float(HANDLES), self._lanes_bottom()
        for spell in self.quiet:
            left, right = self.x_for(spell.start), self.x_for(spell.end)
            painter.fillRect(QRectF(left, top, max(right - left, 1), bottom - top), shade)

    def _paint_axis(self, painter: QPainter, text: QColor) -> None:
        assert self.start is not None and self.end is not None
        local = self.start.astimezone()
        # Label every step that keeps the labels about 70 px apart, on round clock times.
        per_pixel = self._span() / self._width()
        step = next((s for s in TICK_STEPS if s / per_pixel >= 70), TICK_STEPS[-1])
        since_midnight = local.hour * 3600 + local.minute * 60 + local.second
        first = local + timedelta(seconds=(-since_midnight) % step, microseconds=-local.microsecond)
        y = self._lanes_bottom()
        muted = QColor(text)
        muted.setAlpha(140)
        painter.setPen(muted)
        moment = first
        while moment <= self.end:
            x = self.x_for(moment)
            painter.drawLine(QPointF(x, y), QPointF(x, y + 4))
            painter.drawText(
                QRectF(x - 40, y + 4, 80, AXIS - 4),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                moment.strftime("%H:%M"),
            )
            moment += timedelta(seconds=step)

    def _paint_markers(self, painter: QPainter, periods: list[Period], text: QColor) -> None:
        palette = self.palette()
        bottom = self._lanes_bottom()
        dragging = self._drag[0] if self._drag else None
        for period in periods:
            x = self.x_for(period.start)
            picked = period.number in (self.selected, dragging)
            painter.setPen(QPen(text, 2.5 if picked else 1.5))
            painter.drawLine(QPointF(x, HANDLES - 2), QPointF(x, bottom))
            name = period.name
            if period.number == dragging:
                name += " " + period.start.astimezone().strftime("%H:%M:%S")
            width = painter.fontMetrics().horizontalAdvance(name) + 10
            handle = QRectF(x, 1, width, HANDLES - 4)
            if handle.right() > self.width():
                handle.moveRight(x)  # flip to the left of the line near the right edge
            if picked:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(palette.color(QPalette.ColorRole.Highlight))
                painter.drawRoundedRect(handle, 3, 3)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(palette.color(QPalette.ColorRole.HighlightedText))
            else:
                painter.setPen(text)
            painter.drawText(handle, Qt.AlignmentFlag.AlignCenter, name)

    def _paint_playhead(self, painter: QPainter) -> None:
        if self.playhead is None:
            return
        x = self.x_for(self.playhead[1])
        painter.setPen(QPen(PLAYHEAD, 1.5))
        painter.drawLine(QPointF(x, HANDLES - 2), QPointF(x, self._lanes_bottom()))


def _any(values: np.ndarray) -> bool:
    return bool(np.isfinite(values).any())
