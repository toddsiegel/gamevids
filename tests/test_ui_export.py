"""The Export page, run headless with a stand-in for the export itself."""

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
from gamevids_ui.export_page import ExportPage  # noqa: E402


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
def make_page(app, conn, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    revealed = []
    monkeypatch.setattr(ExportPage, "_reveal", staticmethod(revealed.append))
    pages = []

    def make(export=fake_export):
        page = ExportPage(conn, export=export)
        page.ask_path = lambda default: default
        page.revealed = revealed
        pages.append(page)
        return page

    yield make
    for page in pages:
        page.shutdown()
        page.close()


def finish(page):
    """Wait for the export thread, then deliver its signals."""
    if page.thread is not None:
        page.thread.wait()
    QApplication.processEvents()


def keep_first(conn, game, count=1):
    triage = load_triage(conn, game)
    for clip in triage.clips[:count]:
        save_triage(conn, replace(clip, status="kept"))


def test_with_nothing_kept_there_is_nothing_to_export(make_page, conn, tmp_path):
    page = make_page()
    page.set_game(add_game_with_clips(conn, tmp_path))

    assert page.summary.text() == "No kept clips yet. Keep some on the Triage tab (Alt+4) first."
    assert not page.export_button.isEnabled()
    assert page.last.text() == "Not exported yet."
    page.start()  # ignored
    assert page.thread is None


def test_export_saves_the_video_and_records_it(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game, 2)
    page.set_game(game)

    assert page.summary.text().splitlines() == [
        "2 kept clips from every camera in the order they happened, after a title card: "
        "0:00:33 of 1080p60 video.",  # 3 s of title, 20 s from A and 10 s from B
        "1 clip of our goalie's is still undecided and will be left out.",
    ]
    assert page.export_button.isEnabled()

    page.start()
    assert page.thread is not None and not page.export_button.isEnabled()
    finish(page)

    video = tmp_path / "Videos" / "Game Vids" / "vs Lafayette - DZone.mp4"
    assert video.exists()
    assert page.status.text().startswith("Saved vs Lafayette - DZone.mp4: 0:00:33, 2 MB, made in ")
    (export,) = load_exports(conn, game.id)
    assert (export.path, export.clips, export.duration) == (video, 2, 33.0)
    assert page.last.text().endswith(f": 2 clips in 0:00:33, to {video}")
    assert page.export_button.isEnabled()
    assert page.bar.isHidden() and page.stop_button.isHidden()


def test_cancelling_the_save_dialog_exports_nothing(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    page.set_game(game)
    page.ask_path = lambda default: None

    page.start()

    assert page.thread is None and load_exports(conn, game.id) == []


def test_a_failed_export_says_why(make_page, conn, tmp_path):
    def broken(*_, **__):
        raise ExportError("footage not found: /gone.MP4")

    page = make_page(broken)
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    page.set_game(game)

    page.start()
    finish(page)

    assert page.status.text() == "Export failed: footage not found: /gone.MP4"
    assert load_exports(conn, game.id) == []


def test_stop_ends_the_export_and_saves_nothing(make_page, conn, tmp_path):
    started = threading.Event()

    def endless(title, shots, output, progress=None, **_):
        while True:
            progress(1.0, 10.0)
            started.set()

    page = make_page(endless)
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    page.set_game(game)

    page.start()
    assert started.wait(5)
    page.stop()
    finish(page)

    assert page.thread is None
    assert page.status.text() == "Export stopped; nothing was saved."
    assert load_exports(conn, game.id) == []


def test_another_game_waits_while_one_exports(make_page, conn, tmp_path):
    release = threading.Event()

    def waiting(*args, **kwargs):
        release.wait(5)
        return fake_export(*args, **kwargs)

    page = make_page(waiting)
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    other_folder = tmp_path / "other"
    other_folder.mkdir()
    other = add_game_with_clips(conn, other_folder)
    keep_first(conn, other)
    page.set_game(game)
    page.start()

    page.set_game(other)
    assert page.status.text() == "Exporting vs Lafayette; one export runs at a time."
    assert not page.export_button.isEnabled() and page.bar.isHidden()

    release.set()
    finish(page)
    assert page.status.text() == ""  # the outcome belongs to the first game
    assert page.export_button.isEnabled()
    page.set_game(game)
    assert page.status.text().startswith("Saved vs Lafayette - DZone.mp4")


def test_a_finished_export_opens_its_folder(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    page.set_game(game)

    page.start()
    finish(page)
    assert [p.parent for p in page.revealed] == [tmp_path / "Videos" / "Game Vids"]


def test_a_failed_export_opens_nothing(make_page, conn, tmp_path):
    def failing(*args, **kwargs):
        raise ExportError("no space")

    page = make_page(failing)
    game = add_game_with_clips(conn, tmp_path)
    keep_first(conn, game)
    page.set_game(game)

    page.start()
    finish(page)
    assert page.revealed == []
