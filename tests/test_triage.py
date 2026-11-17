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

    triage.discard()
    triage.toggle_goal_against()  # marks, keeps and moves on, like keep

    assert triage.index == 2
    saved = {c.id: c for c in load_clips(conn, game.camera_ids["A"])}
    assert saved[triage.clips[1].id].status == "kept"
    assert saved[triage.clips[1].id].goal_against

    resumed = load_triage(conn, game)
    assert resumed.index == 2
    assert resumed.decided == 2
    assert [c.status for c in resumed.clips] == ["discarded", "kept", "candidate"]

    resumed.keep()
    assert resumed.current is None
    assert load_triage(conn, game).index == 3
    with pytest.raises(TriageError, match="every clip has been triaged"):
        resumed.keep()


def test_undo_puts_back_the_last_change_and_returns_to_its_clip(conn, game):
    set_periods(conn, game, ["B", "A", "B"])
    triage = load_triage(conn, game)
    triage.toggle_goal_against()
    triage.keep()

    assert triage.undo()
    assert triage.index == 1
    assert triage.current.status == "candidate"
    assert triage.undo()  # the mark and the keep together
    assert triage.index == 0
    assert triage.current.status == "candidate" and not triage.current.goal_against
    assert not load_triage(conn, game).clips[0].goal_against
    assert not triage.undo()


def test_marking_a_marked_clip_again_unmarks_it_and_stays(conn, game):
    set_periods(conn, game, ["B", "A", "B"])
    triage = load_triage(conn, game)
    triage.toggle_goal_against()
    triage.go(0)

    triage.toggle_goal_against()

    assert triage.index == 0
    clip = load_triage(conn, game).clips[0]
    assert clip.status == "kept" and not clip.goal_against


def test_clearing_makes_one_clip_or_all_undecided_and_undoes_as_one_change(conn, game):
    set_periods(conn, game, ["B", "A", "B"])
    triage = load_triage(conn, game)
    triage.keep()
    triage.toggle_goal_against()
    triage.keep()
    triage.go(1)

    triage.clear()
    assert triage.index == 1  # clearing stays on the clip
    assert [c.status for c in load_triage(conn, game).clips] == ["kept", "candidate", "kept"]
    assert triage.clips[1].goal_against  # the mark stays

    assert triage.clear_all()
    assert triage.index == 0
    assert triage.decided == 0
    assert load_triage(conn, game).decided == 0
    assert not triage.clear_all()  # nothing left to clear

    assert triage.undo()  # the whole Clear All at once
    assert triage.index == 1
    assert [c.status for c in load_triage(conn, game).clips] == ["kept", "candidate", "kept"]
    assert triage.undo()
    assert triage.clips[1].status == "kept" and triage.clips[1].goal_against


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


def split_clips(conn, game):
    """Camera A's P1 clip split in three, at 400-420, 430-440 and 450-460 s."""
    a = game.camera_ids["A"]
    spans = [(100, 110), (400, 420), (430, 440), (450, 460), (1500, 1510), (2500, 2520)]
    segments = [Segment(at(s), at(e), 0.1) for s, e in spans]
    save_clips(conn, a, segments, DEFAULT_SETTINGS, make_play_curve(0, GAME))
    set_periods(conn, game, ["A", "B", "B"])  # P2 has B's clips at 1,400 and 1,805 s


def test_joining_runs_from_the_first_in_point_to_the_last_out_point(conn, game):
    split_clips(conn, game)
    triage = load_triage(conn, game)
    assert offsets(triage.clips) == [400, 430, 450, 1400, 1805, 2600]
    triage.set_in(at(398))
    triage.discard()
    triage.toggle_goal_against()
    triage.go(2)
    triage.set_out(at(462))
    ids = [c.id for c in triage.clips]

    triage.join(2, 0)  # either way round

    assert triage.index == 0
    joined = triage.clips[0]
    assert (joined.in_point, joined.out_point) == (at(398), at(462))
    assert joined.status == "kept" and joined.goal_against
    assert triage.reach(joined) == (at(390), at(470))
    assert [p.name for p in triage.periods] == ["P1", "P2", "P2", "P3"]
    reloaded = load_triage(conn, game)
    assert [c.id for c in reloaded.clips] == [ids[0], *ids[3:]]
    assert reloaded.clips[0] == joined

    assert triage.undo()  # puts back the deleted clips, ids and all
    assert triage.index == 2
    assert load_triage(conn, game).clips == triage.clips
    assert [c.id for c in triage.clips] == ids
    assert triage.clips[2].out_point == at(462)


@pytest.mark.parametrize(
    ("first", "last", "error"),
    [
        (1, 1, "two or more"),
        (2, 3, "one camera"),
        (3, 4, "one recording"),
    ],
)
def test_joining_needs_neighbours_on_one_camera_and_recording(conn, game, first, last, error):
    split_clips(conn, game)
    triage = load_triage(conn, game)

    with pytest.raises(TriageError, match=error):
        triage.join(first, last)
    assert len(triage.clips) == 6 and not triage.history


def test_joining_across_periods_is_refused(conn, game):
    set_periods(conn, game, ["A", "A", "A"])
    triage = load_triage(conn, game)  # 400, 1500 and 2500 s

    with pytest.raises(TriageError, match="one period"):
        triage.join(0, 1)


@pytest.mark.parametrize(
    ("statuses", "joined"),
    [
        (["discarded", "discarded"], "discarded"),
        (["discarded", "candidate"], "candidate"),
        (["discarded", "kept"], "kept"),
    ],
)
def test_a_joined_clip_is_kept_if_any_part_was(conn, game, statuses, joined):
    split_clips(conn, game)
    triage = load_triage(conn, game)
    for status in statuses:
        {"discarded": triage.discard, "kept": triage.keep, "candidate": lambda: triage.go(1)}[
            status
        ]()

    triage.join(0, 1)

    assert triage.clips[0].status == joined
