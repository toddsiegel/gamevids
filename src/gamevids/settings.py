"""Checks shared by the settings that can be tuned in `config.toml`."""

from dataclasses import fields


def check_settings(settings, error: type[Exception], fractions: tuple[str, ...] = ()) -> None:
    """Raise `error` unless every field is a non-negative number and `fractions` are in (0, 1).

    Meant to be called from a frozen dataclass's `__post_init__`.
    """
    for field in fields(settings):
        value = getattr(settings, field.name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise error(f"{field.name} must be a number, not {value!r}")
        if value < 0:
            raise error(f"{field.name} can't be negative")
    for name in fractions:
        if not 0 < getattr(settings, name) < 1:
            raise error(f"{name} must be between 0 and 1 (a fraction of the zone)")
