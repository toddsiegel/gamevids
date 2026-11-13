from contextlib import closing
from datetime import date, timedelta

import pytest
from conftest import GAME, GAME_START, make_info, make_play_curve

from gamevids import db
from gamevids.detect import DEFAULT_SETTINGS, Segment, load_clips, save_clips
from gamevids.games import add_game, load_game
from gamevids.grouping import group_files
from gamevids.periods import Period, save_periods
from gamevids.triage import TriageError, load_triage


def at(seconds):
    return GAME_START + timedelta(seconds=seconds)


def offsets(clips):
    return [(c.start - GAME_START).total_seconds() for c in clips]


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


@pytest.fixture
def game(conn):
    """Cameras A and B over 3,200 s, with clips on both and P1-P3 starting at 300, 1290, 2290 s.

    Camera B was stopped from 1,700 to 1,800 s, so it has two recordings.
    """
    infos = [
        make_info("a.MP4", serial="CAMA", duration=3200),
        make_info("b1.MP4", serial="CAMB", duration=1700),
        make_info("b2.MP4", serial="CAMB", start=1800, duration=1400),
    ]
    game = load_game(conn, add_game(conn, "vs Lafayette", group_files(infos), date(2026, 10, 24)))
    a, b = game.camera_ids["A"], game.camera_ids["B"]
    curve = make_play_curve(0, GAME)
    clips = {
        a: [(100, 110), (400, 420), (1500, 1510), (2500, 2520)],
        b: [(200, 210), (350, 360), (1400, 1410), (1805, 1815), (2600, 2610)],
    }
    for camera_id, spans in clips.items():
        segments = [Segment(at(s), at(e), 0.1) for s, e in spans]
        save_clips(conn, camera_id, segments, DEFAULT_SETTINGS, curve)
    return game


def set_periods(conn, game, goalies):
    """P1-P3 with our goalie at camera `goalies[n]` in each."""
    starts = [300, 1290, 2290]
    periods = [
        Period(number, at(start), game.camera_ids[goalie] if goalie else None)
        for number, (start, goalie) in enumerate(zip(starts, goalies, strict=True), 1)
    ]
    save_periods(conn, game.id, periods)


def test_only_our_goalies_clips_are_triaged_in_wall_clock_order(conn, game):
    set_periods(conn, game, ["B", "A", "B"])

    triage = load_triage(conn, game)

    # Warmup clips (before P1) and far-end clips are left out.
    assert offsets(triage.clips) == [350, 1500, 2600]
    assert [p.name for p in triage.periods] == ["P1", "P2", "P3"]
    assert triage.missing is None
    assert triage.index == 0


def test_a_period_with_no_goalie_end_contributes_no_clips(conn, game):
    set_periods(conn, game, ["A", "B", None])

    assert offsets(load_triage(conn, game).clips) == [400, 1400, 1805]


@pytest.mark.parametrize(
    ("setup", "missing"),
    [("no clips", "clips"), ("no periods", "periods"), ("no goalie", "goalie")],
)
def test_says_what_is_missing(conn, game, setup, missing):
    if setup == "no clips":
        conn.execute("DELETE FROM clip")
    if setup == "no goalie":
        set_periods(conn, game, [None, None, None])

    triage = load_triage(conn, game)

    assert triage.missing == missing
    assert triage.clips == []
    assert triage.current is None


def test_decisions_are_saved_and_triage_resumes_at_the_first_undecided_clip(conn, game):
    set_periods(conn, game, ["B", "A", "B"])
    triage = load_triage(conn, game)

    triage.keep()
    triage.toggle_star()  # starring doesn't move on
    triage.discard()

    assert triage.index == 2
    saved = {c.id: c for c in load_clips(conn, game.camera_ids["A"])}
    assert saved[triage.clips[1].id].status == "discarded"
    assert saved[triage.clips[1].id].starred

    resumed = load_triage(conn, game)
    assert resumed.index == 2
    assert resumed.decided == 2
    assert [c.status for c in resumed.clips] == ["kept", "discarded", "candidate"]

    resumed.keep()
    assert resumed.current is None
    assert load_triage(conn, game).index == 3
    with pytest.raises(TriageError, match="every clip has been triaged"):
        resumed.keep()


def test_undo_puts_back_the_last_change_and_returns_to_its_clip(conn, game):
    set_periods(conn, game, ["B", "A", "B"])
    triage = load_triage(conn, game)
    triage.toggle_star()
    triage.keep()

    assert triage.undo()
    assert triage.index == 0
    assert triage.current.status == "candidate" and triage.current.starred
    assert triage.undo()
    assert not load_triage(conn, game).clips[0].starred
    assert not triage.undo()


def test_trims_reach_ten_seconds_past_the_clip_within_its_recording(conn, game):
    set_periods(conn, game, ["A", "B", None])
    triage = load_triage(conn, game)  # clips at 400, 1400 and 1805 s

    triage.set_in(at(392))
    triage.set_out(at(428))
    assert triage.current.in_point == at(392)
    assert triage.current.duration == 36
    with pytest.raises(TriageError, match="at most 10 s before"):
        triage.set_in(at(389))
    with pytest.raises(TriageError, match="at least 1 s before it ends"):
        triage.set_in(at(427.5))
    with pytest.raises(TriageError, match="at least 1 s after it starts"):
        triage.set_out(at(392.5))

    triage.go(2)  # camera B's second recording starts at 1,800 s
    assert triage.reach(triage.current) == (at(1800), at(1825))
    with pytest.raises(TriageError, match="at most 10 s before"):
        triage.set_in(at(1799))

    saved = load_triage(conn, game).clips[0]
    assert (saved.trim_in, saved.trim_out, saved.status) == (at(392), at(428), "candidate")
