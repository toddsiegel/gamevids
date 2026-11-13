"""Periods: where each one starts, and which camera is behind our goalie's net.

Between periods every D-zone goes quiet at once, with the players at the
benches or changing ends. During play at least one zone nearly always has
something moving, even if it's only a goalie. So the proposal looks for
*quiet spells*, where every camera's smoothed activity stays below
`quiet_level`, and picks the longest ones that leave each period at least
`min_period` long. A period starts `lead` seconds before its quiet spell
ends, which is just before the puck drop.

A camera that isn't recording counts as quiet, so a stretch where every
camera was stopped (as they often are between periods) is a quiet spell too.

The warmup keeps both zones busy at the same time for minutes, which play
almost never does for more than a few seconds. When the footage has a warmup,
period 1 starts at the end of the first quiet spell after it; otherwise it
starts with the footage.

Periods are the same length on the game clock, so their wall-clock lengths
are usually close too. A late period played with a stopped clock can have
stoppages longer than an intermission, so the pick also weighs how even the
periods come out: each second between the longest and shortest period costs
`evenness` seconds of quiet.

Even so, a stoppage after a goal or for a penalty can be as quiet as an
intermission and as long, so the proposal is a starting point to check, not
an answer.
"""

import math
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from itertools import combinations, pairwise

import numpy as np

from gamevids.activity import ActivityCurve, moving_average
from gamevids.settings import check_settings

STEP = 1.0  # seconds between points on the timeline the cameras are combined on
REGULATION = 3  # periods before overtime
DEFAULT_COUNT = REGULATION
MAX_CANDIDATES = 30  # longest quiet spells tried as breaks, to bound the search


class PeriodError(RuntimeError):
    pass


@dataclass(frozen=True)
class PeriodSettings:
    smoothing: float = 5.0  # seconds averaged on each camera first
    quiet_level: float = 0.015  # fraction moving that every zone must stay below to be quiet
    merge_gap: float = 15.0  # seconds; quiet spells this close become one...
    blip_level: float = 0.04  # ...unless the zone between them gets this busy
    min_quiet: float = 30.0  # seconds; a shorter quiet spell can't be a break
    lead: float = 10.0  # seconds a period starts before its quiet spell ends
    min_period: float = 480.0  # seconds; the shortest a period can be, wall clock
    evenness: float = 0.25  # seconds of quiet given up per second of spread in period lengths
    warmup_level: float = 0.02  # fraction moving that every zone must be above for warmup
    warmup_window: float = 30.0  # seconds; every zone busy for over half of this is warmup
    min_warmup: float = 60.0  # seconds of warmup needed to count

    def __post_init__(self) -> None:
        check_settings(self, PeriodError, fractions=("quiet_level", "blip_level", "warmup_level"))


DEFAULT_SETTINGS = PeriodSettings()


@dataclass(frozen=True)
class QuietSpell:
    """A stretch where every camera's zone was quiet."""

    start: datetime
    end: datetime

    @property
    def duration(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True)
class Proposal:
    starts: list[datetime]  # where each period starts, P1 first
    quiet: list[QuietSpell]  # every quiet spell, in time order
    warmup_end: datetime | None  # None when no warmup was found
    footage_start: datetime
    footage_end: datetime

    def spell_at(self, moment: datetime) -> QuietSpell | None:
        """The quiet spell `moment` falls in, if any."""
        return next((s for s in self.quiet if s.start <= moment <= s.end), None)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """`(start, stop)` index pairs of each run of True values."""
    edges = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def _timeline(curves: list[ActivityCurve], smoothing: float) -> tuple[datetime, np.ndarray]:
    """Each camera's smoothed curve on a shared grid, one row per camera, `STEP` apart.

    A row is NaN where its camera wasn't recording.
    """
    start = min(c.start for c in curves)
    end = max(c.end for c in curves)
    n = math.ceil((end - start).total_seconds() / STEP)
    rows = np.full((len(curves), n), np.nan)
    for row, curve in zip(rows, curves, strict=True):
        window = max(1, round(smoothing * curve.rate))
        values = moving_average(curve.values.astype(np.float64), window)
        offset = (curve.start - start).total_seconds()
        index = np.floor((np.arange(n) * STEP - offset) * curve.rate).astype(int)
        inside = (index >= 0) & (index < len(values))
        row[inside] = values[index[inside]]
    return start, rows


