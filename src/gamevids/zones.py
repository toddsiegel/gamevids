"""D-zone polygons: the part of each camera's view where play is watched.

Corners are fractions of the frame, `(x, y)` from the top-left, so a polygon
fits any resolution. On the command line a polygon is written as
`"x,y x,y x,y ..."`.
"""

import json
import sqlite3
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]
Polygon = tuple[Point, ...]


class ZoneError(ValueError):
    pass


def _area(points: Polygon) -> float:
    pairs = zip(points, points[1:] + points[:1], strict=True)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in pairs)) / 2


def check_polygon(points: Polygon) -> Polygon:
    if len(points) < 3:
        raise ZoneError("a zone needs at least 3 corners")
    for x, y in points:
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ZoneError(f"corner {format_point((x, y))} is outside the frame (0 to 1)")
    if _area(points) == 0:
        raise ZoneError("the zone's corners all lie on one line")
    return points


def parse_polygon(text: str) -> Polygon:
    """Parse `"x,y x,y x,y ..."` into a polygon."""
    points = []
    for corner in text.split():
        try:
            x, y = (float(v) for v in corner.split(","))
        except ValueError:
            raise ZoneError(f"can't read corner {corner!r}; write corners as x,y") from None
        points.append((x, y))
    return check_polygon(tuple(points))


def format_point(point: Point) -> str:
    return ",".join(f"{v:.3f}".rstrip("0").rstrip(".") or "0" for v in point)


def format_polygon(points: Polygon) -> str:
    return " ".join(format_point(p) for p in points)


def polygon_mask(points: Polygon, width: int, height: int) -> np.ndarray:
    """A `height` x `width` boolean mask of the pixels whose centres lie inside."""
    xs = (np.arange(width) + 0.5) / width
    ys = (np.arange(height) + 0.5) / height
    x, y = np.meshgrid(xs, ys)
    inside = np.zeros((height, width), dtype=bool)
    # Even-odd rule: flip every pixel to the left of each edge the row crosses.
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
        if y1 == y2:
            continue
        crosses = (y1 > y) != (y2 > y)
        x_edge = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (x < x_edge)
    return inside


def polygon_to_json(points: Polygon) -> str:
    return json.dumps([list(p) for p in points])


def polygon_from_json(text: str) -> Polygon:
    return tuple((float(x), float(y)) for x, y in json.loads(text))


def save_zone(conn: sqlite3.Connection, camera_id: int, points: Polygon) -> None:
    with conn:
        conn.execute(
            "INSERT INTO zone (camera_id, polygon) VALUES (?, ?)"
            " ON CONFLICT (camera_id) DO UPDATE SET polygon = excluded.polygon",
            (camera_id, polygon_to_json(check_polygon(points))),
        )


def load_zone(conn: sqlite3.Connection, camera_id: int) -> Polygon | None:
    row = conn.execute("SELECT polygon FROM zone WHERE camera_id = ?", (camera_id,)).fetchone()
    return polygon_from_json(row["polygon"]) if row else None


# A starting point when no other game has a zone: the ice from the near boards
# out past the faceoff dots, as the cameras behind the nets usually see it.
DEFAULT_ZONE: Polygon = ((0, 0.55), (0.26, 0.46), (0.74, 0.47), (1, 0.58), (1, 1), (0, 1))


@dataclass(frozen=True)
class Suggestion:
    polygon: Polygon
    game_id: int | None  # the game it came from; None for DEFAULT_ZONE
    label: str | None  # the camera there it came from


def suggest_zone(conn: sqlite3.Connection, camera_id: int) -> Suggestion:
    """A zone to start camera `camera_id` from, when it has none of its own.

    Rinks are the same size and the cameras sit in about the same spot, so the
    zone comes from the nearest game in time that has one: the latest game on
    or before this one's date, else the earliest after it. Within that game,
    the same camera (by serial) is preferred, then the same label, then any.
    """
    this = conn.execute(
        "SELECT c.game_id, c.serial, c.label, g.date FROM camera c"
        " JOIN game g ON g.id = c.game_id WHERE c.id = ?",
        (camera_id,),
    ).fetchone()
    if this is None:
        raise ZoneError(f"no camera {camera_id}")
    rows = conn.execute(
        "SELECT g.id AS game_id, g.date, c.serial, c.label, z.polygon FROM zone z"
        " JOIN camera c ON c.id = z.camera_id JOIN game g ON g.id = c.game_id"
        " WHERE g.id != ?",
        (this["game_id"],),
    ).fetchall()
    if not rows:
        return Suggestion(DEFAULT_ZONE, None, None)

    def nearness(row: sqlite3.Row) -> tuple:
        before = row["date"] <= this["date"]
        # Earlier games first, latest of them first; then later games, earliest first.
        when = (0, _negated(row["date"]), -row["game_id"]) if before else (1, row["date"], 0)
        same_serial = this["serial"] is not None and row["serial"] == this["serial"]
        return (*when, not same_serial, row["label"] != this["label"], row["label"])

    best = min(rows, key=nearness)
    return Suggestion(polygon_from_json(best["polygon"]), best["game_id"], best["label"])


def _negated(day: str) -> tuple[int, ...]:
    """Sorts ISO dates newest first."""
    return tuple(-int(part) for part in day.split("-"))
