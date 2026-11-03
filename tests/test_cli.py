import subprocess
import sys

import pytest

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
