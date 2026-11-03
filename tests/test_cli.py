import subprocess
import sys
from dataclasses import replace

import pytest
from conftest import FULL_CHAPTER, make_info

import gamevids.cli
from gamevids import __version__
from gamevids.cli import main


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
    assert header.split() == ["ID", "DATE", "CAMERAS", "FILES", "FOOTAGE", "TITLE"]
    assert row.split() == ["1", "2026-10-24", "1", "2", "1:05:25", "vs", "Hyland", "Hills"]

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


def test_game_add_uses_the_library_option(game_folder, tmp_path, capsys):
    path = tmp_path / "other.db"
    assert main(["--db", str(path), "game", "add", "--title", "t", str(game_folder)]) == 0
    assert path.exists()
    assert main(["game", "list"]) == 0
    assert "No games yet" in capsys.readouterr().out
