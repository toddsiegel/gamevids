import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from conftest import FULL_CHAPTER, GAME, GAME_START, make_info, make_play_curve

import gamevids.cli
from gamevids import __version__, db
from gamevids.activity import ActivityCurve, ScanSettings, save_curve
from gamevids.cli import main
from gamevids.zones import save_zone

WHOLE_FRAME = ((0, 0), (1, 0), (1, 1), (0, 1))


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"gamevids {__version__}"


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    assert "usage: gamevids" in capsys.readouterr().out


def test_runs_as_module():
    result = subprocess.run(
        [sys.executable, "-m", "gamevids", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.startswith("gamevids ")


def test_probe_prints_cameras_and_unassigned_files(make_clip, capsys):
    clip = make_clip("Game - 1st Period.mp4", seconds=1, creation_time="2026-10-24T17:54:48Z")
    make_clip("Game - 1st Period - D Zone.mp4", seconds=1)

    assert main(["probe", str(clip.parent)]) == 0

    header, *rows = capsys.readouterr().out.splitlines()
    assert header.split() == ["CAM", "START", "END", "LENGTH", "PERIOD", "NOTE", "FILE"]
    assert rows[0].startswith("?")
    assert rows[0].endswith("P1      no serial               Game - 1st Period.mp4")
    assert rows[1].startswith("-")
    assert rows[1].endswith("D-zone export, skipped  Game - 1st Period - D Zone.mp4")
    assert "have no camera serial; assign them by hand" in rows[-1]


def test_probe_with_no_videos_fails(tmp_path, capsys):
    assert main(["probe", str(tmp_path)]) == 1
    assert "No video files found" in capsys.readouterr().err


@pytest.fixture
def game_folder(tmp_path, monkeypatch):
    """A folder of empty files that probe as one camera's chapters plus a D-zone export."""
    folder = tmp_path / "2026-10-24 Hyland Hills"
    folder.mkdir()
    infos = {
        "DJI_20261024115345_0001_D.MP4": make_info("x", duration=2522.95, size=FULL_CHAPTER),
        "DJI_20261024123549_0002_D.MP4": make_info("x", start=2524.0, duration=1402.0),
        "1st Period - D Zone.mp4": make_info("x", serial=None),
    }
    for name in infos:
        (folder / name).touch()

    def fake_probe(path):
        return replace(infos[path.name], path=path)

    monkeypatch.setattr("gamevids.cli.probe_file", fake_probe)
    return folder


def test_game_add_list_show_remove(game_folder, capsys):
    assert main(["game", "list"]) == 0
    assert "No games yet" in capsys.readouterr().out

    assert main(["game", "add", "--title", "vs Hyland Hills", str(game_folder)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Added game 1.\nSkipped 1 D-zone export(s).\n")
    assert f"Folder: {game_folder}" in out

    assert main(["game", "list"]) == 0
    header, row = capsys.readouterr().out.splitlines()
    assert header.split() == ["ID", "DATE", "TITLE", "CAMERAS", "FILES", "FOOTAGE"]
    assert row.split() == ["1", "2026-10-24", "vs", "Hyland", "Hills", "1", "2", "1:05:25"]

    assert main(["game", "show", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == ["Game 1: vs Hyland Hills", "Date: 2026-10-24", f"Folder: {game_folder}"]
    assert [line.split()[-1] for line in lines[5:7]] == [
        "DJI_20261024115345_0001_D.MP4",
        "DJI_20261024123549_0002_D.MP4",
    ]

    assert main(["game", "remove", "1"]) == 0
    assert "Removed game 1 (vs Hyland Hills)" in capsys.readouterr().out
    assert main(["game", "show", "1"]) == 1
    assert "error: no game 1" in capsys.readouterr().err


def test_game_rename(game_folder, capsys):
    assert main(["game", "add", "--title", "t", str(game_folder)]) == 0
    capsys.readouterr()

    assert main(["game", "rename", "1", "vs Hyland Hills"]) == 0
    assert capsys.readouterr().out == "Title: t -> vs Hyland Hills\n"

    assert main(["game", "rename", "1", "--date", "2026-10-25"]) == 0
    assert capsys.readouterr().out == "Date: 2026-10-24 -> 2026-10-25\n"

    assert main(["game", "rename", "1", "@ Arvada", "--date", "2026-09-12"]) == 0
    assert capsys.readouterr().out == (
        "Title: vs Hyland Hills -> @ Arvada\nDate: 2026-10-25 -> 2026-09-12\n"
    )

    assert main(["game", "rename", "1", "@ Arvada"]) == 0
    assert "already has that title and date" in capsys.readouterr().out

    assert main(["game", "rename", "1"]) == 1
    assert "a new title, a new --date, or both" in capsys.readouterr().err

    assert main(["game", "rename", "2", "x"]) == 1
    assert "error: no game 2" in capsys.readouterr().err


def test_game_list_sort_flags(game_folder, tmp_path, capsys):
    (tmp_path / "second").mkdir()
    for f in game_folder.iterdir():
        (tmp_path / "second" / f.name).touch()
    assert main(["game", "add", "--title", "first", "--date", "2026-10-24", str(game_folder)]) == 0
    second = tmp_path / "second"
    assert main(["game", "add", "--title", "second", "--date", "2026-10-01", str(second)]) == 0
    capsys.readouterr()

    def ids(*flags):
        assert main(["game", "list", *flags]) == 0
        return [line.split()[0] for line in capsys.readouterr().out.splitlines()[1:]]

    assert ids() == ["1", "2"]
    assert ids("--desc") == ["2", "1"]
    assert ids("--sort", "date") == ["2", "1"]
    assert ids("--sort", "date", "--desc") == ["1", "2"]


def test_game_add_refuses_files_without_a_serial_unless_told(game_folder, monkeypatch, capsys):
    (game_folder / "2nd Period.mp4").touch()
    unplaced = make_info("x", serial=None)
    probe = gamevids.cli.probe_file
    monkeypatch.setattr(
        "gamevids.cli.probe_file",
        lambda p: replace(unplaced, path=p) if "2nd" in p.name else probe(p),
    )

    assert main(["game", "add", "--title", "t", str(game_folder)]) == 1
    assert "no camera serial:\n  2nd Period.mp4" in capsys.readouterr().err

    assert main(["game", "add", "--title", "t", "--skip-unassigned", str(game_folder)]) == 0
    assert "Skipping files with no camera serial" in capsys.readouterr().err


@pytest.fixture
def legacy_folder(tmp_path, monkeypatch):
    """One camera's 3rd period, plus a 1st period that lost its serial and creation time."""
    folder = tmp_path / "2026-09-26 @ Arapahoe Maize"
    folder.mkdir()
    infos = {
        "3rd Period.mp4": make_info("x", start=3000, timecode="09:55:04;18"),
        "1st Period.mp4": make_info("x", start=None, serial=None, timecode="09:05:51;006"),
    }
    for name in infos:
        (folder / name).touch()
    monkeypatch.setattr(
        "gamevids.cli.probe_file", lambda path: replace(infos[path.name], path=path)
    )
    return folder


def test_game_add_places_an_assigned_file_by_its_timecode(legacy_folder, denver, capsys):
    args = ["game", "add", "--title", "t", "--assign", "1st Period.mp4", "a", str(legacy_folder)]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert "Placing 1st Period.mp4 at 11:54:33, from its timecode." in captured.err
    rows = [line.split() for line in captured.out.splitlines() if line.startswith("A ")]
    assert [row[:2] + row[-2:] for row in rows] == [
        ["A", "11:54:33", "1st", "Period.mp4"],
        ["A", "12:43:46", "3rd", "Period.mp4"],
    ]


def test_game_add_takes_a_start_time_by_hand(legacy_folder, denver, capsys):
    first = str(legacy_folder / "1st Period.mp4")
    args = ["game", "add", "--title", "t", "--assign", first, "B", "--start", first, "11:30"]
    assert main([*args, str(legacy_folder)]) == 0
    out = capsys.readouterr().out
    assert any(line.split()[:2] == ["B", "11:30:00"] for line in out.splitlines())


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--assign", "1st Period.mp4", "B"], "camera B has no timecode"),
        (["--assign", "3rd Period.mp4", "A"], "not one of the files with no camera serial"),
        (["--start", "1st Period.mp4", "11:30"], "--start needs --assign"),
        (["--assign", "1st Period.mp4", "A", "--start", "1st Period.mp4", "noon"], "isn't a time"),
        (["--assign", "1st Period.mp4", "AA"], "isn't a letter"),
    ],
)
def test_game_add_refuses_bad_assignments(legacy_folder, capsys, args, message):
    assert main(["game", "add", "--title", "t", *args, str(legacy_folder)]) == 1
    assert message in capsys.readouterr().err


def test_game_add_uses_the_library_option(game_folder, tmp_path, capsys):
    path = tmp_path / "other.db"
    assert main(["--db", str(path), "game", "add", "--title", "t", str(game_folder)]) == 0
    assert path.exists()
    assert main(["game", "list"]) == 0
    assert "No games yet" in capsys.readouterr().out


@pytest.fixture
def clip_game(make_clip, monkeypatch, capsys):
    """Game 1: one camera made of a real 2 s clip."""
    clip = make_clip(
        "DJI_20261024115345_0001_D.MP4", seconds=2, creation_time="2026-10-24T17:53:46Z"
    )
    probe = gamevids.cli.probe_file
    monkeypatch.setattr("gamevids.cli.probe_file", lambda p: replace(probe(p), serial="CAMA"))
    assert main(["game", "add", "--title", "vs Hyland Hills", str(clip)]) == 0
    capsys.readouterr()
    return clip


def test_scan_needs_a_zone(clip_game, capsys):
    assert main(["scan", "1"]) == 1
    err = capsys.readouterr().err
    assert "Camera A has no D-zone yet. Give one with --zone A CORNERS" in err
    assert "--plot-only" in err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--zone", "A", "0,0 1,1"], "camera A's zone: a zone needs at least 3 corners"),
        (["--zone", "C", "0,0 1,0 1,1"], "error: game 1 has no camera C (it has A)"),
        (["--camera", "B"], "error: game 1 has no camera B (it has A)"),
    ],
)
def test_scan_refuses_bad_arguments(clip_game, capsys, args, message):
    assert main(["scan", "1", *args]) == 1
    assert message in capsys.readouterr().err


def test_plot_only_saves_the_zone_without_scanning(clip_game, tmp_path, library, capsys):
    plot = tmp_path / "zones.png"

    args = ["--plot-only", "--zone", "a", "0,.5 1,.5 1,1", "--plot", str(plot)]
    assert main(["scan", "1", *args]) == 0

    assert capsys.readouterr().out == f"Wrote {plot}\n"
    assert plot.read_bytes().startswith(b"\x89PNG")
    conn = db.connect(library)
    zone = conn.execute("SELECT polygon FROM zone").fetchone()[0]
    assert zone == "[[0.0, 0.5], [1.0, 0.5], [1.0, 1.0]]"
    assert conn.execute("SELECT COUNT(*) FROM activity_curve").fetchone()[0] == 0


def test_scan_saves_curves_and_reuses_the_zone(clip_game, tmp_path, library, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["scan", "1", "--zone", "A", "0,0 1,0 1,1 0,1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Camera A: scanned 0:00:02 of footage in ")
    assert out.endswith("Wrote game-1-activity.png\n")
    assert (tmp_path / "game-1-activity.png").exists()

    conn = db.connect(library)
    samples = conn.execute("SELECT samples FROM activity_curve").fetchone()[0]
    assert len(samples) == 20 * 4  # 2 s at 10 samples a second, float32

    assert main(["scan", "1", "--camera", "a"]) == 0


@pytest.fixture
def scanned_game(clip_game, library, tmp_path, monkeypatch):
    """Game 1 with a saved 2 s curve: quiet, then 20% of the zone moving from 0.5 s to 1.5 s."""
    monkeypatch.chdir(tmp_path)
    conn = db.connect(library)
    values = np.array([0.0] * 5 + [0.2] * 10 + [0.0] * 5, dtype=np.float32)
    curve = ActivityCurve(GAME_START, 10.0, values, WHOLE_FRAME, ScanSettings(), GAME_START)
    save_zone(conn, 1, WHOLE_FRAME)
    save_curve(conn, 1, curve)
    config = tmp_path / "config.toml"
    config.write_text(
        "[detect]\nsmoothing = 0\nmin_duration = 0.5\npad_before = 0\npad_after = 0\n"
    )
    return conn, config


def test_detect_needs_a_scan(clip_game, capsys):
    assert main(["detect", "1"]) == 1
    assert "Camera A hasn't been scanned yet. Run `gamevids scan 1 --camera A` first." in (
        capsys.readouterr().err
    )


def test_detect_lists_and_saves_clips_and_marks_them_on_the_plot(scanned_game, tmp_path, capsys):
    conn, config = scanned_game

    assert main(["--config", str(config), "detect", "1"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        f"Settings (from {config}): threshold 4%, smoothing 0 s, min duration 0.5 s, "
        "merge gap 4 s, padding 0 s before and 0 s after"
    )
    assert lines[2].split() == ["ID", "CAM", "START", "END", "LENGTH", "PEAK"]
    row = lines[3].split()
    assert (row[0], row[1], row[4], row[5]) == ("1", "A", "0:00:01", "20.0%")
    assert lines[5:] == [
        "Camera A: 1 clip, 0:00:01 of 0:00:02 footage (50%)",
        "About 0:00:00 to watch at 2x.",
        "Wrote game-1-activity.png",
    ]
    assert (tmp_path / "game-1-activity.png").exists()
    assert conn.execute("SELECT COUNT(*) FROM clip").fetchone()[0] == 1

    # Detecting again with the defaults replaces the clip with a new one.
    assert main(["detect", "1", "--plot", str(tmp_path / "again.png")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Settings (defaults): threshold 4%, smoothing 2 s,")
    assert lines[3].split()[:2] == ["2", "A"]
    assert conn.execute("SELECT COUNT(*) FROM clip").fetchone()[0] == 1

    config.write_text("[detect]\nthreshold = 0.5\n")
    assert main(["--config", str(config), "detect", "1"]) == 0
    assert "No clips found." in capsys.readouterr().out
    assert conn.execute("SELECT COUNT(*) FROM clip").fetchone()[0] == 0


def test_detect_keeps_triaged_clips_unless_forced(scanned_game, capsys):
    conn, config = scanned_game
    assert main(["--config", str(config), "detect", "1"]) == 0
    with conn:
        conn.execute("UPDATE clip SET status = 'kept'")
    capsys.readouterr()

    assert main(["--config", str(config), "detect", "1"]) == 1
    err = capsys.readouterr().err
    assert "1 of camera A's clips have been triaged" in err
    assert "Pass --force" in err

    assert main(["--config", str(config), "detect", "1", "--force"]) == 0
    assert [r[0] for r in conn.execute("SELECT status FROM clip")] == ["candidate"]


def test_detect_warns_when_the_zone_changed_since_the_scan(scanned_game, capsys):
    conn, config = scanned_game
    save_zone(conn, 1, ((0, 0.5), (1, 0.5), (1, 1)))

    assert main(["--config", str(config), "detect", "1"]) == 0
    assert "camera A's zone has changed since it was scanned" in capsys.readouterr().err


def test_detect_refuses_a_bad_config(scanned_game, capsys):
    _, config = scanned_game
    config.write_text("[detect]\nthreshold = 40\n")

    assert main(["--config", str(config), "detect", "1"]) == 1
    assert "detect.threshold must be between 0 and 1" in capsys.readouterr().err


def test_periods_needs_a_scan(clip_game, capsys):
    assert main(["periods", "1"]) == 1
    assert "Camera A hasn't been scanned yet. Run `gamevids scan 1 --camera A` first." in (
        capsys.readouterr().err
    )


@pytest.fixture
def two_camera_game(tmp_path, monkeypatch, library, denver, capsys):
    """Game 1: cameras A and B each filming the 3,200 s synthetic GAME, scanned."""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "game"
    folder.mkdir()
    infos = {
        "DJI_20261024115345_0001_D.MP4": make_info("x", duration=3200),
        "DJI_20261024115345_0002_D.MP4": make_info("x", duration=3200, serial="CAMB"),
    }
    for name in infos:
        (folder / name).touch()
    monkeypatch.setattr("gamevids.cli.probe_file", lambda p: replace(infos[p.name], path=p))
    monkeypatch.setattr("gamevids.cli.still_frame", lambda *a: np.zeros((36, 64, 3), np.uint8))
    assert main(["game", "add", "--title", "vs Hyland Hills", str(folder)]) == 0
    conn = db.connect(library)
    save_curve(conn, 1, make_play_curve(0, GAME))
    save_curve(conn, 2, make_play_curve(1, GAME))
    capsys.readouterr()
    return conn


def test_periods_are_proposed_saved_and_shown(two_camera_game, tmp_path, capsys):
    conn = two_camera_game

    assert main(["periods", "1"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [
        "Settings (defaults): quiet below 1.5% for 30 s, periods at least 0:08:00, "
        "warmup above 2% for 60 s",
        "Warmup until 11:57:47.",
    ]
    assert lines[3].split() == ["PERIOD", "START", "LENGTH", "GOALIE", "CLIPS", "NOTE"]
    assert lines[4:7] == [
        "P1      11:58:35  0:16:30  -              quiet 11:57:48-11:58:45 (0:00:57)",
        "P2      12:15:05  0:16:40  -              quiet 12:13:48-12:15:15 (0:01:27)",
        "P3      12:31:45  0:15:21  -              quiet 12:30:18-12:31:55 (0:01:37)",
    ]
    assert lines[8:] == [
        "The footage ends at 12:47:06.",
        "",
        "Other quiet spells, longest first:",
        "  12:22:08-12:23:05  0:00:57",
        "",
        "Move a start with --start P2=HH:MM:SS.",
        "Choose the camera behind our goalie's net in P1 with --goalie CAM; "
        "later periods alternate ends.",
        "Wrote game-1-activity.png",
    ]
    assert (tmp_path / "game-1-activity.png").exists()
    assert conn.execute("SELECT COUNT(*) FROM period").fetchone()[0] == 3

    # The saved periods are shown as they are, not proposed again.
    assert main(["periods", "1", "--goalie", "b"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:4] == ["PERIOD", "START", "LENGTH", "GOALIE"]
    assert [line.split()[3] for line in lines[1:4]] == ["B", "A", "B"]
    assert lines[-1] == "Wrote game-1-activity.png"


def test_periods_can_be_moved_by_hand_and_only_replaced_when_forced(two_camera_game, capsys):
    conn = two_camera_game
    assert main(["periods", "1", "--goalie", "A", "--goalie", "P3=A"]) == 0
    capsys.readouterr()

    assert main(["periods", "1", "--start", "p2=12:16", "--start", "OT=12:45:00"]) == 0
    rows = capsys.readouterr().out.splitlines()[1:5]
    assert [r.split()[:4] for r in rows] == [
        ["P1", "11:58:35", "0:17:25", "A"],
        ["P2", "12:16:00", "0:15:45", "B"],
        ["P3", "12:31:45", "0:13:15", "A"],
        ["OT", "12:45:00", "0:02:06", "B"],
    ]
    assert rows[1].endswith("set by hand") and rows[3].endswith("set by hand")

    assert main(["periods", "1", "--propose"]) == 1
    err = capsys.readouterr().err
    assert "P2 and OT's start was set by hand" in err
    assert "Pass --force" in err

    assert main(["periods", "1", "--propose", "--force"]) == 0
    out = capsys.readouterr().out
    assert "Only 3 of 4 periods fit between quiet spells; add the rest with --start." in out
    starts = [r[0] for r in conn.execute("SELECT start FROM period ORDER BY number")]
    assert len(starts) == 3 and "18:16:00" not in starts[1]
    goalies = conn.execute("SELECT goalie_camera_id FROM period ORDER BY number").fetchall()
    assert [g[0] for g in goalies] == [1, 2, 1]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--start", "2=13:30"], "error: 13:30 is outside the game's footage (11:53:46-12:47:06)"),
        (["--start", "2=11:50"], "error: 11:50 is outside the game's footage"),
        (["--start", "3=12:00"], "error: P3 must start after P2 (12:15:05)"),
        (["--start", "5=12:45"], "error: can't set OT2's start"),
        (["--goalie", "C"], "error: game 1 has no camera C (it has A, B)"),
        (["--goalie", "OT=A"], "error: the game has no OT"),
    ],
)
def test_periods_refuses_bad_changes(two_camera_game, capsys, args, message):
    assert main(["periods", "1"]) == 0
    capsys.readouterr()

    assert main(["periods", "1", *args]) == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("value", ["12:00", "X=12:00"])
def test_periods_rejects_a_malformed_start(two_camera_game, capsys, value):
    with pytest.raises(SystemExit):
        main(["periods", "1", "--start", value])
    assert "--start" in capsys.readouterr().err


def test_periods_can_be_laid_out_by_hand_without_a_scan(clip_game, library, denver, capsys):
    assert main(["periods", "1", "--start", "1=11:53:46", "--goalie", "A"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[1].split()[:4] == ["P1", "11:53:46", "0:00:02", "A"]
    assert "Move a start" not in out


def test_triage_lists_our_goalies_clips_and_where_triage_resumes(two_camera_game, capsys):
    conn = two_camera_game
    assert main(["triage", "1"]) == 1
    assert "no clips yet. Run `gamevids scan 1`, then `gamevids detect 1`" in (
        capsys.readouterr().err
    )

    assert main(["detect", "1"]) == 0
    assert main(["triage", "1"]) == 1
    assert "Game 1 has no periods yet. Run `gamevids periods 1` first." in capsys.readouterr().err

    assert main(["periods", "1"]) == 0
    assert main(["triage", "1"]) == 1
    assert "Run `gamevids periods 1 --goalie CAM` first." in capsys.readouterr().err

    assert main(["periods", "1", "--goalie", "A"]) == 0
    first = conn.execute(
        "SELECT id FROM clip WHERE camera_id = 1 AND start >= ? ORDER BY start",
        ((GAME_START + timedelta(seconds=289)).isoformat(),),  # P1's start, 11:58:35
    ).fetchone()[0]
    with conn:
        conn.execute("UPDATE clip SET status = 'kept', starred = 1 WHERE id = ?", (first,))
    capsys.readouterr()

    assert main(["triage", "1"]) == 0

    lines = capsys.readouterr().out.splitlines()
    header = ["#", "ID", "PERIOD", "CAM", "START", "LENGTH", "STATUS", "STAR", "TRIM"]
    assert lines[0].split() == header
    rows = [line.split() for line in lines[1 : lines.index("")]]
    assert rows[0][1:3] == [str(first), "P1"] and rows[0][6:] == ["kept", "*"]
    # P1 and P3 at camera A, P2 at camera B, in wall-clock order
    assert {(r[2], r[3]) for r in rows} == {("P1", "A"), ("P2", "B"), ("P3", "A")}
    assert [r[4] for r in rows] == sorted(r[4] for r in rows)
    assert lines[-2].startswith(f"1 of {len(rows)} clips triaged: 1 kept (")
    assert lines[-1].startswith("Next: #2 (clip ")


def test_export_writes_the_game_video_and_refuses_to_replace_it_unless_forced(
    clip_game, library, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["export", "1"]) == 1
    assert "Game 1 has no kept clips." in capsys.readouterr().err

    conn = db.connect(library)
    with conn:
        conn.execute(
            "INSERT INTO clip (camera_id, start, end, score, status) VALUES (1, ?, ?, 0.1, 'kept')",
            (GAME_START.isoformat(), (GAME_START + timedelta(seconds=1.5)).isoformat()),
        )

    assert main(["export", "1"]) == 0

    video = tmp_path / "Videos" / "Game Vids" / "vs Hyland Hills - DZone.mp4"
    out = capsys.readouterr().out
    assert out.startswith(f"Wrote {video}: 1 clip in 0:00:04, ")
    assert video.stat().st_size > 0
    row = conn.execute("SELECT game_id, path, clips, duration FROM export").fetchone()
    assert tuple(row) == (1, str(video), 1, 4.5)

    assert main(["export", "1"]) == 1
    assert f"{video} already exists. Pass --force to replace it." in capsys.readouterr().err

    folder = tmp_path / "elsewhere"  # not made yet
    assert main(["export", "1", "-o", str(folder)]) == 0
    assert (folder / video.name).exists()
    assert main(["export", "1", "--force"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM export").fetchone()[0] == 3
