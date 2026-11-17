"""The Triage page and its clip bar, run headless."""

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
from gamevids.detect import DEFAULT_SETTINGS, Segment, save_clips  # noqa: E402
from gamevids.games import add_game, load_game  # noqa: E402
from gamevids.grouping import group_files  # noqa: E402
from gamevids.periods import Period, save_periods  # noqa: E402
from gamevids.triage import load_triage  # noqa: E402
from gamevids_ui.clip_bar import MARGIN, ClipBar  # noqa: E402


def at(seconds):
    return GAME_START + timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- clip bar -----------------------------------------------------------------


def test_clip_bar_maps_pixels_to_moments_and_seeks(app):
    bar = ClipBar()
    bar.resize(2 * MARGIN + 400, 32)  # 40 s of reach, a tenth of a second a pixel
    bar.set_clip((at(90), at(130)), (at(100), at(120)), (at(98), at(115)))
    seeks = []
    bar.seek_requested.connect(lambda moment, exact: seeks.append((moment, exact)))

    assert bar.x_for(at(110)) == MARGIN + 200
    assert bar.moment_at(MARGIN + 50) == at(95)
    assert bar.moment_at(-10) == at(90)  # clamped to the reach
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 100, 16))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(MARGIN + 300, 16))

    assert seeks == [(at(100), False), (at(120), True)]
    bar.grab()  # paints without error
    bar.set_clip(None, None, None)
    bar.grab()


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
    from gamevids_ui.triage_page import TriagePage

    pages = []

    def make():
        page = TriagePage(conn)
        page.resize(1200, 800)
        pages.append(page)
        return page

    yield make
    for page in pages:
        page.shutdown()
        page.close()


def add_game_with_clips(conn, tmp_path, goalies=("A", "B", "A")):
    """Cameras A and B, each with clips, and P1-P3 at 300, 1290 and 2290 s."""
    infos = []
    for name, serial in [("a.MP4", "CAMA"), ("b.MP4", "CAMB")]:
        path = tmp_path / name
        path.touch()
        infos.append(replace(make_info(name, serial=serial, duration=3200), path=path))
    game = load_game(conn, add_game(conn, "vs Lafayette", group_files(infos), date(2026, 10, 24)))
    a, b = game.camera_ids["A"], game.camera_ids["B"]
    curve = make_play_curve(0, GAME)
    clips = {a: [(100, 110), (400, 420), (1500, 1510), (2500, 2520)], b: [(1400, 1410)]}
    for camera_id, spans in clips.items():
        segments = [Segment(at(s), at(e), 0.1) for s, e in spans]
        save_clips(conn, camera_id, segments, DEFAULT_SETTINGS, curve)
    ids = [game.camera_ids[g] if g else None for g in goalies]
    starts = [300, 1290, 2290]
    periods = [
        Period(n, at(s), i) for n, (s, i) in enumerate(zip(starts, ids, strict=True), start=1)
    ]
    save_periods(conn, game.id, periods)
    return game


def statuses(conn, game):
    return [(c.status, c.goal_against) for c in load_triage(conn, game).clips]


def test_opens_at_our_goalies_first_clip(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)

    page.set_game(game)

    # P1 at A (400 s), P2 at B (1,400 s), P3 at A (2,500 s); the warmup clip is left out.
    assert page.table.rowCount() == 3
    assert [page.table.item(r, 2).text() for r in range(3)] == ["A", "B", "A"]
    assert page.table.selectedIndexes()[0].row() == 0
    assert page.player.label == "A"
    assert page.status.text().startswith("Clip 1 of 3 · P1 · Camera A · ")
    assert "Undecided" in page.status.text() and "Speed 2×" in page.status.text()
    assert page.bar.reach == (at(390), at(430))
    assert page.bar.kept == (at(400), at(420))


def test_keys_decide_and_triage_resumes_where_it_stopped(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)

    page.toggle_goal_against()  # marks, keeps and moves on, like Enter
    assert page.triage.index == 1
    assert page.player.label == "B"  # P2's clip is on camera B
    assert page.table.item(0, 5).text() == "Kept" and page.table.item(0, 6).text() == "GA"
    assert statuses(conn, game) == [("kept", True), ("candidate", False), ("candidate", False)]

    resumed = make_page()
    resumed.set_game(game)
    assert resumed.triage.index == 1
    assert resumed.status.text().startswith("Clip 2 of 3 · P2 · Camera B")

    resumed.discard()
    resumed.keep()
    assert resumed.clip is None
    assert "3 of 3 clips triaged: 2 kept" in resumed.player.message.text()
    resumed.keep()  # nothing left: ignored

    resumed.undo()
    assert resumed.triage.index == 2
    assert statuses(conn, game)[2] == ("candidate", False)


def test_up_and_down_move_between_clips(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)
    page.show()
    page.activateWindow()
    assert QTest.qWaitForWindowActive(page)  # shortcuts only fire in the active window

    QTest.keyClick(page, Qt.Key.Key_Up)  # at the first clip: stays
    assert page.triage.index == 0
    QTest.keyClick(page, Qt.Key.Key_Down)
    assert page.triage.index == 1
    assert page.player.label == "B"
    assert page.table.selectedIndexes()[0].row() == 1
    QTest.keyClick(page, Qt.Key.Key_Down)
    QTest.keyClick(page, Qt.Key.Key_Down)  # at the last clip: stays
    assert page.triage.index == 2

    page.keep()  # past the last clip, to the summary
    assert page.clip is None
    QTest.keyClick(page, Qt.Key.Key_Down)
    assert page.clip is None
    QTest.keyClick(page, Qt.Key.Key_Up)
    assert page.triage.index == 2
    assert statuses(conn, game) == [("candidate", False), ("candidate", False), ("kept", False)]


