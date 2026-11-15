"""The Triage page: watch each of our goalie's clips and keep or discard it.

Clips play in wall-clock order at 2× (adjustable), each from its in point,
pausing at its out point. Keys keep or discard a clip and move on, trim its
in and out points to the playhead, star it, or undo the last change. Every
change is saved at once, so quitting midway loses nothing, and the page
opens at the first clip still undecided.
"""

import sqlite3
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gamevids.detect import Clip
from gamevids.games import Game
from gamevids.report import clock, hms, triage_summary
from gamevids.triage import Triage, TriageError, load_triage
from gamevids_ui.clip_bar import ClipBar
from gamevids_ui.player import Player

SPEEDS = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
DEFAULT_SPEED = 2.0
SHUTTLE_BACK = 2.0  # seconds J jumps back
SEEK_JUMP = 1.0  # seconds; playback moving further than this between polls was a seek

HELP = (
    "Enter keep · X discard · S star · U undo · I / O set start / end at the playhead · "
    "Space play/pause · J back 2 s · K pause · L play/faster · ←/→ one frame · [ / ] speed"
)
COLUMNS = ("#", "Period", "Cam", "Start", "Length", "Status", "⭐")
MISSING = {
    "clips": "No clips yet. Draw and scan the D-zones on the D-Zones tab to find them.",
    "periods": "Set the periods on the Periods tab first, so triage knows our goalie's clips.",
    "goalie": "Pick the camera behind our goalie's net on the Periods tab first.",
}


def speed_text(speed: float) -> str:
    return f"{speed:g}×"


def seconds(value: float) -> str:
    """`m:ss.ss`, close enough to tell frames apart while trimming."""
    minutes, rest = divmod(max(value, 0.0), 60)
    return f"{int(minutes)}:{rest:05.2f}"


