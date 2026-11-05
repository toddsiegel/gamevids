"""Settings read from `config.toml`, for tuning without changing code.

The file is optional, and so is every key in it; anything left out keeps its
default. It has a section for clip detection and one for period proposals:

    [detect]
    threshold = 0.04     # fraction of the D-zone moving
    smoothing = 2.0      # seconds
    min_duration = 2.0   # seconds
    merge_gap = 4.0      # seconds
    pad_before = 3.0     # seconds
    pad_after = 2.0      # seconds

    [periods]
    smoothing = 5.0          # seconds
    quiet_level = 0.015      # fraction of every D-zone moving
    merge_gap = 15.0         # seconds
    blip_level = 0.04        # fraction of any D-zone moving
    min_quiet = 30.0         # seconds
    lead = 10.0              # seconds
    min_period = 480.0       # seconds
    evenness = 0.25          # seconds of quiet per second of spread
    warmup_level = 0.02      # fraction of every D-zone moving
    warmup_window = 30.0     # seconds
    min_warmup = 60.0        # seconds
"""

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from gamevids.detect import DetectError, DetectSettings
from gamevids.periods import PeriodError, PeriodSettings

CONFIG_ENV = "GAMEVIDS_CONFIG"
SECTIONS = {"detect": DetectSettings, "periods": PeriodSettings}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    detect: DetectSettings = field(default_factory=DetectSettings)
    periods: PeriodSettings = field(default_factory=PeriodSettings)
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

    unknown = sorted(set(data) - set(SECTIONS))
    if unknown:
        raise ConfigError(f"{path}: unknown section [{unknown[0]}]")
    sections = {}
    for name, settings_class in SECTIONS.items():
        values = data.get(name, {})
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: {name} must be a [{name}] section")
        known = {f.name for f in fields(settings_class)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ConfigError(
                f"{path}: unknown setting {name}.{unknown[0]} (known: {', '.join(sorted(known))})"
            )
        try:
            sections[name] = settings_class(**values)
        except (DetectError, PeriodError) as e:
            raise ConfigError(f"{path}: {name}.{e}") from None
    return Config(**sections, path=path)