def _quiet_spells(rows: np.ndarray, settings: PeriodSettings) -> list[tuple[int, int]]:
    """Quiet runs, joined across short blips of a little movement.

    A blip that reaches `blip_level` is play, not a goalie stretching, so it
    splits the spell: a period start placed after it would put that play in
    the wrong period.
    """
    loudest = np.where(np.isnan(rows), 0.0, rows).max(axis=0)
    spells: list[tuple[int, int]] = []
    for start, stop in _runs(loudest < settings.quiet_level):
        if (
            spells
            and start - spells[-1][1] <= settings.merge_gap / STEP
            and loudest[spells[-1][1] : start].max() < settings.blip_level
        ):
            spells[-1] = (spells[-1][0], stop)
        else:
            spells.append((start, stop))
    return [(a, b) for a, b in spells if b - a >= settings.min_quiet / STEP]


def _warmup_end(rows: np.ndarray, settings: PeriodSettings) -> int | None:
    """Where the first warmup ends, or None if there isn't one.

    Warmup is where every camera is busy at once for most of each
    `warmup_window`, for at least `min_warmup`. The cameras are compared
    moment by moment before averaging: averaged first, play where the puck
    goes end to end every few seconds would look busy at both ends.
    """
    if len(rows) < 2:
        return None  # one camera can't tell a warmup from play
    all_busy = np.where(np.isnan(rows), 0.0, rows).min(axis=0) > settings.warmup_level
    window = max(1, round(settings.warmup_window / STEP))
    share = moving_average(all_busy.astype(np.float64), window)
    for start, stop in _runs(share > 0.5):
        if stop - start >= settings.min_warmup / STEP:
            return int(stop)
    return None


def _pick_breaks(
    spells: list[tuple[int, int]], first: int, last: int, count: int, settings: PeriodSettings
) -> list[int]:
    """Where up to `count` breaks end: spell ends after `first`, with every period
    at least `min_period` long, scored by their total quiet time less `evenness`
    times the spread of period lengths. Fewer when that many don't fit.

    The last period isn't counted in the spread, since it runs on to the end of
    the footage.
    """
    gap = settings.min_period / STEP
    usable = [(a, b) for a, b in spells if b - first >= gap and last - b >= gap]
    longest = sorted(usable, key=lambda s: s[1] - s[0], reverse=True)[:MAX_CANDIDATES]
    candidates = sorted(longest, key=lambda s: s[1])
    for k in range(min(count, len(candidates)), 0, -1):
        best: tuple[float, list[int]] | None = None
        for chosen in combinations(candidates, k):
            starts = [first, *(b for _, b in chosen)]
            lengths = [b - a for a, b in pairwise(starts)]
            if min(lengths) < gap or last - starts[-1] < gap:
                continue
            quiet = sum(b - a for a, b in chosen)
            score = quiet - settings.evenness * (max(lengths) - min(lengths))
            if best is None or score > best[0]:
                best = (score, starts[1:])
        if best is not None:
            return best[1]
    return []