class TriagePage(QWidget):
    changed = Signal()  # a clip's decision or trim was saved, or undone

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.game: Game | None = None
        self.triage: Triage | None = None
        self.labels: dict[int, str] = {}  # camera label by id
        self.speed = DEFAULT_SPEED
        self._last: datetime | None = None  # the moment playback was last seen at
        self._ended = False  # paused at the out point, so Space replays

        self.player = Player(controls=False)
        self.player.clock_changed.connect(self._clock_changed)
        self.bar = ClipBar()
        self.bar.seek_requested.connect(self._seek)
        self.time_label = QLabel()
        self.time_label.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.status = QLabel()
        self.message = QLabel()
        self.message.setWordWrap(True)
        help_label = QLabel(HELP)
        help_label.setWordWrap(True)
        help_label.setEnabled(False)  # greyed out

        transport = QHBoxLayout()
        transport.addWidget(self.bar, 1)
        transport.addWidget(self.time_label)
        details = QHBoxLayout()
        details.addWidget(self.status, 1)
        details.addWidget(self.message, 1)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.player, 1)
        column.addLayout(transport)
        column.addLayout(details)
        column.addWidget(help_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # keys stay with triage
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._row_picked)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([860, 400])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        for keys, action in [
            (("Return", "Enter"), self.keep),
            (("X",), self.discard),
            (("S",), self.toggle_star),
            (("U", QKeySequence.StandardKey.Undo), self.undo),
            (("I",), self.set_in),
            (("O",), self.set_out),
            (("Space",), self.toggle_pause),
            (("J",), self.shuttle_back),
            (("K",), self.pause),
            (("L",), self.shuttle_forward),
            (("Right",), self.step_forward),
            (("Left",), self.step_back),
            (("[",), lambda: self.change_speed(-1)),
            (("]",), lambda: self.change_speed(1)),
        ]:
            for key in keys:
                QShortcut(QKeySequence(key), self, action)
        self.clear()

    # -- loading -----------------------------------------------------------------

    def clear(self, message: str = "Pick a game on the left.") -> None:
        self.game = None
        self.triage = None
        self.labels = {}
        self.player.clear(message)
        self._fill_table()
        self._show()

    def set_game(self, game: Game) -> None:
        self.game = game
        self.labels = {camera_id: label for label, camera_id in game.camera_ids.items()}
        self.player.set_game(game)
        self.triage = load_triage(self.conn, game)
        self.message.clear()
        self._fill_table()
        self._go(play=self.isVisible())

    def refresh(self) -> None:
        """Pick up changes made elsewhere, such as periods moved or our goalie's end changed.

        While the same clips are up for triage, the place and the undo history are kept.
        """
        if self.game is None or self.triage is None:
            return
        fresh = load_triage(self.conn, self.game)
        same = [c.id for c in fresh.clips] == [c.id for c in self.triage.clips]
        if same and fresh.missing == self.triage.missing:
            return
        self.triage = fresh
        self.message.clear()
        self._fill_table()
        self._go(play=False)

    # -- moving between clips ----------------------------------------------------

    @property
    def clip(self) -> Clip | None:
        return self.triage.current if self.triage else None

    def _go(self, *, play: bool = True) -> None:
        """Show the clip triage is at from its in point, playing if `play`."""
        self._ended = False
        self._last = None
        clip = self.clip
        if self.triage is None:
            pass  # no game: `clear` has said so
        elif self.triage.missing:
            self.player.clear(MISSING[self.triage.missing])
        elif not self.triage.clips:
            self.player.clear("No clips at our goalie's end.")
        elif clip is None:
            summary = triage_summary(self.triage)
            self.player.clear(f"{summary}\n\nPick a clip in the list to change it.")
        else:
            self.player.show_moment(clip.in_point, self.labels[clip.camera_id])
            if play:
                self.play()
            else:
                self.player.pause()
        self._show()

    def _row_picked(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if self.triage is not None and rows and rows[0].row() != self.triage.index:
            self.triage.go(rows[0].row())
            self.message.clear()
            self._go()

    # -- decisions ---------------------------------------------------------------

    def _decide(self, change, *, move: bool = False) -> None:
        if self.clip is None or self.triage is None:
            return
        index = self.triage.index
        try:
            change()
        except TriageError as e:
            self.message.setText(f"Can't: {e}.")
            return
        self.message.clear()
        self._fill_row(index)
        self.changed.emit()
        if move:
            self._go()
        else:
            self._show()

    def keep(self) -> None:
        if self.triage is not None:
            self._decide(self.triage.keep, move=True)

    def discard(self) -> None:
        if self.triage is not None:
            self._decide(self.triage.discard, move=True)

    def toggle_star(self) -> None:
        if self.triage is not None:
            self._decide(self.triage.toggle_star)

    def set_in(self) -> None:
        moment = self.player.wall_clock()
        if self.triage is not None and moment is not None:
            self._decide(lambda: self.triage.set_in(moment))

    def set_out(self) -> None:
        moment = self.player.wall_clock()
        if self.triage is not None and moment is not None:
            self._decide(lambda: self.triage.set_out(moment))

    def undo(self) -> None:
        if self.triage is None:
            return
        before = self.triage.index
        if not self.triage.undo():
            self.message.setText("Nothing to undo.")
            return
        self._fill_row(self.triage.index)
        self.changed.emit()
        moved = self.triage.index != before
        self.message.setText("Undone.")
        if moved:
            self._go()
        else:
            self._show()

    # -- playback ----------------------------------------------------------------

    def play(self) -> None:
        if self.player.timeline is not None:
            self.player.mpv.speed = self.speed
            self.player.mpv.pause = False

    def pause(self) -> None:
        self.player.pause()

    def toggle_pause(self) -> None:
        """Play or pause; at the out point, play the clip again from its in point."""
        clip = self.clip
        if self._ended and clip is not None:
            self._ended = False
            self._last = None
            self.player.show_moment(clip.in_point)
            self.play()
        elif self.player.timeline is not None and self.player.mpv.pause:
            self.play()
        else:
            self.pause()

    def shuttle_back(self) -> None:
        """Jump back `SHUTTLE_BACK` seconds, within the clip's reach, and play on."""
        clip, moment = self.clip, self.player.wall_clock()
        if clip is None or moment is None or self.triage is None:
            return
        low, _ = self.triage.reach(clip)
        self._seek(max(moment - timedelta(seconds=SHUTTLE_BACK), low), True)
        self.play()

    def shuttle_forward(self) -> None:
        """Play, or speed up when already playing."""
        if self.player.timeline is None:
            return
        if self.player.mpv.pause:
            self._ended = False
            self.play()
        else:
            self.change_speed(1)

    def step_forward(self) -> None:
        self._ended = False
        self.player.step_forward()

    def step_back(self) -> None:
        self._ended = False
        self.player.step_back()

    def change_speed(self, step: int) -> None:
        index = min(max(SPEEDS.index(self.speed) + step, 0), len(SPEEDS) - 1)
        self.speed = SPEEDS[index]
        if self.player.timeline is not None:
            self.player.mpv.speed = self.speed
        self._show()

    def _seek(self, moment: datetime, exact: bool) -> None:
        self._ended = False
        self._last = None  # a jump, not playback crossing the out point
        self.player.show_moment(moment, exact=exact)

    def _clock_changed(self, _label: str, moment: datetime) -> None:
        """Pause when playback reaches the out point."""
        clip, last = self.clip, self._last
        self._last = moment
        self.bar.set_playhead(moment)
        self._show_time(moment)
        if clip is None or last is None or self.player.mpv.pause:
            return
        out = clip.out_point
        if last < out <= moment and (moment - last).total_seconds() < SEEK_JUMP:
            self.pause()
            self.player.show_moment(out)  # back to the out point from any overshoot
            self._last = out
            self._ended = True

    # -- showing -----------------------------------------------------------------

    def _show(self) -> None:
        triage, clip = self.triage, self.clip
        if triage is None or clip is None:
            self.bar.set_clip(None, None, None)
            self.time_label.clear()
            self.status.setText(f"Speed {speed_text(self.speed)}")
        else:
            period = triage.period
            assert period is not None
            self.bar.set_clip(
                triage.reach(clip), (clip.start, clip.end), (clip.in_point, clip.out_point)
            )
            state = {"kept": "Kept", "discarded": "Discarded"}.get(clip.status, "Undecided")
            parts = [
                f"Clip {triage.index + 1} of {len(triage.clips)}",
                period.name,
                f"Camera {self.labels.get(clip.camera_id, '?')}",
                clock(clip.in_point),
                state + (" ⭐" if clip.starred else ""),
                f"{triage.decided} done",
                f"Speed {speed_text(self.speed)}",
            ]
            self.status.setText(" · ".join(parts))
        self._select_row()

    def _show_time(self, moment: datetime) -> None:
        clip = self.clip
        if clip is None:
            self.time_label.clear()
            return
        into = (moment - clip.in_point).total_seconds()
        sign = "-" if into < 0 else ""
        self.time_label.setText(f"{sign}{seconds(abs(into))} / {seconds(clip.duration)}")

    def _fill_table(self) -> None:
        clips = self.triage.clips if self.triage else []
        self.table.blockSignals(True)
        self.table.clearContents()
        self.table.setRowCount(len(clips))
        self.table.blockSignals(False)
        for row in range(len(clips)):
            self._fill_row(row)
        self._select_row()

    def _fill_row(self, row: int) -> None:
        assert self.triage is not None
        clip, period = self.triage.clips[row], self.triage.periods[row]
        status = {"kept": "Kept", "discarded": "Discarded"}.get(clip.status, "")
        cells = (
            str(row + 1),
            period.name,
            self.labels.get(clip.camera_id, "?"),
            clock(clip.in_point),
            hms(clip.duration),
            status,
            "⭐" if clip.starred else "",
        )
        self.table.blockSignals(True)
        for column, text in enumerate(cells):
            self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.blockSignals(False)

    def _select_row(self) -> None:
        self.table.blockSignals(True)
        if self.triage is not None and self.triage.current is not None:
            self.table.selectRow(self.triage.index)
            self.table.scrollToItem(self.table.item(self.triage.index, 0))
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)

    def shutdown(self) -> None:
        self.player.shutdown()
