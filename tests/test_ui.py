"""The Qt app, run headless. Tests that need libmpv skip when it isn't installed."""

import os
from contextlib import closing
from datetime import date, timedelta

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import FULL_CHAPTER, GAME_START, make_info  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gamevids import db  # noqa: E402
from gamevids.games import add_game, load_game  # noqa: E402
from gamevids.grouping import group_files  # noqa: E402
from gamevids.probe import ProbeError  # noqa: E402
from gamevids_ui.add_game import AddGameDialog  # noqa: E402
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


def test_a_player_without_controls_hides_them_and_binds_no_keys(window_class):
    from PySide6.QtGui import QShortcut

    from gamevids_ui.player import Player

    bare, full = Player(controls=False), Player()
    try:
        assert bare.controls.isHidden() and not full.controls.isHidden()
        assert bare.findChildren(QShortcut) == []
        assert len(full.findChildren(QShortcut)) == 10
    finally:
        for player in (bare, full):
            player.shutdown()


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
        assert "Add Game" in window.player.message.text()


# -- Add Game dialog ----------------------------------------------------------


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


@pytest.fixture
def dialog(app, conn):
    """An Add Game dialog whose probe looks files up in `dialog.files` by name."""
    files = {}

    def probe(path):
        if path.name not in files:
            raise ProbeError(f"{path}: Invalid data found when processing input")
        return files[path.name]

    widget = AddGameDialog(conn, probe=probe)
    widget.files = files
    yield widget
    widget.close()


def pick(dialog, *infos, extra=()):
    """Add `infos` (and `extra` unreadable paths) to the dialog and wait for probing."""
    for info in infos:
        dialog.files[info.path.name] = info
    dialog.add_paths([info.path for info in infos] + list(extra))
    for _ in range(500):
        if not dialog.busy:
            break
        QTest.qWait(10)
    assert not dialog.busy


def tree_rows(dialog):
    rows = []
    for i in range(dialog.tree.topLevelItemCount()):
        top = dialog.tree.topLevelItem(i)
        rows.append(top.text(0))
        rows += [
            f"  {top.child(j).text(0)}  {top.child(j).text(5)}".rstrip()
            for j in range(top.childCount())
        ]
    return rows


def test_dialog_groups_files_and_prefills_title_and_date(dialog, conn, denver):
    pick(
        dialog,
        make_info("DJI_20261024115345_0001_D.MP4", duration=2522.95, size=FULL_CHAPTER),
        make_info("DJI_20261024123549_0002_D.MP4", start=2524.0),
        make_info("1st Period - D Zone.mp4", serial=None),
    )

    assert tree_rows(dialog) == [
        "Camera A · CAMA",
        "  DJI_20261024115345_0001_D.MP4",
        "  DJI_20261024123549_0002_D.MP4  next chapter",
        "D-zone exports, skipped",
        "  1st Period - D Zone.mp4",
    ]
    assert not dialog.loose_box.isVisibleTo(dialog)
    assert dialog.title_edit.text() == "footage"  # the folder's name
    assert dialog.date_edit.date().toPython() == date(2026, 10, 24)
    assert dialog.add_button.isEnabled()

    dialog.title_edit.setText("vs Hyland Hills")
    dialog.add_button.click()

    game = load_game(conn, dialog.game_id)
    assert (game.title, game.date) == ("vs Hyland Hills", date(2026, 10, 24))
    assert len(game.camera("A").chapters) == 2


def test_a_file_with_no_serial_must_be_placed_or_left_out(dialog, conn, denver):
    pick(
        dialog,
        make_info("G - 3rd Period.mp4", start=3000, timecode="09:55:04;18"),
        make_info("G - 1st Period.mp4", start=None, serial=None, timecode="09:05:51;006"),
    )
    (row,) = dialog.loose
    assert dialog.loose_box.isVisibleTo(dialog)
    assert not dialog.add_button.isEnabled()
    assert dialog.problem.text() == "Choose a camera for G - 1st Period.mp4, or leave it out."
    assert "No camera yet" in tree_rows(dialog)
    assert row.period.currentText() == "P1"  # from the name

    choices = [row.camera.itemText(i) for i in range(row.camera.count())]
    assert choices == ["Choose…", "A · CAMA", "B · new camera", "Leave out"]

    row.camera.setCurrentIndex(1)  # camera A: placed by its timecode
    assert row.start.time().toString("HH:mm:ss") == "11:54:33"
    assert "timecode" in row.note.text()
    row.period.setCurrentText("P2")
    assert tree_rows(dialog)[:2] == ["Camera A · CAMA", "  G - 1st Period.mp4  placed by hand"]
    assert dialog.add_button.isEnabled()

    dialog.add_button.click()
    (first, third) = load_game(conn, dialog.game_id).camera("A").chapters
    assert first.start == GAME_START + timedelta(seconds=3000 - 49 * 60 - 13)
    assert first.period == "2"


def test_a_new_camera_needs_a_start_time_typed_in(dialog, conn, denver):
    pick(
        dialog,
        make_info("a.MP4"),
        make_info("P2.mp4", start=None, serial=None, timecode="09:05:51;006"),
    )
    (row,) = dialog.loose

    row.camera.setCurrentIndex(2)  # B, a new camera: no timecode to go by
    assert dialog.problem.text() == "Set the start time of P2.mp4."
    assert row.note.text() == "enter the start time"

    row.start.setTime(row.start.time().fromString("12:30:00", "HH:mm:ss"))
    assert row.note.text() == "start set by hand"
    dialog.add_button.click()

    camera = load_game(conn, dialog.game_id).camera("B")
    assert camera.serial is None
    assert camera.start.astimezone().strftime("%Y-%m-%d %H:%M:%S") == "2026-10-24 12:30:00"


def test_left_out_files_are_not_saved(dialog, conn):
    pick(dialog, make_info("a.MP4"), make_info("b - 2nd Period.mp4", serial=None))
    dialog.loose[0].camera.setCurrentText("Leave out")

    assert "Left out" in tree_rows(dialog)
    dialog.add_button.click()
    assert [
        c.file.path.name for c in load_game(conn, dialog.game_id).grouping.cameras[0].chapters
    ] == ["a.MP4"]


def test_files_already_in_a_game_block_adding(dialog, conn):
    add_game(conn, "first", group_files([make_info("a.MP4")]))

    pick(dialog, make_info("a.MP4"))

    assert "already in game 1 (first)" in dialog.problem.text()
    assert not dialog.add_button.isEnabled()
    assert "Already in the library" in tree_rows(dialog)


def test_unreadable_files_are_warned_about_and_a_typed_title_is_kept(dialog, tmp_path):
    dialog.title_edit.setText("vs Lafayette")
    pick(dialog, make_info("a.MP4"), extra=[tmp_path / "notes.mp4"])

    assert "notes.mp4: Invalid data" in dialog.warnings.text()
    assert dialog.title_edit.text() == "vs Lafayette"
    assert dialog.add_button.isEnabled()


def test_an_empty_title_or_no_files_blocks_adding(dialog):
    assert dialog.problem.text() == "Add the game's video files, or its folder."
    pick(dialog, make_info("a.MP4"))
    dialog.title_edit.setText("  ")
    assert dialog.problem.text() == "Enter a title."
    assert not dialog.add_button.isEnabled()