def propose_periods(
    curves: list[ActivityCurve],
    count: int = DEFAULT_COUNT,
    settings: PeriodSettings = DEFAULT_SETTINGS,
) -> Proposal:
    """Propose where `count` periods start from every camera's activity curve.

    Proposes fewer when the footage is too short to fit `count` periods of
    `min_period` each between quiet spells.
    """
    if not curves:
        raise PeriodError("no activity curves to propose periods from")
    if count < 1:
        raise PeriodError("a game needs at least one period")
    origin, rows = _timeline(curves, settings.smoothing)

    def moment(index: int) -> datetime:
        return origin + timedelta(seconds=index * STEP)

    n = rows.shape[1]
    spells = _quiet_spells(rows, settings)
    warmup = _warmup_end(rows, settings)
    first = 0
    if warmup is not None:
        after = [b for _, b in spells if b > warmup and b < n]
        first = after[0] if after else warmup
    breaks = _pick_breaks(spells, first, n, count - 1, settings)

    # Start each period a little before play resumes, so the padding the
    # detector puts before a clip doesn't reach back into the previous period.
    begins = {b: max(a, b - round(settings.lead / STEP)) for a, b in spells}
    return Proposal(
        starts=[moment(begins.get(i, i)) for i in [first, *breaks]],
        quiet=[QuietSpell(moment(a), moment(b)) for a, b in spells],
        warmup_end=None if warmup is None else moment(warmup),
        footage_start=origin,
        footage_end=max(c.end for c in curves),
    )


# --- Saved periods ----------------------------------------------------------


@dataclass(frozen=True)
class Period:
    number: int  # 1, 2, 3, then 4 for overtime, ...
    start: datetime  # wall clock
    goalie_camera_id: int | None = None  # behind our goalie's net; None if unknown or unfilmed
    moved: bool = False  # the start was set by hand rather than proposed

    @property
    def name(self) -> str:
        return period_name(self.number)


def period_ends(periods: list[Period], footage_end: datetime) -> list[datetime]:
    """Where each period ends: at the next one's start, and the last with the footage."""
    if not periods:
        return []
    return [*(p.start for p in periods[1:]), footage_end]


def period_name(number: int) -> str:
    """P1, P2, P3, then OT, OT2, ..."""
    if number <= REGULATION:
        return f"P{number}"
    overtime = number - REGULATION
    return "OT" if overtime == 1 else f"OT{overtime}"


def parse_period(text: str) -> int:
    """A period number from `2`, `P2`, `OT` or `OT2`."""
    cleaned = text.strip().upper()
    if m := re.fullmatch(r"P?([1-9]\d*)", cleaned):
        return int(m.group(1))
    if m := re.fullmatch(r"OT([1-9]\d*)?", cleaned):
        return REGULATION + int(m.group(1) or 1)
    raise PeriodError(f"{text!r} isn't a period; use 1, P2, OT or OT2")


def other_end(camera_ids: list[int], camera_id: int | None) -> int | None:
    """The camera at the other end from `camera_id`.

    None when that end has no camera, or when there are more than two cameras
    and the far one can't be known.
    """
    if camera_id is None or len(camera_ids) != 2:
        return None
    return camera_ids[1] if camera_id == camera_ids[0] else camera_ids[0]


def goalie_for(camera_ids: list[int], first: int | None, number: int) -> int | None:
    """Our goalie's camera in period `number`, given P1's: teams change ends every period."""
    return first if number % 2 == 1 else other_end(camera_ids, first)


def periods_from_proposal(
    proposal: Proposal, saved: list[Period], camera_ids: list[int]
) -> list[Period]:
    """The proposed starts, keeping the goalie's camera already chosen for each period."""
    goalies = {p.number: p.goalie_camera_id for p in saved}
    first = goalies.get(1)
    return [
        Period(number, start, goalies.get(number, goalie_for(camera_ids, first, number)))
        for number, start in enumerate(proposal.starts, start=1)
    ]


def remove_last(periods: list[Period]) -> list[Period]:
    """Drop the last period, say an overtime the proposal found that wasn't played."""
    if not periods:
        raise PeriodError("the game has no periods to remove")
    return periods[:-1]


