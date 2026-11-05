"""The library database: one SQLite file holding every game.

The schema is built by numbered migrations. `PRAGMA user_version` records
how many have run, so opening an older library brings it up to date. Never
edit a migration that has shipped; add a new one to the end of `MIGRATIONS`.

Times are stored as ISO 8601 text in UTC, dates as `YYYY-MM-DD`.
"""

import os
import sqlite3
from pathlib import Path

DB_ENV = "GAMEVIDS_DB"

MIGRATIONS = [
    # 1: games and the files that make them up
    """
    CREATE TABLE game (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        -- filled in by later phases
        opponent TEXT,
        home_away TEXT,
        tournament TEXT,
        venue TEXT,
        team TEXT,
        season TEXT
    );

    CREATE TABLE camera (
        id INTEGER PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES game(id) ON DELETE CASCADE,
        serial TEXT,
        label TEXT NOT NULL,
        clock_offset REAL NOT NULL DEFAULT 0,  -- seconds added to this camera's clock
        UNIQUE (game_id, label)
    );

    CREATE TABLE source_file (
        id INTEGER PRIMARY KEY,
        camera_id INTEGER NOT NULL REFERENCES camera(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,  -- order on the camera's timeline
        recording INTEGER NOT NULL,  -- files in one recording play back-to-back
        path TEXT NOT NULL UNIQUE,
        start TEXT NOT NULL,  -- wall clock after stitching
        creation_time TEXT,  -- as the file states it
        duration REAL NOT NULL,
        size INTEGER NOT NULL,
        fps_num INTEGER NOT NULL,
        fps_den INTEGER NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        video_codec TEXT NOT NULL,
        audio_codec TEXT,
        encoder TEXT,
        period TEXT,  -- from a legacy file name: "1", "2", "3", "OT"
        UNIQUE (camera_id, position)
    );
    """,
    # 2: D-zones and activity curves
    """
    CREATE TABLE zone (
        camera_id INTEGER PRIMARY KEY REFERENCES camera(id) ON DELETE CASCADE,
        polygon TEXT NOT NULL  -- JSON [[x, y], ...] as fractions of the frame from top-left
    );

    CREATE TABLE activity_curve (
        camera_id INTEGER PRIMARY KEY REFERENCES camera(id) ON DELETE CASCADE,
        start TEXT NOT NULL,  -- wall clock of the first sample
        rate REAL NOT NULL,  -- samples per second
        samples BLOB NOT NULL,  -- little-endian float32; NaN where the camera wasn't recording
        polygon TEXT NOT NULL,  -- the zone the scan used
        settings TEXT NOT NULL,  -- JSON scan settings
        scanned_at TEXT NOT NULL
    );
    """,
]


class DatabaseError(RuntimeError):
    pass


def default_path() -> Path:
    """`$GAMEVIDS_DB`, else `library.db` in the user's data folder."""
    if env := os.environ.get(DB_ENV):
        return Path(env)
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "gamevids" / "library.db"


def connect(path: Path) -> sqlite3.Connection:
    """Open the library at `path`, creating it or migrating it as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    current = schema_version(conn)
    if current > len(MIGRATIONS):
        raise DatabaseError(
            f"library is at schema version {current}, but this gamevids only knows "
            f"{len(MIGRATIONS)}; upgrade gamevids"
        )
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        # One transaction per migration, so a failure leaves the last good version.
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
