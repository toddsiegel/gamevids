"""The export runner, run headless with a stand-in for the export itself."""

import os
import threading
from contextlib import closing
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_ui_triage import add_game_with_clips  # noqa: E402

from gamevids import db  # noqa: E402
from gamevids.export import ExportError, ExportResult, load_exports  # noqa: E402
from gamevids.triage import load_triage, save_triage  # noqa: E402
from gamevids_ui.export_runner import ExportRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


def fake_export(title, shots, output, progress=None, **_):
    """Make a pretend video, reporting progress halfway and at the end."""
    total = 3.0 + sum(s.duration for s in shots)
    progress(total / 2, total)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"x" * 2_000_000)
    progress(total, total)
    return ExportResult(output, total, sum(len(s.clip_ids) for s in shots), len(shots), 2_000_000)


@pytest.fixture
def make_runner(app, conn, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    revealed = []
    monkeypatch.setattr(ExportRunner, "_reveal", staticmethod(revealed.append))
    runners = []

    def make(export=fake_export):
        runner = ExportRunner(conn, export=export)
        runner.titles = []
        runner.ask_path = lambda default, title: runner.titles.append(title) or default
        runner.explained = []
        runner.explain = runner.explained.append
        runner.asked = []
        runner.confirm_undecided = lambda text: runner.asked.append(text) or True
        runner.endings = []
        runner.ended.connect(lambda message, failed: runner.endings.append((message, failed)))
        runner.revealed = revealed
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.shutdown()


def finish(runner):
    """Wait for the export thread, then deliver its signals."""
    if runner.thread is not None:
        runner.thread.wait()
    QApplication.processEvents()


def keep_first(conn, game, count=1):
    triage = load_triage(conn, game)
    for clip in triage.clips[:count]:
        save_triage(conn, replace(clip, status="kept"))


def keep_all(conn, game):
    keep_first(conn, game, len(load_triage(conn, game).clips))


def test_export_needs_a_game_and_no_export_running(make_runner, conn, tmp_path):
    runner = make_runner()
    assert not runner.export_action.isEnabled() and not runner.stop_action.isEnabled()
    runner.set_game(add_game_with_clips(conn, tmp_path))
    assert runner.export_action.isEnabled()  # even with nothing kept, to say why
    runner.set_game(None)
    assert not runner.export_action.isEnabled()


def test_with_nothing_kept_export_says_why(make_runner, conn, tmp_path):
    runner = make_runner()
    runner.set_game(add_game_with_clips(conn, tmp_path))

    runner.start()

    assert runner.explained == [
        "vs Lafayette has no kept clips yet. Keep some on the Triage tab (Alt+4) first."
    ]
    assert runner.thread is None and runner.titles == []


def test_export_says_why_it_cant_plan_the_video(make_runner, conn, tmp_path, monkeypatch):
    def unplannable(game, clips):
        raise ExportError("camera B has no start time")

    monkeypatch.setattr("gamevids_ui.export_runner.plan_shots", unplannable)
    runner = make_runner()
    runner.set_game(add_game_with_clips(conn, tmp_path))

    runner.start()

    assert runner.explained == ["Can't export vs Lafayette: camera B has no start time."]
    assert runner.thread is None


def test_export_asks_before_leaving_out_undecided_clips(make_runner, conn, tmp_path):
    runner = make_runner()
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game, 2)
    runner.set_game(game)
    runner.confirm_undecided = lambda text: runner.asked.append(text) or False

    runner.start()  # cancelled

    assert runner.asked == [
        "1 clip of our goalie's is still undecided and will be left out of the game video."
    ]
    assert runner.thread is None and runner.titles == []


def test_export_saves_the_video_and_records_it(make_runner, conn, tmp_path):
    runner = make_runner()
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game, 2)
    runner.set_game(game)

    runner.start()
    assert runner.titles == ["Export Game Video: 2 clips, 0:00:33"]  # 3 s title, 20 s A, 10 s B
    assert runner.busy and not runner.export_action.isEnabled()
    assert runner.stop_action.isEnabled()
    assert runner.status.startswith("Exporting vs Lafayette: ")
    finish(runner)

    video = tmp_path / "Videos" / "Game Vids" / "vs Lafayette - DZone.mp4"
    assert video.exists()
    ((message, failed),) = runner.endings
    assert message.startswith("Saved vs Lafayette - DZone.mp4: 0:00:33, 2 MB, made in ")
    assert not failed
    (export,) = load_exports(conn, game.id)
    assert (export.path, export.clips, export.duration) == (video, 2, 33.0)
    assert runner.export_action.isEnabled() and not runner.stop_action.isEnabled()
    assert runner.status == ""


