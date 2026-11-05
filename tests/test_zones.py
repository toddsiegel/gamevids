import numpy as np
import pytest

from gamevids import db
from gamevids.zones import (
    ZoneError,
    format_polygon,
    load_zone,
    parse_polygon,
    polygon_mask,
    save_zone,
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
