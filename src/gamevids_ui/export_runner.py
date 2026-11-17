"""Making the game video from the kept clips, from the Game menu.

*Export…* (Ctrl+E) works on the picked game from any tab. It says why when
there's nothing to export, and asks first when some of our goalie's clips
are still undecided, since those are left out. Then it asks where to save
the video, starting at `~/Videos/Game Vids/<title> - DZone.mp4`; the Save
dialog asks before replacing a video that's there.

The export runs on a worker thread, and only one runs at a time. It carries
on if another game is picked meanwhile. The window shows its progress in the
status bar, with *Stop* there and in the Game menu, and says how it ended.
"""

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from gamevids.export import (
    ExportError,
    ExportResult,
    Shot,
    default_path,
    export_video,
    kept_clips,
    plan_shots,
    save_export,
    video_length,
)
from gamevids.frames import VideoError
from gamevids.games import Game
from gamevids.report import hms
from gamevids.triage import load_triage

ExportFunction = Callable[..., ExportResult]  # called like `gamevids.export.export_video`


class _Stopped(Exception):
    pass


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


class ExportThread(QThread):
    progress = Signal(float, float)  # seconds of video made, seconds in all

    def __init__(
        self, game: Game, shots: list[Shot], output: Path, export: ExportFunction, parent=None
    ):
        super().__init__(parent)
        self.game = game
        self.shots = shots
        self.output = output
        self.export = export
        self.result: ExportResult | None = None
        self.error: str | None = None
        self.stopped = False
        self.started_at = time.monotonic()
        self.done = 0.0
        self.total = video_length(shots)

    def _progress(self, done: float, total: float) -> None:
        if self.isInterruptionRequested():
            raise _Stopped
        self.progress.emit(done, total)

    def run(self) -> None:
        try:
            self.result = self.export(
                self.game.title, self.shots, self.output, progress=self._progress
            )
        except _Stopped:
            self.stopped = True
        except (ExportError, VideoError, OSError) as e:
            self.error = str(e)

    @property
    def speed(self) -> float | None:
        elapsed = time.monotonic() - self.started_at
        return self.done / elapsed if elapsed > 1 else None