def test_all_kept_exports_without_asking(make_runner, conn, tmp_path):
    runner = make_runner()
    game = add_game_with_clips(conn, tmp_path)
    keep_all(conn, game)
    runner.set_game(game)

    runner.start()
    finish(runner)

    assert runner.asked == [] and len(load_exports(conn, game.id)) == 1


def test_cancelling_the_save_dialog_exports_nothing(make_runner, conn, tmp_path):
    runner = make_runner()
    game = add_game_with_clips(conn, tmp_path)
    keep_all(conn, game)
    runner.set_game(game)
    runner.ask_path = lambda default, title: None

    runner.start()

    assert runner.thread is None and load_exports(conn, game.id) == []


def test_a_failed_export_says_why(make_runner, conn, tmp_path):
    def broken(*_, **__):
        raise ExportError("footage not found: /gone.MP4")

    runner = make_runner(broken)
    game = add_game_with_clips(conn, tmp_path)
    keep_all(conn, game)
    runner.set_game(game)

    runner.start()
    finish(runner)

    assert runner.endings == [("Couldn't export vs Lafayette: footage not found: /gone.MP4", True)]
    assert load_exports(conn, game.id) == []
    assert runner.revealed == []


def test_stop_ends_the_export_and_saves_nothing(make_runner, conn, tmp_path):
    started = threading.Event()

    def endless(title, shots, output, progress=None, **_):
        while True:
            progress(1.0, 10.0)
            started.set()

    runner = make_runner(endless)
    game = add_game_with_clips(conn, tmp_path)
    keep_all(conn, game)
    runner.set_game(game)

    runner.start()
    assert started.wait(5)
    runner.stop_action.trigger()
    finish(runner)

    assert runner.thread is None
    assert runner.endings == [("Export stopped; nothing was saved.", False)]
    assert load_exports(conn, game.id) == []


def test_one_export_runs_at_a_time(make_runner, conn, tmp_path):
    release = threading.Event()

    def waiting(*args, **kwargs):
        release.wait(5)
        return fake_export(*args, **kwargs)

    runner = make_runner(waiting)
    game = add_game_with_clips(conn, tmp_path)
    keep_all(conn, game)
    other_folder = tmp_path / "other"
    other_folder.mkdir()
    other = add_game_with_clips(conn, other_folder)
    keep_all(conn, other)
    runner.set_game(game)
    runner.start()

    runner.set_game(other)
    assert not runner.export_action.isEnabled()
    assert runner.status.startswith("Exporting vs Lafayette: ")  # names the game exporting

    release.set()
    finish(runner)
    assert runner.export_action.isEnabled()
    assert [p.parent for p in runner.revealed] == [tmp_path / "Videos" / "Game Vids"]
    assert load_exports(conn, game.id) and not load_exports(conn, other.id)


def test_the_exports_dialog_lists_a_games_videos_newest_first(app, conn, tmp_path, monkeypatch):
    from gamevids.export import save_export
    from gamevids_ui.exports_dialog import ExportsDialog

    opened = []
    monkeypatch.setattr(ExportsDialog, "_open", staticmethod(opened.append))
    game = add_game_with_clips(conn, tmp_path)

    dialog = ExportsDialog(conn, game)
    assert dialog.table.isHidden() and dialog.message.text().startswith("Not exported yet.")
    assert not dialog.open_button.isEnabled() and not dialog.folder_button.isEnabled()

    first = tmp_path / "first.mp4"  # never written, so missing
    second = tmp_path / "out" / "second.mp4"
    second.parent.mkdir()
    second.write_bytes(b"x")
    save_export(conn, game.id, ExportResult(first, 33.0, 2, 2, 1))
    save_export(conn, game.id, ExportResult(second, 63.0, 3, 3, 1))

    dialog = ExportsDialog(conn, game)
    assert dialog.message.text() == "2 exports, newest first."
    rows = [
        [dialog.table.item(row, column).text() for column in range(1, 4)]
        for row in range(dialog.table.rowCount())
    ]
    assert rows == [["3", "0:01:03", str(second)], ["2", "0:00:33", f"{first} (missing)"]]

    assert dialog.picked.path == second  # the newest is picked
    dialog.open_video()
    dialog.show_folder()
    assert opened == [second, second.parent]

    dialog.table.selectRow(1)
    assert not dialog.open_button.isEnabled() and dialog.folder_button.isEnabled()
    dialog.open_video()  # ignored: the file's gone
    assert opened == [second, second.parent]
