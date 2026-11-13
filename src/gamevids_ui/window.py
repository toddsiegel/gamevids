"""The main window: the game list beside the player."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gamevids.games import GameError, list_games, load_game
from gamevids.report import hms
from gamevids_ui.add_game import AddGameDialog
from gamevids_ui.player import Player

GAME_ID = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self.footage_dir: Path | None = None  # where the last Add Game picked files from
        self.setWindowTitle("Game Vids")

        self.add_action = QAction("&Add Game…", self)
        self.add_action.setShortcut(QKeySequence.StandardKey.New)
        self.add_action.triggered.connect(self.add_game)
        self.menuBar().addMenu("&Game").addAction(self.add_action)

        self.games = QListWidget()
        self.games.setAlternatingRowColors(True)
        self.games.setMinimumWidth(260)
        self.games.currentItemChanged.connect(self._game_picked)
        self.player = Player()
        add_button = QPushButton("Add Game…")
        add_button.clicked.connect(self.add_game)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(add_button)
        side.addWidget(self.games, 1)

        splitter = QSplitter()
        splitter.addWidget(sidebar)
        splitter.addWidget(self.player)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1080])
        self.setCentralWidget(splitter)
        self.reload_games()

    def reload_games(self, select: int | None = None) -> None:
        """Fill the game list again, picking game `select` if given."""
        self.games.clear()
        summaries = list_games(self.conn, "date", descending=True)
        for game in summaries:
            cameras = f"{game.cameras} camera{'' if game.cameras == 1 else 's'}"
            item = QListWidgetItem(f"{game.title}\n{game.date} · {cameras} · {hms(game.footage)}")
            item.setData(GAME_ID, game.id)
            self.games.addItem(item)
            if game.id == select:
                self.games.setCurrentItem(item)
        if not summaries:
            self.player.clear("No games yet. Add one with Game ▸ Add Game… (Ctrl+N).")

    def add_game(self) -> None:
        dialog = AddGameDialog(self.conn, self, start_dir=self.footage_dir)
        accepted = dialog.exec()
        self.footage_dir = dialog.start_dir
        if accepted and dialog.game_id is not None:
            self.reload_games(select=dialog.game_id)

    def _game_picked(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        try:
            game = load_game(self.conn, item.data(GAME_ID))
        except GameError as e:
            self.player.clear(str(e))
            return
        self.setWindowTitle(f"{game.title} — Game Vids")
        self.player.set_game(game)
        self.player.video.setFocus()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.player.shutdown()
        super().closeEvent(event)
