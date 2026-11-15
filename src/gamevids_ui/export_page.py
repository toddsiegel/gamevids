"""The Export page: make the game video from the kept clips.

*Export…* asks where to save the video, starting at
`~/Videos/Game Vids/<title> - DZone.mp4`; the Save dialog asks before
replacing a video that's there. The export runs on a worker thread with its
progress shown on the page, and only one runs at a time. It carries on if
another game is picked meanwhile, and the page shows it again on coming
back to its game.
"""

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gamevids.export import (
    ExportError,
    ExportResult,
    Shot,
    default_path,
    export_video,
    kept_clips,
    load_exports,
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


class ExportPage(QWidget):
    def __init__(
        self, conn: sqlite3.Connection, parent=None, *, export: ExportFunction = export_video
    ):
        super().__init__(parent)
        self.conn = conn
        self.export = export
        self.game: Game | None = None
        self.shots: list[Shot] = []
        self.thread: ExportThread | None = None
        self.outcome: tuple[int, str] | None = None  # (game id, how its last export here ended)

        self.heading = QLabel()
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.last = QLabel()
        self.last.setWordWrap(True)

        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Make the game video (Ctrl+E)")
        self.export_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        buttons = QHBoxLayout()
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self.heading)
        layout.addWidget(self.summary)
        layout.addSpacing(12)
        layout.addLayout(buttons)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addSpacing(12)
        layout.addWidget(self.last)
        layout.addStretch(1)

        QShortcut(QKeySequence("Ctrl+E"), self, self.start)
        self.clear()

    @property
    def busy(self) -> bool:
        return self.thread is not None

    # -- loading -----------------------------------------------------------------

    def clear(self, message: str = "Pick a game on the left.") -> None:
        self.game = None
        self.shots = []
        self.heading.setText(message)
        self.summary.clear()
        self.last.clear()
        self._show()

    def set_game(self, game: Game) -> None:
        self.game = game
        self.refresh()

    def refresh(self) -> None:
        """Count the kept clips again, since triage may have changed them."""
        game = self.game
        if game is None:
            return
        self.heading.setText(game.title)
        try:
            self.shots = plan_shots(game, kept_clips(self.conn, game))
        except ExportError as e:
            self.shots = []
            self.summary.setText(f"Can't export: {e}.")
        else:
            self.summary.setText(self._summary(game))
        exports = load_exports(self.conn, game.id)
        if exports:
            latest = exports[0]
            when = latest.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            self.last.setText(
                f"Last exported {when}: {plural(latest.clips, 'clip')} in "
                f"{hms(latest.duration)}, to {latest.path}"
            )
        else:
            self.last.setText("Not exported yet.")
        self._show()

    def _summary(self, game: Game) -> str:
        if not self.shots:
            return "No kept clips yet. Keep some on the Triage tab (Alt+4) first."
        clips = sum(len(s.clip_ids) for s in self.shots)
        text = (
            f"{plural(clips, 'kept clip')} from every camera in the order they happened, "
            f"after a title card: {hms(video_length(self.shots))} of 1080p60 video."
        )
        triage = load_triage(self.conn, game)
        undecided = len(triage.clips) - triage.decided
        if undecided:
            text += (
                f"\n{plural(undecided, 'clip')} of our goalie's "
                f"{'is' if undecided == 1 else 'are'} still undecided and will be left out."
            )
        return text

    # -- exporting ---------------------------------------------------------------

    def ask_path(self, default: Path) -> Path | None:
        """Ask where to save the video; the dialog confirms replacing a file."""
        default.parent.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Export Game Video", str(default), "Videos (*.mp4)"
        )
        if not chosen:
            return None
        path = Path(chosen)
        return path if path.suffix else path.with_suffix(".mp4")

    def start(self) -> None:
        game = self.game
        if game is None or not self.shots or self.busy:
            return
        output = self.ask_path(default_path(game))
        if output is None:
            return
        thread = ExportThread(game, self.shots, output, self.export, self)
        thread.progress.connect(self._progress)
        thread.finished.connect(self._finished)
        self.thread = thread
        self.outcome = None
        thread.start()
        self._show()

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
        self._show()

    def _finished(self) -> None:
        thread = self.thread
        if thread is None or self.sender() is not thread:
            return
        self.thread = None
        thread.deleteLater()
        game_id = thread.game.id
        if thread.result is not None:
            result = thread.result
            took = time.monotonic() - thread.started_at
            try:
                save_export(self.conn, game_id, result)
            except sqlite3.Error as e:
                message = f"Saved {result.path}, but couldn't record the export: {e}"
            else:
                message = (
                    f"Saved {result.path.name}: {hms(result.duration)}, "
                    f"{result.size / 1e6:.0f} MB, made in {hms(took)}."
                )
        elif thread.stopped:
            message = "Export stopped; nothing was saved."
        else:
            message = f"Export failed: {thread.error}"
        self.outcome = (game_id, message)
        if self.game is not None and self.game.id == game_id:
            self.refresh()
        else:
            self._show()
        if thread.result is not None:
            self._reveal(thread.result.path)  # show the finished video

    @staticmethod
    def _reveal(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # -- showing -----------------------------------------------------------------

    def _show(self) -> None:
        thread, game = self.thread, self.game
        mine = thread is not None and game is not None and thread.game.id == game.id
        self.export_button.setEnabled(game is not None and bool(self.shots) and thread is None)
        self.stop_button.setVisible(mine)
        self.bar.setVisible(mine)
        if thread is not None and mine:
            fraction = thread.done / thread.total if thread.total else 0.0
            self.bar.setValue(round(fraction * 1000))
            speed = f", {thread.speed:.1f}× real time" if thread.speed else ""
            self.status.setText(
                f"Exporting to {thread.output.name}: {hms(thread.done)} of "
                f"{hms(thread.total)}{speed}"
            )
        elif thread is not None:
            self.status.setText(f"Exporting {thread.game.title}; one export runs at a time.")
        elif self.outcome is not None and game is not None and self.outcome[0] == game.id:
            self.status.setText(self.outcome[1])
        else:
            self.status.clear()

    def exporting(self, game_id: int) -> bool:
        return self.thread is not None and self.thread.game.id == game_id

    def forget(self, game_id: int) -> None:
        """Stop the game's export, if it's running, and drop how its last one ended,
        for a game about to be deleted. Nothing partial is saved."""
        if self.exporting(game_id):
            self.shutdown()
        if self.outcome is not None and self.outcome[0] == game_id:
            self.outcome = None
        self._show()

    def shutdown(self) -> None:
        """Stop any export; nothing partial is saved."""
        thread = self.thread
        if thread is not None:
            self.thread = None
            thread.finished.disconnect(self._finished)
            thread.requestInterruption()
            thread.wait()
            thread.deleteLater()
