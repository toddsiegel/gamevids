"""The D-Zones page, its polygon canvas and the background scan queue, run headless."""

import os
import threading
from contextlib import closing
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import GAME, make_info, make_play_curve  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gamevids import db  # noqa: E402
from gamevids.activity import ScanError, load_curve  # noqa: E402
from gamevids.detect import load_clips  # noqa: E402
from gamevids.games import add_game, load_game  # noqa: E402
from gamevids.grouping import group_files  # noqa: E402
from gamevids.zones import DEFAULT_ZONE, load_zone, save_zone  # noqa: E402
from gamevids_ui.scans import ScanJob, ScanQueue  # noqa: E402
from gamevids_ui.zone_canvas import MARGIN, ZoneCanvas  # noqa: E402
from gamevids_ui.zones_page import ZonesPage  # noqa: E402

SQUARE = ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75))
LOW = ((0, 0.6), (1, 0.6), (1, 1), (0, 1))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(condition, timeout_ms=5000):
    for _ in range(timeout_ms // 10):
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("timed out")


# -- canvas -------------------------------------------------------------------


@pytest.fixture
def canvas(app):
    widget = ZoneCanvas()
    widget.resize(1600 + 2 * MARGIN, 900 + 2 * MARGIN)  # the frame is 1600 x 900 pixels
    widget.set_polygon(SQUARE)
    widget.show()
    yield widget
    widget.close()


def at(x, y):
    return QPoint(round(MARGIN + x * 1600), round(MARGIN + y * 900))


def test_canvas_keeps_the_frame_shape_and_maps_fractions(canvas):
    assert canvas.to_frame(QPointF(MARGIN + 400, MARGIN + 225)) == (0.25, 0.25)
    canvas.resize(1600 + 2 * MARGIN, 1000 + 2 * MARGIN)  # taller than 16:9: bands of 50 px
    assert canvas.frame_rect().top() == MARGIN + 50
    assert canvas.to_widget((0.5, 0.0)) == QPointF(MARGIN + 800, MARGIN + 50)
    assert canvas.to_frame(QPointF(-30, 2000)) == (0.0, 1.0)  # clamped to the frame


def test_dragging_a_corner_moves_it_and_stops_at_the_frame_edge(canvas):
    edits = []
    canvas.changed.connect(lambda: edits.append(canvas.polygon()))

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=at(0.75, 0.75))
    QTest.mouseMove(canvas, at(0.9, 0.95))
    QTest.mouseMove(canvas, QPoint(1620, 920))  # past the frame's corner
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(1620, 920))

    assert canvas.polygon()[2] == (1.0, 1.0)
    assert edits
    canvas.undo()
    assert canvas.polygon() == SQUARE


def test_double_clicking_an_edge_adds_a_corner_and_right_click_removes_one(canvas):
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.5, 0.25))
    assert canvas.polygon()[:3] == ((0.25, 0.25), (0.5, 0.25), (0.75, 0.25))

    QTest.mouseClick(canvas, Qt.MouseButton.RightButton, pos=at(0.5, 0.25))
    assert canvas.polygon() == SQUARE
    QTest.mouseClick(canvas, Qt.MouseButton.RightButton, pos=at(0.25, 0.25))
    assert len(canvas.polygon()) == 3
    QTest.mouseClick(canvas, Qt.MouseButton.RightButton, pos=at(0.75, 0.25))
    assert len(canvas.polygon()) == 3  # a zone keeps at least 3 corners

    canvas.undo()
    assert canvas.polygon() == SQUARE


def test_redrawing_places_corners_until_the_first_is_clicked(canvas):
    canvas.start_drawing()
    assert canvas.drawing and canvas.polygon() == ()
    for x, y in [(0.1, 0.5), (0.9, 0.5), (0.9, 0.9)]:
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(x, y))
    assert canvas.drawing
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.1, 0.5))

    assert not canvas.drawing
    assert canvas.polygon() == ((0.1, 0.5), (0.9, 0.5), (0.9, 0.9))
    canvas.undo()
    assert canvas.polygon() == SQUARE


def test_escape_gives_up_drawing_and_enter_finishes_it(canvas):
    canvas.start_drawing()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.1, 0.5))
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    assert not canvas.drawing
    assert canvas.polygon() == SQUARE

    canvas.start_drawing()
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.1, 0.5))
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.9, 0.5))
    QTest.keyClick(canvas, Qt.Key.Key_Return)
    assert canvas.drawing  # two corners aren't a zone yet
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=at(0.5, 0.9))
    QTest.keyClick(canvas, Qt.Key.Key_Return)
    assert canvas.polygon() == ((0.1, 0.5), (0.9, 0.5), (0.5, 0.9))


