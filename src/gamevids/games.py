"""Games in the library: save a camera grouping as a game, then list and load games."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path

from gamevids.grouping import CameraGroup, Chapter, Grouping, Recording, classify_name
from gamevids.probe import MediaInfo


class GameError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameSummary:
    id: int
    title: str
    date: date
    cameras: int
    files: int
    footage: float  # seconds, summed over all cameras


@dataclass
class Game:
    id: int
    title: str
    date: date
    created_at: datetime
    grouping: Grouping  # cameras only; exports and unplaced files are never stored
    camera_ids: dict[str, int]  # database id by camera label

    def camera(self, label: str) -> CameraGroup:
        for camera in self.grouping.cameras:
            if camera.label == label.upper():
                return camera
        labels = ", ".join(c.label for c in self.grouping.cameras)
        raise GameError(f"game {self.id} has no camera {label} (it has {labels})")


def _ts(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def add_game(
    conn: sqlite3.Connection, title: str, grouping: Grouping, game_date: date | None = None
) -> int:
    """Save `grouping` as a new game and return its id.

    The date defaults to the local date of the first camera's start.
    """
    title = title.strip()
    if not title:
        raise GameError("the game needs a title")
    if grouping.unassigned:
        names = ", ".join(c.file.path.name for c in grouping.unassigned)
        raise GameError(f"files with no camera serial can't be added yet: {names}")
    if not grouping.cameras:
        raise GameError("no files with a camera serial to add")

    paths = {str(c.file.path.resolve()): c for cam in grouping.cameras for c in cam.chapters}
    placeholders = ", ".join("?" * len(paths))
    taken = conn.execute(
        "SELECT f.path, g.id, g.title FROM source_file f"
        " JOIN camera c ON c.id = f.camera_id JOIN game g ON g.id = c.game_id"
        f" WHERE f.path IN ({placeholders}) ORDER BY g.id, f.path",
        list(paths),
    ).fetchall()
    if taken:
        lines = []
        for r in taken:
            heading = f"already in game {r['id']} ({r['title']}):"
            if heading not in lines:
                lines.append(heading)
            lines.append(f"  {Path(r['path']).name}")
        raise GameError("\n".join(lines))

    assert grouping.date is not None
    game_date = game_date or grouping.date.astimezone().date()
    with conn:
        game_id = conn.execute(
            "INSERT INTO game (title, date, created_at) VALUES (?, ?, ?)",
            (title, game_date.isoformat(), _ts(datetime.now(UTC))),
        ).lastrowid
        for camera in grouping.cameras:
            camera_id = conn.execute(
                "INSERT INTO camera (game_id, serial, label) VALUES (?, ?, ?)",
                (game_id, camera.serial, camera.label),
            ).lastrowid
            position = 0
            for recording_index, recording in enumerate(camera.recordings):
                for chapter in recording.chapters:
                    _insert_file(conn, camera_id, position, recording_index, chapter)
                    position += 1
    assert game_id is not None
    return game_id


def _insert_file(
    conn: sqlite3.Connection, camera_id: int, position: int, recording: int, chapter: Chapter
) -> None:
    info = chapter.file
    columns = {
        "camera_id": camera_id,
        "position": position,
        "recording": recording,
        "path": str(info.path.resolve()),
        "start": _ts(chapter.start),
        "creation_time": _ts(info.creation_time) if info.creation_time else None,
        "duration": info.duration,
        "size": info.size,
        "fps_num": info.fps.numerator,
        "fps_den": info.fps.denominator,
        "width": info.width,
        "height": info.height,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "encoder": info.encoder,
        "period": chapter.period,
    }
    names = ", ".join(columns)
    placeholders = ", ".join(f":{name}" for name in columns)
    conn.execute(f"INSERT INTO source_file ({names}) VALUES ({placeholders})", columns)


def list_games(conn: sqlite3.Connection) -> list[GameSummary]:
    rows = conn.execute(
        "SELECT g.id, g.title, g.date,"
        " COUNT(DISTINCT c.id) AS cameras, COUNT(f.id) AS files,"
        " COALESCE(SUM(f.duration), 0) AS footage"
        " FROM game g LEFT JOIN camera c ON c.game_id = g.id"
        " LEFT JOIN source_file f ON f.camera_id = c.id"
        " GROUP BY g.id ORDER BY g.id"
    ).fetchall()
    return [
        GameSummary(
            r["id"],
            r["title"],
            date.fromisoformat(r["date"]),
            r["cameras"],
            r["files"],
            r["footage"],
        )
        for r in rows
    ]


def load_game(conn: sqlite3.Connection, game_id: int) -> Game:
    row = conn.execute("SELECT * FROM game WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        raise GameError(f"no game {game_id}")

    cameras = []
    camera_ids = {}
    for cam in conn.execute(
        "SELECT * FROM camera WHERE game_id = ? ORDER BY label", (game_id,)
    ).fetchall():
        recordings: list[Recording] = []
        for f in conn.execute(
            "SELECT * FROM source_file WHERE camera_id = ? ORDER BY position", (cam["id"],)
        ).fetchall():
            if len(recordings) <= f["recording"]:
                recordings.append(Recording([]))
            recordings[-1].chapters.append(_chapter(f, cam["serial"]))
        cameras.append(CameraGroup(cam["serial"], cam["label"], recordings))
        camera_ids[cam["label"]] = cam["id"]

    return Game(
        id=row["id"],
        title=row["title"],
        date=date.fromisoformat(row["date"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        grouping=Grouping(cameras=cameras),
        camera_ids=camera_ids,
    )


def _chapter(row: sqlite3.Row, serial: str | None) -> Chapter:
    path = Path(row["path"])
    stated = row["creation_time"]
    info = MediaInfo(
        path=path,
        duration=row["duration"],
        size=row["size"],
        fps=Fraction(row["fps_num"], row["fps_den"]),
        width=row["width"],
        height=row["height"],
        video_codec=row["video_codec"],
        audio_codec=row["audio_codec"],
        creation_time=datetime.fromisoformat(stated) if stated else None,
        encoder=row["encoder"],
        serial=serial,
    )
    kind, _ = classify_name(path.name)
    return Chapter(info, datetime.fromisoformat(row["start"]), kind, row["period"])


def remove_game(conn: sqlite3.Connection, game_id: int) -> None:
    """Forget a game. Its video files are left untouched."""
    with conn:
        if conn.execute("DELETE FROM game WHERE id = ?", (game_id,)).rowcount == 0:
            raise GameError(f"no game {game_id}")


def rename_game(
    conn: sqlite3.Connection,
    game_id: int,
    title: str | None = None,
    game_date: date | None = None,
) -> None:
    """Change a game's title, its date, or both. Whatever is left as None is kept."""
    if title is None and game_date is None:
        raise GameError("give a new title, a new date, or both")
    if title is not None:
        title = title.strip()
        if not title:
            raise GameError("the game needs a title")
    with conn:
        changed = conn.execute(
            "UPDATE game SET title = COALESCE(?, title), date = COALESCE(?, date) WHERE id = ?",
            (title, game_date.isoformat() if game_date else None, game_id),
        ).rowcount
        if changed == 0:
            raise GameError(f"no game {game_id}")
