import numpy as np
import pytest

from gamevids import db
from gamevids.zones import (
    DEFAULT_ZONE,
    Suggestion,
    ZoneError,
    check_polygon,
    format_polygon,
    load_zone,
    parse_polygon,
    polygon_mask,
    save_zone,
    suggest_zone,
)


def test_parse_and_format_round_trip():
    points = parse_polygon("0,.55  .26,.46 0.74,0.47 1,1 0,1")

    assert points == ((0, 0.55), (0.26, 0.46), (0.74, 0.47), (1, 1), (0, 1))
    assert format_polygon(points) == "0,0.55 0.26,0.46 0.74,0.47 1,1 0,1"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("0,0 1,1", "at least 3 corners"),
        ("0,0 1,0 1,1.5", r"1,1.5 is outside the frame"),
        ("0,0 1,0 x,1", "can't read corner 'x,1'"),
        ("0,0 1;0 1,1", "can't read corner '1;0'"),
        ("0,0 .5,.5 1,1", "on one line"),
    ],
)
def test_bad_polygons_are_refused(text, message):
    with pytest.raises(ZoneError, match=message):
        parse_polygon(text)


def test_mask_covers_pixel_centres_inside():
    left_half = ((0, 0), (0.5, 0), (0.5, 1), (0, 1))

    mask = polygon_mask(left_half, 4, 2)

    assert mask.tolist() == [[True, True, False, False], [True, True, False, False]]


def test_mask_handles_concave_polygons():
    # An L: the full left column and the bottom row of a 3x3 grid.
    ell = ((0, 0), (1 / 3, 0), (1 / 3, 2 / 3), (1, 2 / 3), (1, 1), (0, 1))

    mask = polygon_mask(ell, 3, 3)

    assert mask.astype(int).tolist() == [[1, 0, 0], [1, 0, 0], [1, 1, 1]]


def test_whole_frame_mask():
    assert polygon_mask(((0, 0), (1, 0), (1, 1), (0, 1)), 32, 18).all()


def test_zones_are_saved_and_replaced(library):
    conn = db.connect(library)
    with conn:
        conn.execute("INSERT INTO game (id, title, date, created_at) VALUES (1, 't', 'd', 'c')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (1, 1, 'A')")

    assert load_zone(conn, 1) is None
    save_zone(conn, 1, ((0, 0), (1, 0), (1, 1)))
    save_zone(conn, 1, ((0, 0.5), (1, 0.5), (1, 1), (0, 1)))

    assert load_zone(conn, 1) == ((0, 0.5), (1, 0.5), (1, 1), (0, 1))
    assert np.isclose(polygon_mask(load_zone(conn, 1), 4, 4).mean(), 0.5)


def _game(conn, title, day, *cameras):
    """Add a game on `day` with cameras given as (serial, label, zone or None); return their ids."""
    with conn:
        game_id = conn.execute(
            "INSERT INTO game (title, date, created_at) VALUES (?, ?, 'c')", (title, day)
        ).lastrowid
        ids = {
            label: conn.execute(
                "INSERT INTO camera (game_id, serial, label) VALUES (?, ?, ?)",
                (game_id, serial, label),
            ).lastrowid
            for serial, label, _ in cameras
        }
    for _, label, polygon in cameras:
        if polygon:
            save_zone(conn, ids[label], polygon)
    return ids


LOW = ((0, 0.6), (1, 0.6), (1, 1), (0, 1))
HIGH = ((0, 0.4), (1, 0.4), (1, 1), (0, 1))
MID = ((0, 0.5), (1, 0.5), (1, 1), (0, 1))


def test_with_no_other_zones_the_default_shape_is_suggested(library):
    conn = db.connect(library)
    ids = _game(conn, "first", "2026-10-17", ("S1", "A", None))

    assert suggest_zone(conn, ids["A"]) == Suggestion(DEFAULT_ZONE, None, None)
    check_polygon(DEFAULT_ZONE)


def test_the_same_camera_in_the_latest_earlier_game_is_suggested(library):
    conn = db.connect(library)
    _game(conn, "old", "2026-10-10", ("S1", "A", MID), ("S2", "B", MID))
    _game(conn, "last", "2026-10-17", ("S2", "A", LOW), ("S1", "B", HIGH))
    _game(conn, "later", "2026-10-30", ("S1", "A", MID))
    ids = _game(conn, "this", "2026-10-24", ("S1", "A", None), ("S3", "B", None))

    # The serial is matched even though camera S1 has a different label in that game.
    assert suggest_zone(conn, ids["A"]) == Suggestion(HIGH, 2, "B")
    # No serial match there: the same label wins.
    assert suggest_zone(conn, ids["B"]) == Suggestion(HIGH, 2, "B")


def test_a_later_game_is_used_when_none_is_earlier(library):
    conn = db.connect(library)
    ids = _game(conn, "this", "2026-10-10", ("S1", "A", None))
    _game(conn, "much later", "2026-11-30", ("S1", "A", LOW))
    _game(conn, "next", "2026-10-17", ("S9", "A", HIGH))

    assert suggest_zone(conn, ids["A"]) == Suggestion(HIGH, 3, "A")


def test_the_cameras_own_game_is_never_suggested(library):
    conn = db.connect(library)
    ids = _game(conn, "this", "2026-10-10", ("S1", "A", LOW), ("S2", "B", None))

    assert suggest_zone(conn, ids["B"]).polygon == DEFAULT_ZONE
