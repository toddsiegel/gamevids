from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import FULL_CHAPTER, GAME, GAME_START, make_info, make_play_curve

from gamevids import db
from gamevids.activity import save_curve
from gamevids.detect import DEFAULT_SETTINGS, Segment, load_clips, save_clips
from gamevids.export import ExportResult, save_export
from gamevids.games import (
    GameError,
    add_game,
    files_in_library,
    game_contents,
    list_games,
    load_game,
    remove_game,
    rename_game,
)
from gamevids.grouping import Assignment, assign_files, group_files
from gamevids.periods import Period, save_periods
from gamevids.report import removal_items, removal_note
from gamevids.triage import save_triage
from gamevids.zones import save_zone


@pytest.fixture
def conn(library):
    conn = db.connect(library)
    yield conn
    conn.close()


def two_camera_game(folder: str = "game"):
    return group_files(
        [
            make_info(f"{folder}/a1.MP4", duration=2522.95, size=FULL_CHAPTER),
            make_info(f"{folder}/a2.MP4", start=2524.0, duration=1402.0),
            make_info(f"{folder}/a3 - 3rd Period.mp4", start=5000.0),
            make_info(f"{folder}/b1.MP4", start=60.0, serial="CAMB"),
        ]
    )


def test_a_saved_game_loads_back_the_same(conn):
    grouping = two_camera_game()

    game = load_game(conn, add_game(conn, "  vs Lafayette ", grouping))

    assert game.title == "vs Lafayette"
    assert game.grouping.cameras == grouping.cameras
    a, b = game.grouping.cameras
    assert (a.label, a.serial, len(a.recordings)) == ("A", "CAMA", 2)
    assert a.chapters[1].start == GAME_START + timedelta(seconds=2522.95)
    assert a.chapters[2].period == "3"
    assert (b.label, b.serial) == ("B", "CAMB")


def test_date_comes_from_the_files_unless_given(conn):
    first = add_game(conn, "t", two_camera_game("one"))
    second = add_game(conn, "t", two_camera_game("two"), date(2026, 10, 25))

    assert load_game(conn, first).date == GAME_START.astimezone().date()
    assert load_game(conn, second).date == date(2026, 10, 25)


def test_a_file_can_only_belong_to_one_game(conn):
    add_game(conn, "first", two_camera_game())

    with pytest.raises(GameError, match=r"already in game 1 \(first\):\n  a1.MP4\n  a2.MP4"):
        add_game(conn, "again", two_camera_game())
    assert len(list_games(conn)) == 1


def test_files_in_library_names_each_files_game(conn):
    grouping = two_camera_game()
    add_game(conn, "first", grouping)
    a1 = grouping.cameras[0].chapters[0].file.path

    assert files_in_library(conn, [a1, a1.with_name("new.MP4")]) == {a1: (1, "first")}
    assert files_in_library(conn, []) == {}


def test_a_camera_made_by_hand_is_saved_with_its_timecodes(conn):
    loose = make_info("P1.mp4", start=None, serial=None, timecode="09:05:51;006")
    grouping = group_files([make_info("P3.mp4", start=3000), loose])
    start = GAME_START + timedelta(seconds=10)
    placed = assign_files(grouping, {loose.path: Assignment("B", "1", start)})

    game = load_game(conn, add_game(conn, "t", placed))

    camera = game.camera("B")
    assert camera.serial is None
    (chapter,) = camera.chapters
    assert (chapter.start, chapter.period) == (start, "1")
    assert chapter.file.creation_time is None
    assert chapter.file.timecode == "09:05:51;006"


@pytest.mark.parametrize(
    ("title", "files", "message"),
    [
        ("", [make_info("a.MP4")], "needs a title"),
        ("t", [make_info("a - 1st Period.mp4", serial=None)], "need a camera first"),
        ("t", [make_info("a - 1st Period - D Zone.mp4")], "no files with a camera serial"),
    ],
)
def test_add_game_rejects(conn, title, files, message):
    with pytest.raises(GameError, match=message):
        add_game(conn, title, group_files(files))


def test_list_games_summarises_each_game_in_id_order(conn):
    add_game(conn, "later", two_camera_game("one"), date(2026, 10, 25))
    add_game(conn, "earlier", group_files([make_info("two/a.MP4")]), date(2026, 10, 1))

    (later, earlier) = list_games(conn)

    assert (earlier.title, earlier.cameras, earlier.files) == ("earlier", 1, 1)
    assert (later.title, later.cameras, later.files) == ("later", 2, 4)
    assert later.footage == pytest.approx(2522.95 + 1402 + 600 + 600)