# -- scan queue -----------------------------------------------------------------


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


def add_two_camera_game(conn, tmp_path, title="vs Lafayette", day="2026-10-24"):
    """A game whose footage files exist (empty), so the page treats them as found."""
    infos = []
    for name, serial in [("a.MP4", "CAMA"), ("b.MP4", "CAMB")]:
        info = make_info(f"{title}-{name}", serial=serial, duration=3200)
        path = tmp_path / info.path.name
        path.touch()
        infos.append(replace(info, path=path))
    game_id = add_game(conn, title, group_files(infos), date.fromisoformat(day))
    return load_game(conn, game_id)


class FakeScan:
    """Stands in for `scan_camera`: reports progress, then returns a played game's curve.

    Each call blocks until `release()` when `hold` is set, so tests can look
    at a scan while it runs.
    """

    def __init__(self, *, hold=False, fail=None):
        self.calls = []
        self.fail = fail
        self.gate = threading.Event()
        if not hold:
            self.gate.set()

    def release(self):
        self.gate.set()

    def __call__(self, camera, polygon, *, progress):
        self.calls.append((camera.label, polygon))
        progress(0.0, camera.duration)
        while not self.gate.wait(0.01):
            progress(1.0, camera.duration)  # lets a stop request through
        if self.fail:
            raise ScanError(self.fail)
        progress(camera.duration, camera.duration)
        return replace(make_play_curve(0, GAME), polygon=polygon)


def job(game, label, polygon=LOW):
    camera = game.camera(label)
    return ScanJob(game.camera_ids[label], game.id, game.title, camera, polygon)


