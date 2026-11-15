"""The main window: the game list beside a page for each step of working on a game."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gamevids.config import Config
from gamevids.games import (
    GameContents,
    GameError,
    game_contents,
    list_games,
    load_game,
    remove_game,
)
from gamevids.report import hms, removal_items, removal_note
from gamevids_ui.add_game import AddGameDialog
from gamevids_ui.export_page import ExportPage
from gamevids_ui.periods_page import PeriodsPage
from gamevids_ui.player import Player
from gamevids_ui.scans import ScanQueue
from gamevids_ui.triage_page import TriagePage
from gamevids_ui.zones_page import ZonesPage

GAME_ID = Qt.ItemDataRole.UserRole


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection, config: Config | None = None):
        super().__init__()
        self.conn = conn
        config = config or Config()
        self.footage_dir: Path | None = None  # where the last Add Game picked files from
        self.setWindowTitle("Game Vids")
        self.scans = ScanQueue(conn, self, detect_settings=config.detect)

        self.add_action = QAction("&Add Game…", self)
        self.add_action.setShortcut(QKeySequence.StandardKey.New)
        self.add_action.triggered.connect(self.add_game)
        self.delete_action = QAction("&Delete Game…", self)
        self.delete_action.setToolTip("Delete the game's data from the library (Delete)")
        self.delete_action.triggered.connect(self.delete_game)
        game_menu = self.menuBar().addMenu("&Game")
        game_menu.addAction(self.add_action)
        game_menu.addSeparator()
        game_menu.addAction(self.delete_action)

        self.games = QListWidget()
        self.games.setAlternatingRowColors(True)
        self.games.setMinimumWidth(260)
        self.games.currentItemChanged.connect(self._game_picked)
        self.games.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.games.addAction(self.delete_action)
        QShortcut(
            QKeySequence.StandardKey.Delete,
            self.games,
            self.delete_game,
            context=Qt.ShortcutContext.WidgetShortcut,
        )
        add_button = QPushButton("Add Game…")
        add_button.clicked.connect(self.add_game)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(add_button)
        side.addWidget(self.games, 1)

        self.player = Player()
        self.zones = ZonesPage(conn, self.scans)
        self.periods = PeriodsPage(conn, self.scans, settings=config.periods)
        self.triage = TriagePage(conn)
        self.export = ExportPage(conn)
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        self.pages.addTab(self.player, "Watch")
        self.pages.addTab(self.zones, "D-Zones")
        self.pages.addTab(self.periods, "Periods")
        self.pages.addTab(self.triage, "Triage")
        self.pages.addTab(self.export, "Export")
        self.pages.currentChanged.connect(self._page_picked)
        for index in range(self.pages.count()):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Alt+{index + 1}"))
            action.triggered.connect(lambda _=False, i=index: self.pages.setCurrentIndex(i))
            self.addAction(action)

        splitter = QSplitter()
        splitter.addWidget(sidebar)
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 1080])
        self.setCentralWidget(splitter)

        self.scan_status = QLabel()
        self.statusBar().addPermanentWidget(self.scan_status)
        self.scans.changed.connect(self._show_scan_status)
        self.scans.finished.connect(self._scan_finished)
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
            self._clear_pages("No games yet. Add one with Game ▸ Add Game… (Ctrl+N).")
        self.delete_action.setEnabled(self.games.currentItem() is not None)

    def add_game(self) -> None:
        dialog = AddGameDialog(self.conn, self, start_dir=self.footage_dir)
        accepted = dialog.exec()
        self.footage_dir = dialog.start_dir
        if accepted and dialog.game_id is not None:
            self.reload_games(select=dialog.game_id)
            self.pages.setCurrentWidget(self.zones)  # a new game's next step

    def delete_game(self) -> None:
        """Delete the picked game's data, once the user has seen what goes and agreed."""
        item = self.games.currentItem()
        if item is None:
            return
        game_id = item.data(GAME_ID)
        try:
            contents = game_contents(self.conn, game_id)
            camera_ids = list(load_game(self.conn, game_id).camera_ids.values())
        except GameError as e:
            self.statusBar().showMessage(str(e), 15_000)
            return
        stopping = []
        if any(self.scans.job(camera_id) for camera_id in camera_ids):
            stopping.append("Its scans will be stopped.")
        if self.export.exporting(game_id):
            stopping.append("Its export will be stopped, and nothing from it saved.")
        if not self.confirm_delete(contents, stopping):
            return

        self.scans.forget(camera_ids)
        self.export.forget(game_id)
        remove_game(self.conn, game_id)
        # Pick the game below it in the list, or above it if it was last.
        ids = [self.games.item(row).data(GAME_ID) for row in range(self.games.count())]
        row = ids.index(game_id)
        ids.remove(game_id)
        self.reload_games(select=ids[min(row, len(ids) - 1)] if ids else None)
        self.statusBar().showMessage(f"Deleted {contents.title}.", 10_000)

    def confirm_delete(self, contents: GameContents, stopping: list[str]) -> bool:
        """Say what deleting the game takes from the library, and ask before doing it."""
        items = "\n".join(f"• {item}" for item in removal_items(contents))
        after = " ".join([removal_note(contents), *stopping, "This can’t be undone."])
        box = QMessageBox(QMessageBox.Icon.Warning, "Delete Game", "", parent=self)
        box.setText(f"Delete “{contents.title}” ({contents.date.isoformat()})?")
        box.setInformativeText(f"This deletes from the library:\n{items}\n\n{after}")
        delete = box.addButton("Delete Game", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
        box.exec()
        return box.clickedButton() is delete

    def _clear_pages(self, message: str) -> None:
        self.setWindowTitle("Game Vids")
        self.player.clear(message)
        self.zones.clear()
        self.periods.clear(message)
        self.triage.clear(message)
        self.export.clear(message)

    def _game_picked(self, item: QListWidgetItem | None, _previous=None) -> None:
        self.delete_action.setEnabled(item is not None)
        if item is None:
            return
        try:
            game = load_game(self.conn, item.data(GAME_ID))
        except GameError as e:
            self._clear_pages(str(e))
            return
        self.setWindowTitle(f"{game.title} — Game Vids")
        self.player.set_game(game)
        self.zones.set_game(game)
        self.periods.set_game(game)
        self.triage.set_game(game)
        self.export.set_game(game)
        if self.pages.currentWidget() is self.player:
            self.player.video.setFocus()

    def _page_picked(self, _index: int) -> None:
        page = self.pages.currentWidget()
        for player in (self.player, self.periods.player, self.triage.player):
            if not page.isAncestorOf(player):
                player.pause()  # only the page on screen plays
        if page is self.zones:
            self.zones.canvas.setFocus()
        elif page is self.periods:
            self.periods.player.video.setFocus()
        elif page is self.triage:
            self.triage.refresh()  # the periods may have changed which clips are ours
            self.triage.player.video.setFocus()
        elif page is self.export:
            self.export.refresh()  # triage may have kept or discarded clips

    def _show_scan_status(self, _camera_id: int = 0) -> None:
        job = self.scans.running
        if job is None:
            self.scan_status.clear()
            return
        waiting = len(self.scans.waiting)
        more = f" · {waiting} more waiting" if waiting else ""
        self.scan_status.setText(
            f"Scanning {job.game_title}, camera {job.label}: {job.fraction:.0%}{more}"
        )

    def _scan_finished(self, camera_id: int) -> None:
        result = self.scans.results.get(camera_id)
        if result is not None and result.error:
            self.statusBar().showMessage(f"Scan failed: {result.error}", 15_000)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.export.busy:
            answer = QMessageBox.question(
                self,
                "Stop Exporting?",
                "A game video is still being made. Quitting stops it, and nothing is saved.",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        if self.scans.busy:
            answer = QMessageBox.question(
                self,
                "Stop Scanning?",
                "A scan is still running. Quitting stops it, and that camera will need "
                "scanning again.",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        self.scans.stop_all()
        self.zones.shutdown()
        self.periods.shutdown()
        self.triage.shutdown()
        self.export.shutdown()
        self.player.shutdown()
        super().closeEvent(event)