def test_clearing_a_status_and_all_of_them(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)
    page.toggle_goal_against()
    page.discard()
    asked = []

    page.clear_status()  # on clip 3, still undecided: nothing changes
    page.table.selectRow(0)
    page.clear_status()
    assert page.triage.index == 0
    assert page.table.item(0, 5).text() == "" and page.table.item(0, 6).text() == "GA"
    assert statuses(conn, game) == [("candidate", True), ("discarded", False), ("candidate", False)]

    page.confirm_clear_all = lambda decided: asked.append(decided) or False
    page.clear_all_statuses()
    assert statuses(conn, game)[1] == ("discarded", False)  # declined

    page.confirm_clear_all = lambda decided: asked.append(decided) or True
    page.table.selectRow(1)
    page.clear_all_statuses()
    assert asked == [1, 1]
    assert statuses(conn, game) == [("candidate", True), ("candidate", False), ("candidate", False)]
    assert page.triage.index == 0
    assert [page.table.item(r, 5).text() for r in range(3)] == ["", "", ""]
    page.clear_all_statuses()  # nothing decided: not even asked
    assert asked == [1, 1]

    page.undo()
    assert page.triage.index == 1
    assert page.table.item(1, 5).text() == "Discarded"


def test_the_clear_all_dialog_defaults_to_cancel(make_page):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    page = make_page()
    seen = []

    def answer(clear):
        box = QApplication.activeModalWidget()
        cancel = box.button(QMessageBox.StandardButton.Cancel)
        seen.append((box.text(), box.defaultButton() is cancel))
        roles = {box.buttonRole(b): b for b in box.buttons()}
        (roles[QMessageBox.ButtonRole.DestructiveRole] if clear else cancel).click()

    QTimer.singleShot(0, lambda: answer(clear=False))
    assert page.confirm_clear_all(3) is False
    QTimer.singleShot(0, lambda: answer(clear=True))
    assert page.confirm_clear_all(1) is True
    assert seen == [
        ("Clear the keep or discard decision on 3 clips?", True),
        ("Clear the keep or discard decision on 1 clip?", True),
    ]


def test_trimming_sets_the_in_and_out_points_at_the_playhead(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)

    page.player.wall_clock = lambda: at(395)
    page.set_in()
    page.player.wall_clock = lambda: at(425.5)
    page.set_out()
    assert page.bar.kept == (at(395), at(425.5))
    assert page.table.item(0, 4).text() == "0:00:30"
    clip = load_triage(conn, game).clips[0]
    assert (clip.trim_in, clip.trim_out, clip.status) == (at(395), at(425.5), "candidate")

    page.player.wall_clock = lambda: at(385)
    page.set_in()
    assert page.message.text() == "Can't: the clip can start at most 10 s before it was found."

    page.undo()
    assert page.bar.kept == (at(395), at(420))


def test_playback_pauses_at_the_out_point_and_space_replays(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)
    seeks = []
    page.player.show_moment = lambda moment, label=None, exact=True: seeks.append(moment)
    page.player.mpv.pause = False

    page._clock_changed("A", at(419.95))
    assert not page._ended
    page._clock_changed("A", at(420.03))  # a poll past the out point
    assert page._ended and page.player.mpv.pause
    assert seeks == [at(420)]

    page.toggle_pause()
    assert seeks[-1] == at(400) and not page.player.mpv.pause
    assert page.player.mpv.speed == 2.0

    # A seek across the out point, rather than playback, doesn't pause.
    page._seek(at(410), True)
    page._clock_changed("A", at(410))
    page._clock_changed("A", at(425))
    assert not page._ended and not page.player.mpv.pause


def test_speed_steps_through_the_presets(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path)
    page.set_game(game)

    page.change_speed(1)
    page.change_speed(1)
    page.change_speed(1)  # already the fastest
    assert page.speed == 4.0 and page.player.mpv.speed == 4.0
    page.change_speed(-1)
    assert "Speed 3×" in page.status.text()


@pytest.mark.parametrize(
    ("goalies", "message"),
    [
        ((None, None, None), "Pick the camera behind our goalie's net"),
        ((None, None, "B"), "No clips at our goalie's end."),
    ],
)
def test_says_why_there_is_nothing_to_triage(make_page, conn, tmp_path, goalies, message):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path, goalies)

    page.set_game(game)

    assert message in page.player.message.text()
    assert page.table.rowCount() == 0
    page.keep()  # ignored


def test_refresh_picks_up_a_changed_goalie_end_but_keeps_the_place(make_page, conn, tmp_path):
    page = make_page()
    game = add_game_with_clips(conn, tmp_path, (None, None, None))
    page.set_game(game)
    assert page.table.rowCount() == 0

    a = game.camera_ids["A"]
    periods = [Period(n, at(s), a) for n, s in [(1, 300), (2, 1290), (3, 2290)]]
    save_periods(conn, game.id, periods)
    page.refresh()
    assert page.table.rowCount() == 3  # A's clips in all three periods

    page.keep()
    history = page.triage.history
    page.refresh()  # nothing changed
    assert page.triage.index == 1 and page.triage.history is history
