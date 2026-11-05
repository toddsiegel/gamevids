import pytest

from gamevids import config
from gamevids.config import Config, ConfigError, load_config
from gamevids.detect import DetectSettings


def test_a_missing_file_gives_the_defaults(tmp_path):
    assert load_config(tmp_path / "none.toml") == Config()


def test_settings_left_out_keep_their_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[detect]\nthreshold = 0.05\nmerge_gap = 6\n")

    loaded = load_config(path)

    assert loaded.detect == DetectSettings(threshold=0.05, merge_gap=6)
    assert loaded.path == path


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[detect]\nthreshhold = 0.05\n", "unknown setting detect.threshhold"),
        ("[scan]\nrate = 5\n", r"unknown section \[scan\]"),
        ("detect = 3\n", "detect must be a"),
        ("[detect]\nthreshold = 5\n", "detect.threshold must be between 0 and 1"),
        ("[detect]\npad_before = '3'\n", "detect.pad_before must be a number"),
        ("[detect\n", "can't read"),
    ],
)
def test_bad_files_are_refused(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text)

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_default_path_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEVIDS_CONFIG", str(tmp_path / "mine.toml"))
    assert config.default_path() == tmp_path / "mine.toml"

    monkeypatch.delenv("GAMEVIDS_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config.default_path() == tmp_path / "xdg" / "gamevids" / "config.toml"