class ExportRunner(QObject):
    changed = Signal()  # an export started, made progress or ended
    ended = Signal(str, bool)  # how the export ended, and whether that's a failure

    def __init__(
        self, conn: sqlite3.Connection, parent=None, *, export: ExportFunction = export_video
    ):
        super().__init__(parent)
        self.conn = conn
        self.export = export
        self.game: Game | None = None
        self.thread: ExportThread | None = None

        # The Game menu shows these, so they work from any tab.
        self.export_action = QAction("&Export…", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.setToolTip("Make the game video (Ctrl+E)")
        self.export_action.triggered.connect(self.start)
        self.stop_action = QAction("&Stop Export", self)
        self.stop_action.setToolTip("Stop making the game video; nothing is saved")
        self.stop_action.triggered.connect(self.stop)
        self._enable()

    @property
    def busy(self) -> bool:
        return self.thread is not None

    def set_game(self, game: Game | None) -> None:
        self.game = game
        self._enable()

    def _enable(self) -> None:
        self.export_action.setEnabled(self.game is not None and self.thread is None)
        self.stop_action.setEnabled(self.thread is not None)

    # -- asking ------------------------------------------------------------------

    def _window(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def explain(self, text: str) -> None:
        """Say why the game can't be exported."""
        QMessageBox.information(self._window(), "Export Game Video", text)

    def confirm_undecided(self, text: str) -> bool:
        """Ask whether to export without the undecided clips."""
        box = QMessageBox(
            QMessageBox.Icon.Question, "Export Game Video", text, parent=self._window()
        )
        export = box.addButton("Export", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
        box.exec()
        return box.clickedButton() is export

    def ask_path(self, default: Path, title: str) -> Path | None:
        """Ask where to save the video; the dialog confirms replacing a file."""
        default.parent.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(
            self._window(), title, str(default), "Videos (*.mp4)"
        )
        if not chosen:
            return None
        path = Path(chosen)
        return path if path.suffix else path.with_suffix(".mp4")

    # -- exporting ---------------------------------------------------------------

    def start(self) -> None:
        game = self.game
        if game is None or self.busy:
            return
        try:
            shots = plan_shots(game, kept_clips(self.conn, game))
        except ExportError as e:
            self.explain(f"Can't export {game.title}: {e}.")
            return
        if not shots:
            self.explain(
                f"{game.title} has no kept clips yet. Keep some on the Triage tab (Alt+4) first."
            )
            return
        triage = load_triage(self.conn, game)
        undecided = len(triage.clips) - triage.decided
        if undecided and not self.confirm_undecided(
            f"{plural(undecided, 'clip')} of our goalie's "
            f"{'is' if undecided == 1 else 'are'} still undecided and will be left out "
            "of the game video."
        ):
            return
        clips = sum(len(s.clip_ids) for s in shots)
        title = f"Export Game Video: {plural(clips, 'clip')}, {hms(video_length(shots))}"
        output = self.ask_path(default_path(game), title)
        if output is None:
            return
        thread = ExportThread(game, shots, output, self.export, self)
        thread.progress.connect(self._progress)
        thread.finished.connect(self._finished)
        self.thread = thread
        thread.start()
        self._enable()
        self.changed.emit()

    def stop(self) -> None:
        thread = self.thread
        if thread is None:
            return
        thread.requestInterruption()
        thread.wait()  # `_finished` follows from the event loop

    def _progress(self, done: float, total: float) -> None:
        thread = self.thread
        if thread is None or self.sender() is not thread:
            return
        thread.done, thread.total = done, total
        self.changed.emit()

    def _finished(self) -> None:
        thread = self.thread
        if thread is None or self.sender() is not thread:
            return
        self.thread = None
        thread.deleteLater()
        failed = False
        if thread.result is not None:
            result = thread.result
            took = time.monotonic() - thread.started_at
            try:
                save_export(self.conn, thread.game.id, result)
            except sqlite3.Error as e:
                message = f"Saved {result.path}, but couldn't record the export: {e}"
                failed = True
            else:
                message = (
                    f"Saved {result.path.name}: {hms(result.duration)}, "
                    f"{result.size / 1e6:.0f} MB, made in {hms(took)}."
                )
        elif thread.stopped:
            message = "Export stopped; nothing was saved."
        else:
            message = f"Couldn't export {thread.game.title}: {thread.error}"
            failed = True
        self._enable()
        self.changed.emit()
        self.ended.emit(message, failed)
        if thread.result is not None:
            self._reveal(thread.result.path)  # show the finished video

    @staticmethod
    def _reveal(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # -- showing -----------------------------------------------------------------

    @property
    def status(self) -> str:
        """What the running export is doing, or "" when none is."""
        thread = self.thread
        if thread is None:
            return ""
        speed = f", {thread.speed:.1f}× real time" if thread.speed else ""
        return f"Exporting {thread.game.title}: {hms(thread.done)} of {hms(thread.total)}{speed}"

    @property
    def fraction(self) -> float:
        thread = self.thread
        if thread is None or not thread.total:
            return 0.0
        return thread.done / thread.total

    def exporting(self, game_id: int) -> bool:
        return self.thread is not None and self.thread.game.id == game_id

    def forget(self, game_id: int) -> None:
        """Stop the game's export, if it's running, for a game about to be deleted.
        Nothing partial is saved."""
        if self.exporting(game_id):
            self.shutdown()

    def shutdown(self) -> None:
        """Stop any export; nothing partial is saved."""
        thread = self.thread
        if thread is not None:
            self.thread = None
            thread.finished.disconnect(self._finished)
            thread.requestInterruption()
            thread.wait()
            thread.deleteLater()
            self._enable()
            self.changed.emit()
