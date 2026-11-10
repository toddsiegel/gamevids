"""The main window: the game list beside the player."""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMainWindow, QSplitter

from gamevids.games import GameError, list_games, load_game
from gamevids.report import hms
from gamevids_ui.player import Player

GAME_ID = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self.setWindowTitle("Game Vids")

        self.games = QListWidget()
        self.games.setAlternatingRowColors(True)
        self.games.setMinimumWidth(260)
        self.games.currentItemChanged.connect(self._game_picked)
        self.player = Player()

        splitter = QSplitter()
        splitter.addWidget(self.games)
        splitter.addWidget(self.player)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1080])
        self.setCentralWidget(splitter)
        self.reload_games()

    def reload_games(self) -> None:
        self.games.clear()
        summaries = list_games(self.conn, "date", descending=True)
        for game in summaries:
            cameras = f"{game.cameras} camera{'' if game.cameras == 1 else 's'}"
            item = QListWidgetItem(f"{game.title}\n{game.date} · {cameras} · {hms(game.footage)}")
            item.setData(GAME_ID, game.id)
            self.games.addItem(item)
        if not summaries:
            self.player.clear("No games yet. Add one with `gamevids game add`.")

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
