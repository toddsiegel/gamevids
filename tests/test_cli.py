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
