"""The Qt app, run headless. Tests that need libmpv skip when it isn't installed."""

import os
from contextlib import closing
from datetime import date

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import FULL_CHAPTER, make_info  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gamevids import db  # noqa: E402
from gamevids.games import add_game  # noqa: E402
from gamevids.grouping import group_files  # noqa: E402
from gamevids_ui.scrubber import MARGIN, Scrubber  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window_class(app):
    try:
        import mpv  # noqa: F401
    except OSError:
        pytest.skip("libmpv not installed")
    from gamevids_ui.window import MainWindow

    windows = []

    def make(conn):
        window = MainWindow(conn)
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.close()


@pytest.fixture
def scrubber(app):
    widget = Scrubber()
    widget.resize(2 * MARGIN + 1000, 28)
    widget.set_timeline(4000.0, [2500.0], [3500.0])
    return widget


def test_scrubber_maps_pixels_to_positions(scrubber):
    assert scrubber.position_at(MARGIN) == 0
    assert scrubber.position_at(MARGIN + 250) == 1000
    assert scrubber.position_at(-50) == 0  # clamped at both ends
    assert scrubber.position_at(5000) == 4000
    assert scrubber.x_for(2000) == MARGIN + 500


def test_dragging_seeks_to_keyframes_then_exactly_on_release(scrubber):
    seeks = []
    scrubber.seek_requested.connect(lambda position, exact: seeks.append((position, exact)))
    y = scrubber.height() // 2

    QTest.mousePress(scrubber, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 100, y))
    QTest.mouseMove(scrubber, QPoint(MARGIN + 200, y))
    QTest.mouseRelease(scrubber, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 200, y))

    assert seeks[0] == (400, False)
    assert seeks[-1] == (800, True)
    assert all(not exact for _, exact in seeks[:-1])


def test_scrubber_ignores_playback_while_dragging(scrubber):
    y = scrubber.height() // 2
    QTest.mousePress(scrubber, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 100, y))
    scrubber.set_position(3000)
    assert scrubber.position == 400
    QTest.mouseRelease(scrubber, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 100, y))
    scrubber.set_position(3000)
    assert scrubber.position == 3000


def test_precise_time_shows_milliseconds(window_class):
    from gamevids_ui.player import precise

    assert precise(2522.953767) == "0:42:02.954"
    assert precise(59.9996) == "0:01:00.000"


def test_window_lists_games_newest_first(window_class, library):
    with closing(db.connect(library)) as conn:
        for day, title in [("2026-10-17", "vs Lafayette"), ("2026-10-24", "vs Hyland Hills")]:
            grouping = group_files([make_info(f"{title}.MP4")])
            add_game(conn, title, grouping, game_date=date.fromisoformat(day))

        window = window_class(conn)

        items = [window.games.item(i).text() for i in range(window.games.count())]
        assert items[0].startswith("vs Hyland Hills\n2026-10-24 · 1 camera · 0:10:00")
        assert items[1].startswith("vs Lafayette\n")


def test_picking_a_game_with_missing_footage_says_so(window_class, library):
    with closing(db.connect(library)) as conn:
        files = [
            make_info("DJI_20261024115345_0001_D.MP4", duration=100, size=FULL_CHAPTER),
            make_info("DJI_20261024115545_0002_D.MP4", start=100),
        ]
        add_game(conn, "vs Lafayette", group_files(files))
        window = window_class(conn)

        window.games.setCurrentRow(0)

        player = window.player
        assert player.camera_box.currentText() == "A"
        assert player.timeline is None
        assert "Can't find camera A's footage" in player.message.text()
        assert "/footage/DJI_20261024115345_0001_D.MP4" in player.message.text()
        assert window.windowTitle() == "vs Lafayette — Game Vids"


def test_empty_library_says_how_to_add_a_game(window_class, library):
    with closing(db.connect(library)) as conn:
        window = window_class(conn)
        assert "gamevids game add" in window.player.message.text()