def test_scans_run_one_at_a_time_and_save_curves_and_clips(app, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    scan = FakeScan(hold=True)
    queue = ScanQueue(conn, scan=scan)
    finished = []
    queue.finished.connect(finished.append)

    queue.add(job(game, "A"))
    queue.add(job(game, "B", SQUARE))
    wait_for(lambda: len(scan.calls) == 1)
    assert queue.running.label == "A"
    assert [j.label for j in queue.waiting] == ["B"]

    scan.release()
    wait_for(lambda: not queue.busy)

    assert finished == [game.camera_ids["A"], game.camera_ids["B"]]
    assert scan.calls == [("A", LOW), ("B", SQUARE)]
    curve = load_curve(conn, game.camera_ids["B"])
    assert curve is not None and curve.polygon == SQUARE
    result = queue.results[game.camera_ids["B"]]
    assert result.error is None
    assert result.clips == len(load_clips(conn, game.camera_ids["B"])) > 0


def test_stopping_a_scan_saves_nothing_and_moves_on(app, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    scan = FakeScan(hold=True)
    queue = ScanQueue(conn, scan=scan)
    queue.add(job(game, "A"))
    queue.add(job(game, "B"))
    wait_for(lambda: len(scan.calls) == 1)

    queue.cancel(game.camera_ids["A"])
    scan.release()
    wait_for(lambda: not queue.busy)

    assert queue.results[game.camera_ids["A"]].stopped
    assert load_curve(conn, game.camera_ids["A"]) is None
    assert load_curve(conn, game.camera_ids["B"]) is not None


def test_a_failed_scan_is_reported(app, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    queue = ScanQueue(conn, scan=FakeScan(fail="ffmpeg is not installed"))

    queue.add(job(game, "A"))
    wait_for(lambda: not queue.busy)

    assert queue.results[game.camera_ids["A"]].error == "ffmpeg is not installed"
    assert load_curve(conn, game.camera_ids["A"]) is None


# -- the page -----------------------------------------------------------------


def grey_frame(path, at, width, height):
    return np.full((height, width, 3), 128, dtype=np.uint8)


@pytest.fixture
def page(app, conn):
    scan = FakeScan(hold=True)
    queue = ScanQueue(conn, scan=scan)
    widget = ZonesPage(conn, queue, grab=grey_frame)
    widget.scan = scan
    widget.resize(1200, 800)
    widget.show()
    yield widget
    scan.release()
    queue.stop_all()
    widget.shutdown()
    widget.close()


def test_a_new_game_starts_from_the_nearest_games_zones(page, conn, tmp_path):
    before = add_two_camera_game(conn, tmp_path, "vs Lafayette", "2026-10-17")
    save_zone(conn, before.camera_ids["A"], LOW)
    game = add_two_camera_game(conn, tmp_path, "vs Hyland Hills", "2026-10-24")

    page.set_game(game)

    assert page.current.label == "A"
    assert page.canvas.polygon() == LOW
    assert page.source_label.text() == "From vs Lafayette, camera A: check it fits, then scan."
    wait_for(lambda: page.canvas.image is not None)
    assert page.canvas.image.size().toTuple() == (1280, 720)
    assert page.save_button.isEnabled()
    assert page.problem.text() == "Not saved yet."

    page.show_camera(1)  # camera B had no zone there, so it starts from A's too
    assert page.canvas.polygon() == LOW


def test_the_first_game_starts_from_a_default_shape(page, conn, tmp_path):
    page.set_game(add_two_camera_game(conn, tmp_path))

    assert page.canvas.polygon() == DEFAULT_ZONE
    assert page.source_label.text().startswith("A starting shape")


def test_saving_starts_the_scan_and_moves_to_the_next_camera(page, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)
    a, b = game.camera_ids["A"], game.camera_ids["B"]

    page.canvas.set_polygon(SQUARE)
    page.save_and_scan()

    assert load_zone(conn, a) == SQUARE
    assert page.current.label == "B"  # drawn while A scans
    wait_for(lambda: page.scans.running is not None)
    _, bar, text, stop = page.scan_rows[a]
    assert text.text().startswith("Scanning: 0:00:")
    assert stop.isVisibleTo(page)

    page.save_and_scan()
    assert page.scan_rows[b][2].text() == "Waiting for the scan before it"

    page.scan.release()
    wait_for(lambda: not page.scans.busy)
    assert page.scan_rows[a][2].text().startswith("Scanned ")
    assert "candidate" not in page.scan_rows[a][2].text()
    assert " clips" in page.scan_rows[a][2].text()
    assert not page.scan_rows[a][3].isVisibleTo(page)

    # Back on A: scanned with this zone, so there's nothing to scan.
    page.show_camera(0)
    assert page.canvas.polygon() == SQUARE
    assert page.source_label.text() == "Saved zone."
    assert not page.save_button.isEnabled()
    page.canvas.set_polygon(LOW)
    page._canvas_changed()
    assert load_zone(conn, a) == LOW
    assert page.save_button.isEnabled()
    assert "the zone has changed since; scan again" in page.scan_rows[a][2].text()
    page.reset()  # back to the zone it was scanned with
    assert page.canvas.polygon() == SQUARE
    assert load_zone(conn, a) == SQUARE
    assert not page.save_button.isEnabled()


def test_edits_are_saved_and_survive_switching_games(page, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path, "vs Lafayette", "2026-10-17")
    other = add_two_camera_game(conn, tmp_path, "vs Hyland Hills", "2026-10-24")
    page.set_game(game)
    page.canvas.set_polygon(SQUARE)
    page._canvas_changed()

    assert load_zone(conn, game.camera_ids["A"]) == SQUARE
    assert page.source_label.text() == "Saved zone."
    assert page.problem.text() == ""
    assert page.save_button.isEnabled()  # saved, but not scanned yet

    page.set_game(other)
    page.set_game(load_game(conn, game.id))

    assert page.current.label == "A"  # still to be scanned
    assert page.canvas.polygon() == SQUARE
    assert page.source_label.text() == "Saved zone."


def test_a_drag_is_saved_once_its_let_go(page, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)
    page.canvas.set_polygon(SQUARE)
    page._canvas_changed()
    canvas, a = page.canvas, game.camera_ids["A"]
    frame = canvas.frame_rect()

    def to(x, y):
        return QPoint(
            round(frame.left() + x * frame.width()), round(frame.top() + y * frame.height())
        )

    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=to(0.75, 0.75))
    QTest.mouseMove(canvas, to(0.8, 0.9))
    assert load_zone(conn, a) == SQUARE
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=to(0.8, 0.9))

    moved = load_zone(conn, a)
    assert moved != SQUARE and moved == canvas.polygon()


def test_a_redraw_is_saved_once_its_finished_and_a_bad_zone_isnt(page, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    page.set_game(game)
    a = game.camera_ids["A"]

    page.canvas.start_drawing()
    for corner in LOW[:3]:
        page.canvas.points.append(corner)
        page._canvas_changed()
    assert load_zone(conn, a) is None
    page.canvas.points.append(LOW[3])
    page.canvas.finish_drawing()
    assert load_zone(conn, a) == LOW

    page.canvas.set_polygon(((0, 0), (0.5, 0.5), (1, 1)))  # all on one line
    page._canvas_changed()
    assert load_zone(conn, a) == LOW
    assert page.problem.text().startswith("The zone can't be saved")
    assert not page.save_button.isEnabled()


def test_missing_footage_cant_be_scanned(page, conn, tmp_path):
    game = add_two_camera_game(conn, tmp_path)
    game.camera("A").chapters[0].file.path.unlink()
    page.set_game(game)

    assert "Can't find camera A's footage" in page.canvas.message
    assert not page.save_button.isEnabled()
    assert "missing" in page.problem.text()
