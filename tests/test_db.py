import sqlite3

import pytest

from gamevids import db


def test_connect_creates_the_library_at_the_latest_version(tmp_path):
    path = tmp_path / "new" / "library.db"

    conn = db.connect(path)

    assert path.exists()
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"game", "camera", "source_file"} <= tables


def test_reopening_keeps_data_and_runs_nothing_twice(tmp_path):
    path = tmp_path / "library.db"
    with db.connect(path) as conn:
        conn.execute("INSERT INTO game (title, date, created_at) VALUES ('t', '2026-10-24', 'x')")
    conn.close()

    conn = db.connect(path)

    assert [r["title"] for r in conn.execute("SELECT title FROM game")] == ["t"]


def test_a_library_from_a_newer_gamevids_is_refused(tmp_path):
    path = tmp_path / "library.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    raw.close()

    with pytest.raises(db.DatabaseError, match="upgrade gamevids"):
        db.connect(path)


def test_removing_a_game_removes_its_cameras_and_files(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    with conn:
        conn.execute("INSERT INTO game (id, title, date, created_at) VALUES (1, 't', 'd', 'c')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (1, 1, 'A')")
        conn.execute(
            "INSERT INTO source_file (camera_id, position, recording, path, start, duration,"
            " size, fps_num, fps_den, width, height, video_codec)"
            " VALUES (1, 0, 0, '/a.mp4', 's', 1, 1, 30, 1, 1, 1, 'hevc')"
        )
        conn.execute("DELETE FROM game")

    assert conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 0


def test_default_path_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(db.DB_ENV, str(tmp_path / "mine.db"))
    assert db.default_path() == tmp_path / "mine.db"

    monkeypatch.delenv(db.DB_ENV)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert db.default_path() == tmp_path / "gamevids" / "library.db"
