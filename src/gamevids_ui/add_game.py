"""The Add Game dialog: pick a game's files, check how they group, title it and save it.

Files are probed on a background thread. Files with no camera serial get a
row each, where you put them on a camera (or leave them out), set their
period, and set a start time when the file lost its own. Every change groups
the files again, so the tree always shows what will be saved.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QDate, QStandardPaths, Qt, QThread, QTime, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gamevids.games import GameError, add_game, files_in_library
from gamevids.grouping import (
    Assignment,
    Chapter,
    FileKind,
    Grouping,
    GroupingError,
    assign_files,
    estimate_start,
    group_files,
)
from gamevids.probe import MediaInfo, ProbeError, find_videos, probe_file
from gamevids.report import clock, hms, media_format, period_name

VIDEO_FILTER = "Videos (*.mp4 *.MP4 *.mov *.MOV);;All files (*)"
PERIODS = [None, "1", "2", "3", "OT"]
NO_TIME = QTime(0, 0, 0)  # the time edit's minimum, shown as "not set"
TREE_COLUMNS = ["File", "Start", "End", "Length", "Period", "Note"]
LOOSE_COLUMNS = ["File", "Length", "Camera", "Period", "Start", ""]

Probe = Callable[[Path], MediaInfo]


class ProbeThread(QThread):
    """Probes files one by one. Read `infos` and `errors` once it finishes."""

    progress = Signal(int, int, str)  # files done, files in all, the file being read

    def __init__(self, paths: list[Path], probe: Probe, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.probe = probe
        self.infos: list[MediaInfo] = []
        self.errors: list[str] = []

    def run(self) -> None:
        for done, path in enumerate(self.paths):
            if self.isInterruptionRequested():
                return
            self.progress.emit(done, len(self.paths), path.name)
            try:
                self.infos.append(self.probe(path))
            except ProbeError as e:
                self.errors.append(str(e))


@dataclass
class LooseRow:
    """The editors for one file with no camera serial."""

    chapter: Chapter
    camera: QComboBox
    period: QComboBox
    start: QTimeEdit
    note: QLabel
    start_by_hand: bool = False

    @property
    def path(self) -> Path:
        return self.chapter.file.path


def _item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    return item


class AddGameDialog(QDialog):
    """Run with `exec()`; when accepted, `game_id` is the new game."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        parent: QWidget | None = None,
        *,
        probe: Probe = probe_file,
        start_dir: Path | None = None,
    ):
        super().__init__(parent)
        self.conn = conn
        self.probe = probe
        movies = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
        self.start_dir = start_dir or Path(movies or Path.home())
        self.game_id: int | None = None
        self.infos: dict[Path, MediaInfo] = {}
        self.errors: list[str] = []
        self.grouping = Grouping()  # the files as grouped on their own
        self.placed = Grouping()  # with the loose files placed by hand
        self.left_out: list[Chapter] = []
        self.taken: dict[Path, tuple[int, str]] = {}
        self.loose: list[LooseRow] = []
        self._thread: ProbeThread | None = None
        self._missing: list[str] = []  # what the loose-file rows still need
        self._auto_title = ""
        self._auto_date = QDate.currentDate()

        self.setWindowTitle("Add Game")
        self.resize(1100, 700)

        add_files = QPushButton("Add Files…")
        add_files.clicked.connect(self._pick_files)
        add_folder = QPushButton("Add Folder…")
        add_folder.clicked.connect(self._pick_folder)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        self.status = QLabel()
        pickers = QHBoxLayout()
        for button in (add_files, add_folder, self.clear_button):
            pickers.addWidget(button)
        pickers.addWidget(self.status, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(TREE_COLUMNS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(True)

        self.loose_table = QTableWidget(0, len(LOOSE_COLUMNS))
        self.loose_table.setHorizontalHeaderLabels(LOOSE_COLUMNS)
        self.loose_table.verticalHeader().hide()
        self.loose_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        header = self.loose_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.loose_box = QGroupBox("Files with no camera serial: place each on a camera")
        QVBoxLayout(self.loose_box).addWidget(self.loose_table)

        self.warnings = QLabel()
        self.warnings.setWordWrap(True)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Shown on the title card, e.g. 2026-10-17 vs Lafayette")
        self.title_edit.textChanged.connect(self._validate)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(self._auto_date)
        self.date_edit.dateChanged.connect(self._refresh)  # placed start times use the date
        form = QFormLayout()
        form.addRow("Title", self.title_edit)
        form.addRow("Date", self.date_edit)

        self.problem = QLabel()
        self.problem.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.add_button = buttons.addButton("Add Game", QDialogButtonBox.ButtonRole.AcceptRole)
        self.add_button.setDefault(True)
        buttons.accepted.connect(self._add)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.problem, 1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(pickers)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.loose_box)
        layout.addWidget(self.warnings)
        layout.addLayout(form)
        layout.addLayout(bottom)
        self.clear()

    # -- picking and probing ---------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _pick_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "Add Video Files", str(self.start_dir), VIDEO_FILTER
        )
        if names:
            self.start_dir = Path(names[0]).parent
            self.add_paths([Path(n) for n in names])

    def _pick_folder(self) -> None:
        name = QFileDialog.getExistingDirectory(self, "Add a Game Folder", str(self.start_dir))
        if name:
            self.start_dir = Path(name).parent
            self.add_paths([Path(name)])

    def add_paths(self, paths: list[Path]) -> None:
        """Probe the video files among `paths` (folders are expanded) and add them."""
        if self.busy:
            return
        new = [p for p in dict.fromkeys(find_videos(paths)) if p not in self.infos]
        if not new:
            self.status.setText("No new video files there.")
            return
        self._thread = ProbeThread(new, self.probe, self)
        self._thread.progress.connect(self._show_progress)
        self._thread.finished.connect(self._probed)
        self._thread.start()
        self._validate()

    def _show_progress(self, done: int, total: int, name: str) -> None:
        self.status.setText(f"Reading file {done + 1} of {total}: {name}")

    def _probed(self) -> None:
        thread = self._thread
        assert thread is not None
        self._thread = None
        thread.deleteLater()
        for info in thread.infos:
            self.infos[info.path] = info
        self.errors += thread.errors
        self.status.clear()
        self._regroup()

    def clear(self) -> None:
        """Forget every file picked so far."""
        if self.busy:
            return
        self.infos = {}
        self.errors = []
        self.status.clear()
        self._regroup()

    def _stop_probing(self) -> None:
        if self._thread is not None:
            self._thread.requestInterruption()
            self._thread.wait()

    def done(self, result: int) -> None:
        self._stop_probing()
        super().done(result)

    # -- grouping --------------------------------------------------------------

    def _regroup(self) -> None:
        """Group the probed files afresh, and make a row for each with no serial."""
        self.grouping = group_files(list(self.infos.values()))
        paths = [c.file.path for cam in self.grouping.cameras for c in cam.chapters]
        paths += [c.file.path for c in self.grouping.unassigned]
        self.taken = files_in_library(self.conn, paths)
        self._build_loose_rows()
        self._refresh()

    def _camera_choices(self) -> list[tuple[str, str | None]]:
        """(text, label) for the camera picker; label None means not chosen, "" left out."""
        choices: list[tuple[str, str | None]] = [("Choose…", None)]
        choices += [(f"{c.label} · {c.serial}", c.label) for c in self.grouping.cameras]
        taken = {c.label for c in self.grouping.cameras}
        free = [chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in taken]
        choices += [(f"{label} · new camera", label) for label in free[: len(self.loose)]]
        choices.append(("Leave out", ""))
        return choices

    def _build_loose_rows(self) -> None:
        self.loose = []
        table = self.loose_table
        table.setRowCount(0)
        for chapter in self.grouping.unassigned:
            camera = QComboBox()
            period = QComboBox()
            for value in PERIODS:
                period.addItem(period_name(value) or "—", value)
            period.setCurrentIndex(PERIODS.index(chapter.period))
            start = QTimeEdit()
            start.setDisplayFormat("HH:mm:ss")
            start.setMinimumTime(NO_TIME)
            start.setSpecialValueText("not set")
            row = LooseRow(chapter, camera, period, start, QLabel())
            self.loose.append(row)

        choices = self._camera_choices()
        table.setRowCount(len(self.loose))
        for index, row in enumerate(self.loose):
            for text, label in choices:
                row.camera.addItem(text, label)
            info = row.chapter.file
            if info.creation_time:
                row.start.setTime(_qtime(info.creation_time))
                row.start.setEnabled(False)
                row.note.setText("start from the file")
            else:
                row.start.setTime(NO_TIME)
            row.camera.currentIndexChanged.connect(lambda _, r=row: self._camera_changed(r))
            row.period.currentIndexChanged.connect(self._refresh)
            row.start.timeChanged.connect(lambda _, r=row: self._start_edited(r))
            table.setItem(index, 0, _item(info.path.name))
            table.setItem(index, 1, _item(hms(info.duration)))
            table.setCellWidget(index, 2, row.camera)
            table.setCellWidget(index, 3, row.period)
            table.setCellWidget(index, 4, row.start)
            table.setCellWidget(index, 5, row.note)
        table.resizeRowsToContents()
        rows = sum(table.rowHeight(i) for i in range(table.rowCount()))
        table.setFixedHeight(table.horizontalHeader().height() + rows + 2 * table.frameWidth())
        self.loose_box.setVisible(bool(self.loose))

    def _camera_changed(self, row: LooseRow) -> None:
        """Suggest a start from the file's timecode, unless one was typed in."""
        info = row.chapter.file
        if info.creation_time is None and not row.start_by_hand:
            label = row.camera.currentData()
            camera = next((c for c in self.grouping.cameras if c.label == label), None)
            guess = estimate_start(info, camera) if camera else None
            row.start.blockSignals(True)
            row.start.setTime(_qtime(guess) if guess else NO_TIME)
            row.start.blockSignals(False)
            if guess:
                row.note.setText("start from the timecode; check it")
            elif label:
                row.note.setText("enter the start time")
            else:
                row.note.clear()
        self._refresh()

    def _start_edited(self, row: LooseRow) -> None:
        row.start_by_hand = True
        row.note.setText("start set by hand")
        self._refresh()

    def _game_day(self) -> date:
        return self.date_edit.date().toPython()

    def _assignments(self) -> tuple[dict[Path, Assignment], list[Chapter], list[str]]:
        """What the rows say: assignments, files left out, and what's still missing."""
        assignments = {}
        left_out = []
        missing = []
        for row in self.loose:
            name = row.path.name
            label = row.camera.currentData()
            if label is None:
                missing.append(f"Choose a camera for {name}, or leave it out.")
                continue
            if label == "":
                left_out.append(row.chapter)
                continue
            start = None
            if row.chapter.file.creation_time is None:
                if row.start.time() == NO_TIME:
                    missing.append(f"Set the start time of {name}.")
                    continue
                start = datetime.combine(self._game_day(), row.start.time().toPython())
                start = start.astimezone()
            assignments[row.path] = Assignment(label, row.period.currentData(), start)
        return assignments, left_out, missing

    def _refresh(self) -> None:
        assignments, self.left_out, missing = self._assignments()
        try:
            self.placed = assign_files(self.grouping, assignments)
        except GroupingError as e:
            self.placed = self.grouping
            missing.append(str(e))
        self._missing = missing
        self._prefill()
        self._fill_tree()
        warnings = [*self.errors, *self.placed.warnings]
        self.warnings.setText("\n".join(f"⚠ {w}" for w in warnings))
        self.warnings.setVisible(bool(warnings))
        self._validate()

    def _prefill(self) -> None:
        """Suggest a title from the folder and a date from the files, until either is edited."""
        folders = {p.parent for p in self.infos}
        title = folders.pop().name if len(folders) == 1 else ""
        if self.title_edit.text() == self._auto_title:
            self.title_edit.setText(title)
        self._auto_title = title

        starts = [self.grouping.date] if self.grouping.date else []
        starts += [c.file.creation_time for c in self.grouping.unassigned if c.file.creation_time]
        if starts and self.date_edit.date() == self._auto_date:
            first = min(starts).astimezone()
            day = QDate(first.year, first.month, first.day)
            self.date_edit.blockSignals(True)
            self.date_edit.setDate(day)
            self.date_edit.blockSignals(False)
            self._auto_date = day

    def _fill_tree(self) -> None:
        self.tree.clear()
        placed_by_hand = {row.path for row in self.loose}
        for camera in self.placed.cameras:
            formats = "; ".join(sorted({media_format(c.file) for c in camera.chapters}))
            serial = camera.serial or "no serial"
            top = self._branch(
                f"Camera {camera.label} · {serial}", hms(camera.duration), note=formats
            )
            for i, recording in enumerate(camera.recordings):
                for j, chapter in enumerate(recording.chapters):
                    notes = []
                    if i and not j:
                        gap = (recording.start - camera.recordings[i - 1].end).total_seconds()
                        notes.append(f"after {hms(gap)} gap")
                    elif j:
                        notes.append("next chapter")
                    if chapter.file.path in placed_by_hand:
                        notes.append("placed by hand")
                    if chapter.kind is FileKind.OTHER:
                        notes.append("renamed")
                    self._leaf(top, chapter, ", ".join(notes))

        waiting = [c for c in self.placed.unassigned if c not in self.left_out]
        for heading, chapters in [("No camera yet", waiting), ("Left out", self.left_out)]:
            if chapters:
                top = self._branch(heading, hms(sum(c.file.duration for c in chapters)))
                for chapter in chapters:
                    self._leaf(top, chapter, timed=chapter.file.creation_time is not None)
        if self.placed.exports:
            top = self._branch("D-zone exports, skipped", "")
            for info in self.placed.exports:
                QTreeWidgetItem(top, [info.path.name, "", "", hms(info.duration), "", ""])
        if self.taken:
            top = self._branch("Already in the library", "")
            for path, (game_id, title) in sorted(self.taken.items()):
                QTreeWidgetItem(top, [path.name, "", "", "", "", f"in game {game_id}: {title}"])
        self.tree.expandAll()
        for column in range(len(TREE_COLUMNS) - 1):
            self.tree.resizeColumnToContents(column)

    def _branch(self, heading: str, length: str, note: str = "") -> QTreeWidgetItem:
        top = QTreeWidgetItem(self.tree, [heading, "", "", length, "", note])
        font = top.font(0)
        font.setBold(True)
        top.setFont(0, font)
        return top

    def _leaf(
        self, parent: QTreeWidgetItem, chapter: Chapter, note: str = "", *, timed: bool = True
    ) -> None:
        start, end = (clock(chapter.start), clock(chapter.end)) if timed else ("", "")
        QTreeWidgetItem(
            parent,
            [
                chapter.file.path.name,
                start,
                end,
                hms(chapter.file.duration),
                period_name(chapter.period),
                note,
            ],
        )

    # -- saving ----------------------------------------------------------------

    def problems(self) -> list[str]:
        """Why the game can't be added yet; empty when it can."""
        if self.busy:
            return ["Reading the files…"]
        if not self.infos:
            return ["Add the game's video files, or its folder."]
        problems = list(self._missing)
        if self.taken:
            games = sorted({f"game {i} ({t})" for i, t in self.taken.values()})
            problems.append(
                f"{len(self.taken)} of these files are already in {', '.join(games)}. "
                "Clear and pick again."
            )
        if not self.placed.cameras and not problems:
            problems.append("None of these files is from a camera to add.")
        if not self.title_edit.text().strip():
            problems.append("Enter a title.")
        return problems

    def _validate(self) -> None:
        problems = self.problems()
        self.problem.setText(problems[0] if problems else "")
        self.add_button.setEnabled(not problems)
        self.clear_button.setEnabled(not self.busy and bool(self.infos))

    def _add(self) -> None:
        if self.problems():
            return
        grouping = replace(self.placed, unassigned=[])  # what's left was left out
        try:
            self.game_id = add_game(self.conn, self.title_edit.text(), grouping, self._game_day())
        except GameError as e:
            self.problem.setText(str(e))
            return
        self.accept()


def _qtime(moment: datetime) -> QTime:
    local = moment.astimezone()
    return QTime(local.hour, local.minute, local.second)
