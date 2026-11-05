"""Settings read from `config.toml`, for tuning without changing code.

The file is optional, and so is every key in it; anything left out keeps its
default. For now it holds only the clip detection settings:

    [detect]
    threshold = 0.04     # fraction of the D-zone moving
    smoothing = 2.0      # seconds
    min_duration = 2.0   # seconds
    merge_gap = 4.0      # seconds
    pad_before = 3.0     # seconds
    pad_after = 2.0      # seconds
"""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from gamevids.detect import DetectError, DetectSettings

CONFIG_ENV = "GAMEVIDS_CONFIG"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    detect: DetectSettings = field(default_factory=DetectSettings)
    path: Path | None = None  # the file it came from, if there was one


def default_path() -> Path:
    """`$GAMEVIDS_CONFIG`, else `config.toml` in the user's config folder."""
    if env := os.environ.get(CONFIG_ENV):
        return Path(env)
    config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(config_home) / "gamevids" / "config.toml"


def load_config(path: Path) -> Config:
    """Read `path`, or return the defaults if it doesn't exist."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return Config()
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"can't read {path}: {e}") from None

    unknown = sorted(set(data) - {"detect"})
    if unknown:
        raise ConfigError(f"{path}: unknown section [{unknown[0]}]")
    detect = data.get("detect", {})
    if not isinstance(detect, dict):
        raise ConfigError(f"{path}: detect must be a [detect] section")
    known = {f.name for f in fields(DetectSettings)}
    unknown = sorted(set(detect) - known)
    if unknown:
        raise ConfigError(
            f"{path}: unknown setting detect.{unknown[0]} (known: {', '.join(sorted(known))})"
        )
    try:
        settings = DetectSettings(**detect)
    except DetectError as e:
        raise ConfigError(f"{path}: detect.{e}") from None
    return Config(detect=settings, path=path)
