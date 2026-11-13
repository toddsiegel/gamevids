"""The Periods page and its activity timeline, run headless."""

import os
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import GAME, GAME_START, make_info, make_play_curve  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gamevids import db  # noqa: E402
from gamevids.activity import save_curve  # noqa: E402
from gamevids.games import add_game, load_game  # noqa: E402
from gamevids.grouping import group_files  # noqa: E402
from gamevids.periods import Period, load_periods, propose_periods  # noqa: E402
from gamevids_ui.period_timeline import LABEL_WIDTH, RIGHT, Lane, PeriodTimeline  # noqa: E402

FOOTAGE = (GAME_START, GAME_START + timedelta(seconds=3200))


def at(seconds):
    return GAME_START + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- timeline -----------------------------------------------------------------


@pytest.fixture
def timeline(app):
    widget = PeriodTimeline()
    lanes = [
        Lane("A", make_play_curve(0, GAME), [FOOTAGE]),
        Lane("B", None, [(at(100), at(3200))]),
    ]
    widget.set_lanes(lanes, FOOTAGE)
    widget.set_periods(
        [Period(1, at(320)), Period(2, at(1280)), Period(3, at(2240))], {1: "A", 3: "A"}, None
    )
    widget.resize(LABEL_WIDTH + RIGHT + 1000, widget.sizeHint().height())  # 3.2 s a pixel
    widget.show()
    yield widget
    widget.close()


def x_at(seconds):
    return round(LABEL_WIDTH + seconds / 3.2)


def lane_y(widget, index):
    return round(widget.lane_rect(index).center().y())


def test_timeline_maps_pixels_to_moments_and_finds_markers(timeline):
    assert timeline.x_for(at(1600)) == LABEL_WIDTH + 500
    assert timeline.moment_at(LABEL_WIDTH + 250) == at(800)
    assert timeline.moment_at(-40) == at(0)  # clamped to the footage
    assert timeline.marker_at(x_at(1280) + 4) == 2
    assert timeline.marker_at(x_at(1280) + 20) is None
    assert timeline.lane_at(lane_y(timeline, 1)) == "B"
    timeline.grab()  # paints without error, including the unscanned lane


def test_dragging_a_marker_stops_short_of_its_neighbours(timeline):
    drags = []
    timeline.start_dragged.connect(lambda n, moment, done: drags.append((n, moment, done)))
    y = lane_y(timeline, 0)

    QTest.mousePress(timeline, Qt.MouseButton.LeftButton, pos=QPoint(x_at(1280), y))
    QTest.mouseMove(timeline, QPoint(x_at(1600), y))
    QTest.mouseMove(timeline, QPoint(x_at(3000), y))  # past P3
    QTest.mouseRelease(timeline, Qt.MouseButton.LeftButton, pos=QPoint(x_at(3000), y))

    assert drags[0] == (2, at(1600), False)
    assert drags[-1] == (2, at(2239), True)  # a second before P3
    assert all(not done for *_, done in drags[:-1])


def test_clicking_a_marker_picks_it_and_clicking_a_lane_seeks_that_camera(timeline):
    picked, seeks, drags = [], [], []
    timeline.period_picked.connect(picked.append)
    timeline.seek_requested.connect(lambda label, moment, exact: seeks.append((label, moment)))
    timeline.start_dragged.connect(lambda *args: drags.append(args))

    left = Qt.MouseButton.LeftButton
    QTest.mouseClick(timeline, left, pos=QPoint(x_at(2240) + 1, 8))  # P3's name above the lanes
    QTest.mouseClick(timeline, left, pos=QPoint(x_at(800), lane_y(timeline, 1)))

    assert picked == [3]
    assert drags == []  # a click that doesn't move doesn't drag
    assert seeks[-1] == ("B", at(800))


# -- page ---------------------------------------------------------------------


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


@pytest.fixture
def make_page(app, conn):
    try:
        import mpv  # noqa: F401
    except OSError:
        pytest.skip("libmpv not installed")
    from gamevids_ui.periods_page import PeriodsPage
    from gamevids_ui.scans import ScanQueue

    pages = []

    def make():
        queue = ScanQueue(conn)
        page = PeriodsPage(conn, queue)
        page.resize(1200, 900)
        pages.append(page)
        return page, queue

    yield make
    for page in pages:
        page.shutdown()
        page.close()


