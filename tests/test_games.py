from datetime import date, timedelta

import pytest
from conftest import FULL_CHAPTER, GAME_START, make_info

from gamevids import db
from gamevids.games import GameError, add_game, list_games, load_game, remove_game, rename_game
from gamevids.grouping import group_files


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


@pytest.mark.parametrize(
    ("title", "files", "message"),
    [
        ("", [make_info("a.MP4")], "needs a title"),
        ("t", [make_info("a - 1st Period.mp4", serial=None)], "no camera serial"),
        ("t", [make_info("a - 1st Period - D Zone.mp4")], "no files with a camera serial"),
    ],
)
def test_add_game_rejects(conn, title, files, message):
    with pytest.raises(GameError, match=message):
        add_game(conn, title, group_files(files))


def test_list_games_summarises_each_game(conn):
    add_game(conn, "later", two_camera_game("one"), date(2026, 10, 25))
    add_game(conn, "earlier", group_files([make_info("two/a.MP4")]), date(2026, 10, 1))

    (earlier, later) = list_games(conn)

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
