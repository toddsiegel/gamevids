"""The Periods page: check where each period starts, and which end our goalie is at.

Once every camera is scanned, the periods are proposed from the quiet spells
between them (as `gamevids periods` does) and saved. Drag a marker on the
timeline to move a start, or play to the puck drop and press M. Each change
is saved at once, and Ctrl+Z undoes it. Picking our goalie's end in P1
alternates ends for the later periods; any later period can be changed on
its own.
"""

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gamevids.activity import ActivityCurve, load_curve
from gamevids.detect import Clip, load_clips
from gamevids.games import Game
from gamevids.periods import (
    DEFAULT_COUNT,
    DEFAULT_SETTINGS,
    Period,
    PeriodError,
    PeriodSettings,
    Proposal,
    load_periods,
    move_start,
    period_ends,
    period_name,
    periods_from_proposal,
    propose_periods,
    remove_last,
    save_periods,
    set_goalie,
    set_goalie_for,
)
from gamevids.report import clock, hms, period_note
from gamevids_ui.period_timeline import Lane, PeriodTimeline
from gamevids_ui.player import Player
from gamevids_ui.scans import ScanQueue

HELP = (
    "Drag a marker to move a period's start · click a marker or row to jump there · click a "
    "lane to watch that camera · M moves the picked start to the playhead · [ / ] previous/"
    "next period · Ctrl+Z undo"
)
COLUMNS = ("Period", "Start", "Length", "Clips", "Our goalie", "Note")
TABLE_ROWS = 6  # periods shown before the table scrolls
NOT_SET = "Not set"  # not chosen yet, or that end has no camera


