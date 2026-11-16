"""The Exports dialog: the game videos made for a game, newest first.

Each row says when the video was made, how many clips it holds, how long it
runs and where it was saved. A video that's since been moved or deleted is
marked missing. *Open* plays the picked video (as does a double-click), and
*Show in Folder* opens the folder it's in.
"""

import sqlite3
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gamevids.export import Export, load_exports
from gamevids.games import Game
from gamevids.report import hms

COLUMNS = ["Made", "Clips", "Length", "File"]


class ExportsDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, game: Game, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Exports — {game.title}")
        self.resize(820, 360)
        self.exports: list[Export] = load_exports(conn, game.id)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.table = QTableWidget(len(self.exports), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        for column in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        for row, export in enumerate(self.exports):
            when = export.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            file = str(export.path) + ("" if export.path.exists() else " (missing)")
            for column, text in enumerate([when, str(export.clips), hms(export.duration), file]):
                item = QTableWidgetItem(text)
                item.setToolTip(str(export.path) if column == 3 else "")
                self.table.setItem(row, column, item)
        self.table.itemSelectionChanged.connect(self._picked)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_video())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(self.open_video)
        self.folder_button = QPushButton("Show in Folder")
        self.folder_button.clicked.connect(self.show_folder)
        buttons.addButton(self.open_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.folder_button, QDialogButtonBox.ButtonRole.ActionRole)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        if self.exports:
            self.message.setText(
                f"{len(self.exports)} export{'' if len(self.exports) == 1 else 's'}, newest first."
            )
            self.table.selectRow(0)
        else:
            self.message.setText("Not exported yet. Export… (Ctrl+E) makes the game video.")
            self.table.hide()
        self._picked()

    @property
    def picked(self) -> Export | None:
        rows = self.table.selectionModel().selectedRows()
        return self.exports[rows[0].row()] if rows else None

    def _picked(self) -> None:
        export = self.picked
        self.open_button.setEnabled(export is not None and export.path.exists())
        self.folder_button.setEnabled(export is not None and export.path.parent.is_dir())

    def open_video(self) -> None:
        export = self.picked
        if export is not None and export.path.exists():
            self._open(export.path)

    def show_folder(self) -> None:
        export = self.picked
        if export is not None and export.path.parent.is_dir():
            self._open(export.path.parent)

    @staticmethod
    def _open(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