def test_remove_game(conn):
    game_id = add_game(conn, "t", two_camera_game())

    remove_game(conn, game_id)

    assert list_games(conn) == []
    with pytest.raises(GameError, match="no game 1"):
        load_game(conn, game_id)
    with pytest.raises(GameError, match="no game 1"):
        remove_game(conn, game_id)


def test_contents_say_what_removing_a_game_deletes(conn):
    game_id = add_game(conn, "vs Lafayette", two_camera_game(), date(2026, 10, 24))
    contents = game_contents(conn, game_id)
    assert (contents.cameras, contents.files, contents.periods) == (2, 4, 0)
    assert contents.folders == [Path("/footage/game")]
    assert removal_items(contents) == [
        "2 cameras and the list of their 4 files (1:25:25 of footage)"
    ]
    assert removal_note(contents) == "The video files stay in /footage/game."

    a, b = load_game(conn, game_id).camera_ids.values()
    at = GAME_START + timedelta(seconds=100)
    save_zone(conn, a, ((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)))
    curve = make_play_curve(0, GAME)
    save_curve(conn, a, curve)
    segments = [
        Segment(at + timedelta(seconds=s), at + timedelta(seconds=s + 5), 0.1) for s in (0, 50, 90)
    ]
    save_clips(conn, a, segments, DEFAULT_SETTINGS, curve)
    first, second, _ = load_clips(conn, a)
    save_triage(conn, replace(first, status="kept", starred=True))
    save_triage(conn, replace(second, status="discarded"))
    save_periods(conn, game_id, [Period(1, at, a), Period(2, at + timedelta(seconds=60), b)])
    save_export(conn, game_id, ExportResult(Path("/videos/v.mp4"), 30.0, 1, 1, 1000))

    contents = game_contents(conn, game_id)
    assert (contents.zones, contents.scans, contents.clips) == (1, 1, 3)
    assert (contents.kept, contents.discarded, contents.starred) == (1, 1, 1)
    assert (contents.periods, contents.exports) == (2, 1)
    assert removal_items(contents)[1:] == [
        "D-zones drawn on 1 camera",
        "Scans of 1 camera",
        "3 clips: 1 kept, 1 discarded, 1 starred",
        "2 periods",
        "The record of 1 export",
    ]
    assert removal_note(contents).endswith(", and exported game videos stay where they were saved.")

    remove_game(conn, game_id)
    with pytest.raises(GameError, match="no game 1"):
        game_contents(conn, game_id)


def test_rename_game_changes_the_title_the_date_or_both(conn):
    game_id = add_game(conn, "t", two_camera_game(), date(2026, 10, 24))

    rename_game(conn, game_id, "  vs Lafayette ")
    assert (load_game(conn, game_id).title, load_game(conn, game_id).date) == (
        "vs Lafayette",
        date(2026, 10, 24),
    )

    rename_game(conn, game_id, game_date=date(2026, 10, 25))
    assert (load_game(conn, game_id).title, load_game(conn, game_id).date) == (
        "vs Lafayette",
        date(2026, 10, 25),
    )

    rename_game(conn, game_id, "@ Arvada", date(2026, 9, 12))
    assert (load_game(conn, game_id).title, load_game(conn, game_id).date) == (
        "@ Arvada",
        date(2026, 9, 12),
    )


@pytest.mark.parametrize(
    ("game_id", "title", "game_date", "message"),
    [
        (1, None, None, "a new title, a new date, or both"),
        (1, "  ", None, "needs a title"),
        (2, "t", None, "no game 2"),
    ],
)
def test_rename_game_rejects(conn, game_id, title, game_date, message):
    add_game(conn, "t", two_camera_game())
    with pytest.raises(GameError, match=message):
        rename_game(conn, game_id, title, game_date)


@pytest.mark.parametrize(
    ("sort", "descending", "titles"),
    [
        ("id", False, ["b", "a", "c"]),
        ("id", True, ["c", "a", "b"]),
        ("date", False, ["a", "c", "b"]),
        ("date", True, ["b", "c", "a"]),
    ],
)
def test_list_games_sorts(conn, sort, descending, titles):
    add_game(conn, "b", group_files([make_info("b/x.MP4")]), date(2026, 10, 25))
    add_game(conn, "a", group_files([make_info("a/x.MP4")]), date(2026, 10, 1))
    add_game(conn, "c", group_files([make_info("c/x.MP4")]), date(2026, 10, 1))

    assert [g.title for g in list_games(conn, sort, descending)] == titles


def test_list_games_rejects_an_unknown_sort(conn):
    with pytest.raises(GameError, match="can't sort games by 'title'"):
        list_games(conn, "title")
