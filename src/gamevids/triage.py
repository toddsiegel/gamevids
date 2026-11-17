"""Triage: decide which candidate clips go in the game video.

Only our goalie's clips are triaged: a clip is ours when it starts during a
period, on the camera behind our goalie's net in that period. Clips before
P1 (the warmup) and at the far end stay candidates and never reach the game
video. Our clips play in wall-clock order, so triage watches the game the
way the video will show it.

Each clip is kept or discarded, and optionally trimmed; a kept clip can
be marked as a goal against, which keeps it too:
its in and out points can move anywhere within `REACH` seconds of what
detection found, as long as they stay in the clip's recording. Every change
is saved at once, so quitting midway loses nothing, and triage resumes at
the first clip still undecided. A decision can be cleared, for one clip or
all of them, which makes the clip undecided again (its goal against mark and
trim stay).

Neighbouring clips that detection split apart can be joined into one, from
the first one's in point to the last one's out point, as long as they're on
one camera in one period and recording. The joined clip takes the first
one's row; the others are deleted (undo puts them back).
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


def _ts(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat() if moment else None


def _update(conn: sqlite3.Connection, clip: Clip) -> None:
    conn.execute(
        "UPDATE clip SET end = ?, trim_in = ?, trim_out = ?, score = ?, status = ?,"
        " goal_against = ? WHERE id = ?",
        (
            _ts(clip.end),
            _ts(clip.trim_in),
            _ts(clip.trim_out),
            clip.score,
            clip.status,
            int(clip.goal_against),
            clip.id,
        ),
    )


def _insert(conn: sqlite3.Connection, clip: Clip) -> None:
    """Put back a deleted clip under its old id."""
    conn.execute(
        "INSERT INTO clip (id, camera_id, start, end, trim_in, trim_out, score, status,"
        " goal_against) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            clip.id,
            clip.camera_id,
            _ts(clip.start),
            _ts(clip.end),
            _ts(clip.trim_in),
            _ts(clip.trim_out),
            clip.score,
            clip.status,
            int(clip.goal_against),
        ),
    )


def save_triage(conn: sqlite3.Connection, clip: Clip) -> None:
    """Save a clip's status, goal against mark, trim, and end (which joining moves)."""
    with conn:
        _update(conn, clip)


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
    # For undo: each change's index, and the clips and periods before it.
    history: list[tuple[int, list[Clip], list[Period]]] = field(default_factory=list)

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

    def recording(self, clip: Clip) -> Span | None:
        """The recording `clip` starts in, if its camera's recordings are known."""
        for start, end in self.recordings.get(clip.camera_id, []):
            if start <= clip.start <= end:
                return start, end
        return None

    def reach(self, clip: Clip) -> Span:
        """How far `clip` can be trimmed out to: `REACH` beyond it, within its recording."""
        low = clip.start - timedelta(seconds=REACH)
        high = clip.end + timedelta(seconds=REACH)
        recording = self.recording(clip)
        if recording is not None:
            return max(low, recording[0]), min(high, recording[1])
        return low, high

    # -- changes -----------------------------------------------------------------

    def _write(self, clips: list[Clip], periods: list[Period]) -> None:
        """Make the clips `clips`, saving rows that changed, went or came back."""
        old = {c.id: c for c in self.clips}
        with self.conn:
            for gone in old.keys() - {c.id for c in clips}:
                self.conn.execute("DELETE FROM clip WHERE id = ?", (gone,))
            for clip in clips:
                if clip.id not in old:
                    _insert(self.conn, clip)
                elif clip != old[clip.id]:
                    _update(self.conn, clip)
        self.clips, self.periods = list(clips), list(periods)

    def _commit(self, clips: list[Clip], periods: list[Period]) -> bool:
        """Save `clips` as one undoable change. False if nothing changed."""
        if clips == self.clips:
            return False
        self.history.append((self.index, self.clips, self.periods))
        self._write(clips, periods)
        return True

    def _save(self, changes: dict[int, Clip]) -> bool:
        """Save the clips in `changes` by row, as one undoable change. False if none changed."""
        clips = [changes.get(row, c) for row, c in enumerate(self.clips)]
        return self._commit(clips, self.periods)

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

    def toggle_goal_against(self) -> None:
        """Mark the current clip as a goal against, keep it and move on to the next.

        On a clip already marked, take the mark off and stay on it; it stays kept.
        """
        clip = self._clip()
        if clip.goal_against:
            self._change(replace(clip, goal_against=False))
        else:
            self._change(replace(clip, goal_against=True, status="kept"), advance=True)

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

    def join(self, first: int, last: int) -> None:
        """Join the clips in rows `first` to `last` into one, and go to it.

        It runs from the first one's in point to the last one's out point. It's
        kept if any of them was (or was a goal against), discarded if all of
        them were, and otherwise undecided.
        """
        first, last = sorted((first, last))
        first, last = max(first, 0), min(last, len(self.clips) - 1)
        if last <= first:
            raise TriageError("pick two or more clips to join")
        group = self.clips[first : last + 1]
        head, tail = group[0], group[-1]
        if any(c.camera_id != head.camera_id for c in group):
            raise TriageError("only clips on one camera can be joined")
        if any(p != self.periods[first] for p in self.periods[first : last + 1]):
            raise TriageError("only clips in one period can be joined")
        if any(self.recording(c) != self.recording(head) for c in group):
            raise TriageError("only clips in one recording can be joined")
        goal_against = any(c.goal_against for c in group)
        if goal_against or any(c.status == "kept" for c in group):
            status = "kept"
        elif all(c.status == "discarded" for c in group):
            status = "discarded"
        else:
            status = "candidate"
        joined = replace(
            head,
            end=tail.end,
            trim_out=tail.trim_out,
            score=max(c.score for c in group),
            status=status,
            goal_against=goal_against,
        )
        if joined.duration < MIN_LENGTH:
            raise TriageError(f"the joined clip would be shorter than {MIN_LENGTH:g} s")
        self._commit(
            [*self.clips[:first], joined, *self.clips[last + 1 :]],
            [*self.periods[: first + 1], *self.periods[last + 1 :]],
        )
        self.go(first)

    def undo(self) -> bool:
        """Put back the last change and go to its clip. False when there's nothing to undo."""
        if not self.history:
            return False
        index, clips, periods = self.history.pop()
        self._write(clips, periods)
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