class PeriodsPage(QWidget):
    def __init__(
        self,
        conn: sqlite3.Connection,
        scans: ScanQueue | None = None,
        parent=None,
        *,
        settings: PeriodSettings = DEFAULT_SETTINGS,
    ):
        super().__init__(parent)
        self.conn = conn
        self.settings = settings
        self.game: Game | None = None
        self.curves: dict[str, ActivityCurve | None] = {}
        self.clips: list[Clip] = []
        self.proposal: Proposal | None = None
        self.periods: list[Period] = []
        self.history: list[list[Period]] = []  # earlier periods, for undo
        self.selected: int | None = None

        self.player = Player()
        self.player.clock_changed.connect(self._clock_changed)
        self.timeline = PeriodTimeline()
        self.timeline.seek_requested.connect(self._seek)
        self.timeline.start_dragged.connect(self._dragged)
        self.timeline.period_picked.connect(self.pick)

        self.propose_button = QPushButton("Propose Again")
        self.propose_button.setToolTip("Propose every start again from the quiet spells")
        self.propose_button.clicked.connect(self.propose_again)
        self.add_button = QPushButton("Add Period")
        self.add_button.setToolTip("Add a period after the last one, at the playhead")
        self.add_button.clicked.connect(self.add_period)
        self.remove_button = QPushButton("Remove Last")
        self.remove_button.clicked.connect(self.remove_last)
        self.here_button = QPushButton("Start Here")
        self.here_button.setToolTip("Move the picked period's start to the playhead (M)")
        self.here_button.clicked.connect(self.start_here)
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(self.undo)
        self.message = QLabel()
        self.message.setWordWrap(True)
        buttons = QHBoxLayout()
        for button in (
            self.here_button,
            self.add_button,
            self.remove_button,
            self.propose_button,
            self.undo_button,
        ):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            buttons.addWidget(button)
        buttons.addWidget(self.message, 1)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.itemSelectionChanged.connect(self._row_picked)
        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        help_label.setEnabled(False)  # greyed out

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.player, 1)  # the video takes whatever height is left
        layout.addWidget(self.timeline)
        layout.addWidget(help_label)
        layout.addLayout(buttons)
        layout.addWidget(self.table)

        for key, action in [
            ("M", self.start_here),
            ("[", lambda: self.pick_next(-1)),
            ("]", lambda: self.pick_next(1)),
            (QKeySequence.StandardKey.Undo, self.undo),
        ]:
            QShortcut(QKeySequence(key), self, action)
        if scans is not None:
            scans.finished.connect(self._scan_finished)
        self.clear()

    # -- loading -----------------------------------------------------------------

    def clear(self, message: str = "Pick a game on the left.") -> None:
        self.game = None
        self.curves = {}
        self.clips = []
        self.proposal = None
        self.periods = []
        self.history = []
        self.selected = None
        self.player.clear(message)
        self.timeline.set_lanes([], None)
        self.timeline.set_quiet([])
        self._show()
        self.message.clear()

    def set_game(self, game: Game) -> None:
        self.game = game
        self.history = []
        self.selected = None
        self.periods = load_periods(self.conn, game.id)
        self.player.set_game(game)
        self._load_scans()
        if not self.periods and self.proposal is not None:
            self._propose(undoable=False)  # a first proposal, not an edit to undo

    def _load_scans(self) -> None:
        """Read each camera's curve and clips, and propose periods if every camera is scanned."""
        assert self.game is not None
        game = self.game
        self.curves = {}
        self.clips = []
        lanes = []
        for camera in game.grouping.cameras:
            camera_id = game.camera_ids[camera.label]
            curve = load_curve(self.conn, camera_id)
            self.curves[camera.label] = curve
            self.clips += load_clips(self.conn, camera_id)
            recording = [(r.start, r.end) for r in camera.recordings]
            lanes.append(Lane(camera.label, curve, recording))
        footage = game.grouping.footage if game.grouping.cameras else None
        self.timeline.set_lanes(lanes, footage)

        unscanned = [label for label, curve in self.curves.items() if curve is None]
        self.proposal = None
        if game.grouping.cameras and not unscanned:
            curves = [c for c in self.curves.values() if c is not None]
            count = len(self.periods) or DEFAULT_COUNT
            self.proposal = propose_periods(curves, count, self.settings)
            self.message.clear()
        elif unscanned:
            names = " and ".join(unscanned)
            self.message.setText(
                f"Camera {names} {'hasn’t' if len(unscanned) == 1 else 'haven’t'} been scanned "
                "yet. Scan on the D-Zones tab to have periods proposed, or add them by hand."
            )
        self.timeline.set_quiet(self.proposal.quiet if self.proposal else [])
        self._show()

    def _scan_finished(self, camera_id: int) -> None:
        if self.game is None or camera_id not in self.game.camera_ids.values():
            return
        self._load_scans()
        if not self.periods and self.proposal is not None:
            self._propose(undoable=False)  # a first proposal, not an edit to undo

    # -- changes -----------------------------------------------------------------

    @property
    def camera_ids(self) -> list[int]:
        assert self.game is not None
        return [self.game.camera_ids[c.label] for c in self.game.grouping.cameras]

    def _change(self, periods: list[Period], message: str = "", *, undoable: bool = True) -> None:
        """Save `periods` in place of the game's periods, remembering these for undo."""
        assert self.game is not None
        if periods == self.periods:
            return
        if undoable:
            self.history.append(self.periods)
        self.periods = periods
        save_periods(self.conn, self.game.id, periods)
        if self.selected is not None and self.selected > len(periods):
            self.selected = None
        self.message.setText(message)
        self._show()

    def _propose(self, *, undoable: bool = True) -> None:
        assert self.proposal is not None
        periods = periods_from_proposal(self.proposal, self.periods, self.camera_ids)
        count = len(self.periods) or DEFAULT_COUNT
        message = ""
        if len(periods) < count:
            message = (
                f"Only {len(periods)} of {count} periods fit between quiet spells; "
                "add the rest with Add Period."
            )
        elif any(p.moved for p in self.periods):
            message = "Proposed again. Ctrl+Z puts back the starts you set by hand."
        self._change(periods, message, undoable=undoable)

    def propose_again(self) -> None:
        if self.proposal is not None:
            self._propose()

    def _try(self, change) -> None:
        try:
            self._change(change())
        except PeriodError as e:
            self.message.setText(f"Can't: {e}.")

    def _move(self, number: int, moment: datetime) -> None:
        """Set period `number`'s start to `moment`; one past the last adds a period."""
        footage = self._footage()
        self._try(lambda: move_start(self.periods, number, moment, footage, self.camera_ids))

    def _footage(self) -> tuple[datetime, datetime]:
        assert self.game is not None
        return self.game.grouping.footage

    def add_period(self) -> None:
        """Add the next period at the playhead, or halfway through the last period."""
        if self.game is None or not self.game.grouping.cameras:
            return
        start, end = self._footage()
        after = self.periods[-1].start if self.periods else start - timedelta(seconds=1)
        moment = self.player.wall_clock()
        if moment is None or not after < moment < end:
            moment = max(after, start) + (end - max(after, start)) / 2
        number = len(self.periods) + 1
        self._move(number, moment)
        self.pick(number)

    def remove_last(self) -> None:
        if self.periods:
            self._try(lambda: remove_last(self.periods))

    def start_here(self) -> None:
        """Move the picked period's start to the moment the player is showing."""
        moment = self.player.wall_clock()
        if self.game is None or self.selected is None or moment is None:
            return
        number = self.selected
        self._move(number, moment)

    def set_goalie(self, number: int, camera_id: int | None) -> None:
        """Our goalie's end in period `number`; P1's choice alternates through the rest."""
        if number == 1:
            self._change(set_goalie(self.periods, self.camera_ids, camera_id))
        else:
            self._change(set_goalie_for(self.periods, number, camera_id))

    def undo(self) -> None:
        if self.game is None or not self.history:
            return
        self.periods = self.history.pop()
        save_periods(self.conn, self.game.id, self.periods)
        if self.selected is not None and self.selected > len(self.periods):
            self.selected = None
        self.message.setText("Undone.")
        self._show()

    def _dragged(self, number: int, moment: datetime, released: bool) -> None:
        self.selected = number
        self.player.show_moment(moment, exact=released)
        if released:
            self._move(number, moment)

    # -- picking and seeking -----------------------------------------------------

    def pick(self, number: int) -> None:
        """Pick period `number` and jump the player to its start."""
        period = next((p for p in self.periods if p.number == number), None)
        if period is None:
            return
        self.selected = number
        self.player.show_moment(period.start)
        self._show()

    def pick_next(self, step: int) -> None:
        if not self.periods:
            return
        if self.selected is None:
            number = 1 if step > 0 else len(self.periods)
        else:
            number = min(max(self.selected + step, 1), len(self.periods))
        self.pick(number)

    def _row_picked(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and rows[0].row() + 1 != self.selected:
            self.pick(rows[0].row() + 1)

    def _seek(self, label: str | None, moment: datetime, exact: bool) -> None:
        self.player.show_moment(moment, label, exact)

    def _clock_changed(self, label: str, moment: datetime) -> None:
        self.timeline.set_playhead(label, moment)

    # -- showing -----------------------------------------------------------------

    def _show(self) -> None:
        game = self.game
        labels = {cid: label for label, cid in game.camera_ids.items()} if game else {}
        goalies = {p.number: labels.get(p.goalie_camera_id) for p in self.periods}
        self.timeline.set_periods(self.periods, goalies, self.selected)
        self._fill_table(labels)
        has_footage = game is not None and bool(game.grouping.cameras)
        self.propose_button.setEnabled(self.proposal is not None)
        self.add_button.setEnabled(has_footage)
        self.remove_button.setEnabled(bool(self.periods))
        self.here_button.setEnabled(self.selected is not None)
        self.undo_button.setEnabled(bool(self.history))
        unset = game is not None and self.periods and self.periods[0].goalie_camera_id is None
        if unset and not self.message.text():
            self.message.setText(
                "Pick the camera behind our goalie's net in P1; later periods alternate ends."
            )

    def _fill_table(self, labels: dict[int, str]) -> None:
        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(len(self.periods))
        if self.game is not None and self.periods:
            _, footage_end = self._footage()
            ends = period_ends(self.periods, footage_end)
            for row, (period, end) in enumerate(zip(self.periods, ends, strict=True)):
                clips = sum(period.start <= c.start < end for c in self.clips)
                cells = (
                    period.name,
                    clock(period.start),
                    hms((end - period.start).total_seconds()),
                    str(clips) if self.clips else "",
                    None,
                    period_note(period, self.proposal),
                )
                for column, text in enumerate(cells):
                    if text is not None:
                        self.table.setItem(row, column, QTableWidgetItem(text))
                self.table.setCellWidget(row, 4, self._goalie_box(period, labels))
        self._fit_table()
        if self.selected is not None:
            self.table.selectRow(self.selected - 1)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)

    def _fit_table(self) -> None:
        """Make the table just tall enough for its rows, up to `TABLE_ROWS` of them."""
        rows = min(max(self.table.rowCount(), 1), TABLE_ROWS)
        row_height = self.table.verticalHeader().defaultSectionSize()
        if self.table.rowCount():
            row_height = max(row_height, self.table.rowHeight(0))
        height = self.table.horizontalHeader().sizeHint().height() + rows * row_height
        self.table.setFixedHeight(height + 2 * self.table.frameWidth())

    def _goalie_box(self, period: Period, labels: dict[int, str]) -> QComboBox:
        box = QComboBox()
        box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for camera_id in self.camera_ids:
            box.addItem(f"Camera {labels[camera_id]}", camera_id)
        box.addItem(NOT_SET, None)
        chosen = period.goalie_camera_id
        box.setCurrentIndex(box.count() - 1 if chosen is None else box.findData(chosen))
        box.setToolTip(
            "Later periods alternate ends from this one"
            if period.number == 1
            else f"Only {period_name(period.number)}"
        )
        box.currentIndexChanged.connect(
            lambda _i, n=period.number, b=box: self.set_goalie(n, b.currentData())
        )
        return box

    def shutdown(self) -> None:
        self.player.shutdown()