def move_start(
    periods: list[Period],
    number: int,
    start: datetime,
    footage: tuple[datetime, datetime],
    camera_ids: list[int],
) -> list[Period]:
    """Set period `number`'s start by hand. One past the last period adds a period."""
    if not 1 <= number <= len(periods) + 1:
        have = f"P1-{periods[-1].name}" if periods else "none yet"
        raise PeriodError(
            f"can't set {period_name(number)}'s start: the game has periods {have}, "
            "and only the next one can be added"
        )
    if not footage[0] <= start < footage[1]:
        raise PeriodError(
            f"{_clock(start)} is outside the game's footage "
            f"({_clock(footage[0])}-{_clock(footage[1])})"
        )
    index = number - 1
    if index > 0 and start <= periods[index - 1].start:
        before = periods[index - 1]
        raise PeriodError(
            f"{period_name(number)} must start after {before.name} ({_clock(before.start)})"
        )
    if index + 1 < len(periods) and start >= periods[index + 1].start:
        after = periods[index + 1]
        raise PeriodError(
            f"{period_name(number)} must start before {after.name} ({_clock(after.start)})"
        )
    if index == len(periods):
        first = periods[0].goalie_camera_id if periods else None
        return [*periods, Period(number, start, goalie_for(camera_ids, first, number), True)]
    changed = replace(periods[index], start=start, moved=True)
    return [*periods[:index], changed, *periods[index + 1 :]]


def set_goalie(periods: list[Period], camera_ids: list[int], first: int | None) -> list[Period]:
    """Put our goalie at `first`'s end in P1, alternating ends after that.

    None means P1's end isn't known, and so neither is any later period's.
    """
    return [replace(p, goalie_camera_id=goalie_for(camera_ids, first, p.number)) for p in periods]


def set_goalie_for(periods: list[Period], number: int, camera_id: int | None) -> list[Period]:
    """Put our goalie at `camera_id`'s end in one period only."""
    if not 1 <= number <= len(periods):
        raise PeriodError(f"the game has no {period_name(number)}")
    return [replace(p, goalie_camera_id=camera_id) if p.number == number else p for p in periods]


def resolve_clock(text: str, footage: tuple[datetime, datetime]) -> datetime:
    """The moment in the footage a local `HH:MM` or `HH:MM:SS` names."""
    try:
        of_day = time.fromisoformat(text.strip())
    except ValueError:
        raise PeriodError(f"{text!r} isn't a time; use HH:MM:SS, e.g. 12:15:30") from None
    first_day = footage[0].astimezone().date()
    for day in (first_day, first_day + timedelta(days=1)):
        moment = _local(day, of_day)
        if footage[0] <= moment < footage[1]:
            return moment
    raise PeriodError(
        f"{text} is outside the game's footage ({_clock(footage[0])}-{_clock(footage[1])})"
    )


def _local(day: date, of_day: time) -> datetime:
    return datetime.combine(day, of_day).astimezone()


def _clock(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M:%S")


def save_periods(conn: sqlite3.Connection, game_id: int, periods: list[Period]) -> None:
    """Replace the game's periods."""
    with conn:
        conn.execute("DELETE FROM period WHERE game_id = ?", (game_id,))
        conn.executemany(
            "INSERT INTO period (game_id, number, start, goalie_camera_id, moved)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    game_id,
                    p.number,
                    p.start.astimezone(UTC).isoformat(),
                    p.goalie_camera_id,
                    int(p.moved),
                )
                for p in periods
            ],
        )


def load_periods(conn: sqlite3.Connection, game_id: int) -> list[Period]:
    rows = conn.execute(
        "SELECT * FROM period WHERE game_id = ? ORDER BY number", (game_id,)
    ).fetchall()
    return [
        Period(
            number=r["number"],
            start=datetime.fromisoformat(r["start"]),
            goalie_camera_id=r["goalie_camera_id"],
            moved=bool(r["moved"]),
        )
        for r in rows
    ]
