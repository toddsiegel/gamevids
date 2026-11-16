"""Triage: decide which candidate clips go in the game video.

Only our goalie's clips are triaged: a clip is ours when it starts during a
period, on the camera behind our goalie's net in that period. Clips before
P1 (the warmup) and at the far end stay candidates and never reach the game
video. Our clips play in wall-clock order, so triage watches the game the
way the video will show it.

Each clip is kept or discarded, optionally starred, and optionally trimmed:
its in and out points can move anywhere within `REACH` seconds of what
detection found, as long as they stay in the clip's recording. Every change
is saved at once, so quitting midway loses nothing, and triage resumes at
the first clip still undecided. A decision can be cleared, for one clip or
all of them, which makes the clip undecided again (its star and trim stay).
"""

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from gamevids.detect import Clip, load_clips
from gamevids.games import Game
from gamevids.periods import Period, load_periods, period_ends

REACH = 10.0  # seconds of footage either side of a detected clip that trimming can extend into
MIN_LENGTH = 1.0  # seconds; the shortest a trimmed clip can be

Span = tuple[datetime, datetime]


class TriageError(RuntimeError):
    pass


def goalie_clips(
    clips: list[Clip], periods: list[Period], footage_end: datetime
) -> list[tuple[Clip, Period]]:
    """Our goalie's clips in wall-clock order, each with the period it starts in."""
    ours = []
    ends = period_ends(periods, footage_end)
    for clip in sorted(clips, key=lambda c: (c.start, c.camera_id, c.id)):
        for period, end in zip(periods, ends, strict=True):
            if period.start <= clip.start < end:
                if clip.camera_id == period.goalie_camera_id:
                    ours.append((clip, period))
                break
    return ours


def save_triage(conn: sqlite3.Connection, clip: Clip) -> None:
    """Save a clip's status, star and trim."""

    def ts(moment: datetime | None) -> str | None:
        return moment.astimezone(UTC).isoformat() if moment else None

    with conn:
        conn.execute(
            "UPDATE clip SET trim_in = ?, trim_out = ?, status = ?, starred = ? WHERE id = ?",
            (ts(clip.trim_in), ts(clip.trim_out), clip.status, int(clip.starred), clip.id),
        )


@dataclass
class Triage:
    """Our goalie's clips in a game and where triage is up to.

    `missing` says why there's nothing to triage: `"clips"` when no camera
    has been detected, `"periods"` when the game has none, or `"goalie"` when
    no period says which end our goalie is at.
    """

    conn: sqlite3.Connection
    clips: list[Clip]
    periods: list[Period]  # the period each clip starts in, in step with `clips`
    recordings: dict[int, list[Span]]  # each camera's recordings, by camera id
    missing: str | None = None
    index: int = 0  # the clip being triaged; len(clips) once every clip is done
    # For undo: each change's index, and the rows it changed with their clips before it.
    history: list[tuple[int, list[tuple[int, Clip]]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.index = self.resume_index()

    @property
    def current(self) -> Clip | None:
        return self.clips[self.index] if self.index < len(self.clips) else None

    @property
    def period(self) -> Period | None:
        return self.periods[self.index] if self.index < len(self.clips) else None

    def resume_index(self) -> int:
        """The first clip not yet kept or discarded, or the end when every one is."""
        return next(
            (i for i, c in enumerate(self.clips) if c.status == "candidate"), len(self.clips)
        )

    @property
    def decided(self) -> int:
        return sum(c.status != "candidate" for c in self.clips)

    def go(self, index: int) -> None:
        self.index = min(max(index, 0), len(self.clips))

    def reach(self, clip: Clip) -> Span:
        """How far `clip` can be trimmed out to: `REACH` beyond it, within its recording."""
        low = clip.start - timedelta(seconds=REACH)
        high = clip.end + timedelta(seconds=REACH)
        for start, end in self.recordings.get(clip.camera_id, []):
            if start <= clip.start <= end:
                return max(low, start), min(high, end)
        return low, high

    # -- changes -----------------------------------------------------------------

    def _save(self, changes: dict[int, Clip]) -> bool:
        """Save the clips in `changes` by row, as one undoable change. False if none changed."""
        before = [(row, self.clips[row]) for row, c in changes.items() if c != self.clips[row]]
        if not before:
            return False
        self.history.append((self.index, before))
        for row, _ in before:
            save_triage(self.conn, changes[row])
            self.clips[row] = changes[row]
        return True

    def _change(self, clip: Clip, *, advance: bool = False) -> None:
        self._save({self.index: clip})
        if advance:
            self.go(self.index + 1)

    def _clip(self) -> Clip:
        if self.current is None:
            raise TriageError("every clip has been triaged")
        return self.current

    def keep(self) -> None:
        """Keep the current clip and move on to the next."""
        self._change(replace(self._clip(), status="kept"), advance=True)

    def discard(self) -> None:
        """Discard the current clip and move on to the next."""
        self._change(replace(self._clip(), status="discarded"), advance=True)

    def clear(self) -> None:
        """Make the current clip undecided again, staying on it."""
        self._change(replace(self._clip(), status="candidate"))

    def clear_all(self) -> bool:
        """Make every clip undecided again and go back to the first. False if none was decided."""
        cleared = self._save(
            {row: replace(c, status="candidate") for row, c in enumerate(self.clips)}
        )
        if cleared:
            self.go(0)
        return cleared

    def toggle_star(self) -> None:
        clip = self._clip()
        self._change(replace(clip, starred=not clip.starred))

    def set_in(self, moment: datetime) -> None:
        """Start the current clip at `moment`."""
        clip = self._clip()
        low, _ = self.reach(clip)
        if moment < low:
            raise TriageError(f"the clip can start at most {REACH:g} s before it was found")
        if (clip.out_point - moment).total_seconds() < MIN_LENGTH:
            raise TriageError(f"the clip must start at least {MIN_LENGTH:g} s before it ends")
        self._change(replace(clip, trim_in=moment))

    def set_out(self, moment: datetime) -> None:
        """End the current clip at `moment`."""
        clip = self._clip()
        _, high = self.reach(clip)
        if moment > high:
            raise TriageError(f"the clip can end at most {REACH:g} s after it was found")
        if (moment - clip.in_point).total_seconds() < MIN_LENGTH:
            raise TriageError(f"the clip must end at least {MIN_LENGTH:g} s after it starts")
        self._change(replace(clip, trim_out=moment))

    def undo(self) -> bool:
        """Put back the last change and go to its clip. False when there's nothing to undo."""
        if not self.history:
            return False
        index, before = self.history.pop()
        for row, clip in before:
            save_triage(self.conn, clip)
            self.clips[row] = clip
        self.index = index
        return True


def load_triage(conn: sqlite3.Connection, game: Game) -> Triage:
    """The game's triage, at the first clip still undecided."""
    cameras = game.grouping.cameras
    clips = [c for cam in cameras for c in load_clips(conn, game.camera_ids[cam.label])]
    periods = load_periods(conn, game.id)
    recordings = {
        game.camera_ids[cam.label]: [(r.start, r.end) for r in cam.recordings] for cam in cameras
    }
    missing = None
    if not clips:
        missing = "clips"
    elif not periods:
        missing = "periods"
    elif all(p.goalie_camera_id is None for p in periods):
        missing = "goalie"
    ours = goalie_clips(clips, periods, game.grouping.footage[1]) if cameras else []
    return Triage(
        conn,
        [clip for clip, _ in ours],
        [period for _, period in ours],
        recordings,
        missing,
    )