def add_two_camera_game(conn, tmp_path, *, scanned=("A", "B")):
    infos = []
    for name, serial in [("a.MP4", "CAMA"), ("b.MP4", "CAMB")]:
        info = make_info(name, serial=serial, duration=3200)
        path = tmp_path / name
        path.touch()
        infos.append(replace(info, path=path))
    game = load_game(conn, add_game(conn, "vs Lafayette", group_files(infos), date(2026, 10, 24)))
    for index, label in enumerate(scanned):
        save_curve(conn, game.camera_ids[label], make_play_curve(index, GAME))
    return game


def offsets(periods):
    return [(p.start - GAME_START).total_seconds() for p in periods]


# Where play resumes after each quiet spell, less the proposal's 10 s lead.
PROPOSED = [
    (start - GAME_START).total_seconds()
    for start in propose_periods([make_play_curve(0, GAME), make_play_curve(1, GAME)]).starts
]


def test_a_scanned_game_gets_periods_proposed_and_saved(make_page, conn, tmp_path):
    page, _ = make_page()
    game = add_two_camera_game(conn, tmp_path)

    page.set_game(game)

    assert offsets(load_periods(conn, game.id)) == PROPOSED
    assert [round(t, -1) for t in PROPOSED] == [290, 1280, 2280]  # play resumes 10 s later
    assert page.table.rowCount() == 3
    assert page.table.item(1, 5).text().startswith("quiet ")
    assert "Pick the camera behind our goalie's net" in page.message.text()


def test_periods_are_proposed_once_the_last_scan_finishes(make_page, conn, tmp_path):
    page, queue = make_page()
    game = add_two_camera_game(conn, tmp_path, scanned=("A",))

    page.set_game(game)
    assert load_periods(conn, game.id) == []
    assert "Camera B hasn’t been scanned" in page.message.text()
    assert not page.propose_button.isEnabled()

    save_curve(conn, game.camera_ids["B"], make_play_curve(1, GAME))
    queue.finished.emit(game.camera_ids["B"])

    assert len(load_periods(conn, game.id)) == 3
    assert page.propose_button.isEnabled()


def test_p1s_goalie_alternates_and_a_later_period_can_differ(make_page, conn, tmp_path):
    page, _ = make_page()
    game = add_two_camera_game(conn, tmp_path)
    a, b = game.camera_ids["A"], game.camera_ids["B"]
    page.set_game(game)

    page.table.cellWidget(0, 4).setCurrentIndex(1)  # P1: camera B
    assert [p.goalie_camera_id for p in load_periods(conn, game.id)] == [b, a, b]

    page.table.cellWidget(2, 4).setCurrentIndex(2)  # P3: not set
    assert [p.goalie_camera_id for p in load_periods(conn, game.id)] == [b, a, None]


def test_dragging_a_marker_saves_its_start_and_undo_puts_it_back(make_page, conn, tmp_path):
    page, _ = make_page()
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)

    page.timeline.start_dragged.emit(2, at(1300), False)
    assert offsets(load_periods(conn, game.id))[1] == PROPOSED[1]  # saved only on release
    page.timeline.start_dragged.emit(2, at(1310), True)

    saved = load_periods(conn, game.id)
    assert offsets(saved)[1] == 1310 and saved[1].moved
    assert page.selected == 2
    assert page.table.item(1, 5).text() == "set by hand"

    page.undo()
    assert offsets(load_periods(conn, game.id)) == PROPOSED
    assert not page.undo_button.isEnabled()


def test_proposing_again_replaces_a_start_set_by_hand(make_page, conn, tmp_path):
    page, _ = make_page()
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)
    page.timeline.start_dragged.emit(3, at(2400), True)

    page.propose_again()

    assert offsets(load_periods(conn, game.id)) == PROPOSED
    assert "Ctrl+Z puts back" in page.message.text()


def test_periods_can_be_added_moved_to_the_playhead_and_removed(make_page, conn, tmp_path):
    page, _ = make_page()
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)
    page.player.wall_clock = lambda: at(3000)

    page.add_period()  # overtime, at the playhead
    assert offsets(load_periods(conn, game.id)) == [*PROPOSED, 3000]
    assert page.table.item(3, 0).text() == "OT"
    assert page.selected == 4

    page.player.wall_clock = lambda: at(1000)
    page.start_here()  # before P3: refused
    assert "OT must start after P3" in page.message.text()
    page.pick(2)
    page.start_here()
    assert offsets(load_periods(conn, game.id))[1] == 1000

    page.remove_last()
    assert len(load_periods(conn, game.id)) == 3
    assert page.selected == 2
