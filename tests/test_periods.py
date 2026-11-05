from datetime import datetime, timedelta

import pytest
from conftest import GAME, GAME_START
from conftest import make_play_curve as make_curve

from gamevids import db
from gamevids.periods import (
    Period,
    PeriodError,
    PeriodSettings,
    goalie_for,
    load_periods,
    move_start,
    parse_period,
    period_name,
    periods_from_proposal,
    propose_periods,
    resolve_clock,
    save_periods,
    set_goalie,
    set_goalie_for,
)


def seconds(moment: datetime) -> float:
    return (moment - GAME_START).total_seconds()


def starts(proposal) -> list[float]:
    return [seconds(s) for s in proposal.starts]


def resumes(proposal) -> list[float]:
    """Where play resumes after each break: periods start `lead` (10 s) before."""
    return [t + 10 for t in starts(proposal)]


def assert_near(found: list[float], expected: list[float], tolerance: float = 3) -> None:
    assert len(found) == len(expected), (found, expected)
    for a, b in zip(found, expected, strict=True):
        assert abs(a - b) <= tolerance, (found, expected)


def test_periods_start_where_the_quiet_between_them_ends():
    proposal = propose_periods([make_curve(0, GAME), make_curve(1, GAME, offset=62.5)])

    assert_near(resumes(proposal), [300, 1290, 2290])
    assert proposal.warmup_end is not None
    assert 240 <= seconds(proposal.warmup_end) <= 260
    # The stoppage in P2 is a quiet spell too, just shorter than the breaks.
    assert_near([seconds(s.start) for s in proposal.quiet], [240, 1200, 1700, 2190])
    assert proposal.spell_at(proposal.starts[1]) == proposal.quiet[1]
    assert seconds(proposal.footage_end) == 3200


def test_a_long_stoppage_too_close_to_a_break_is_passed_over():
    # A 4-minute stoppage early in P1 is the longest quiet spell, but a break
    # there would leave P1 under the 8-minute minimum.
    script = [*GAME[:2], (300, 400, "play"), (400, 640, "quiet"), (640, 1200, "play"), *GAME[3:]]

    proposal = propose_periods([make_curve(0, script), make_curve(1, script)])

    assert_near(resumes(proposal), [300, 1290, 2290])


def test_uneven_periods_lose_to_breaks_that_split_the_game_evenly():
    # P3 is played with a stopped clock, and a 3-minute stoppage in it is
    # longer than either break. Taking the second break and that stoppage
    # would make P1 33 minutes and P2 13; the real breaks make them 16.5 and
    # 16.7.
    script = [
        *GAME[:3],
        (1200, 1290, "quiet"),
        (1290, 2190, "play"),
        (2190, 2290, "quiet"),
        (2290, 2900, "play"),
        (2900, 3080, "quiet"),
        (3080, 4000, "play"),
    ]
    curves = [make_curve(0, script), make_curve(1, script)]

    assert_near(resumes(propose_periods(curves)), [300, 1290, 2290])
    by_quiet_alone = propose_periods(curves, settings=PeriodSettings(evenness=0))
    assert_near(resumes(by_quiet_alone), [300, 2290, 3080])


def test_movement_inside_a_quiet_spell_splits_it_only_if_it_reaches_the_blip_level():
    # 8 s with both zones 5% busy, 30 s before P3's puck drop. With the
    # default 4% blip level that's play, so P3 starts before it; at 6% it's
    # a blip, and the quiet spell carries on to the drop.
    script = [*GAME[:7], (2190, 2260, "quiet"), (2260, 2268, "warmup"), *GAME[8:]]
    curves = [make_curve(0, script), make_curve(1, script)]

    split = propose_periods(curves)
    merged = propose_periods(curves, settings=PeriodSettings(blip_level=0.06))

    assert_near(resumes(split), [300, 1290, 2260])
    assert_near(resumes(merged), [300, 1290, 2290])


def test_cameras_that_are_all_stopped_count_as_quiet():
    script = [*GAME[:3], (1200, 1290, "off"), *GAME[4:]]

    proposal = propose_periods([make_curve(0, script), make_curve(1, script)])

    assert_near(resumes(proposal), [300, 1290, 2290])


def test_without_a_warmup_p1_starts_with_the_footage():
    script = GAME[2:]
    two = propose_periods([make_curve(0, script, offset=300), make_curve(1, script, offset=300)])
    one = propose_periods([make_curve(0, GAME)])  # one camera can't tell warmup from play

    assert two.warmup_end is None
    assert starts(two)[0] == 300
    assert_near(resumes(two)[1:], [1290, 2290])
    assert one.warmup_end is None
    assert starts(one)[0] == 0


def test_more_periods_than_fit_gives_fewer():
    curves = [make_curve(0, GAME), make_curve(1, GAME)]

    assert len(propose_periods(curves, count=5).starts) == 3
    assert len(propose_periods(curves, count=1).starts) == 1


def test_overtime_takes_the_next_longest_spell_that_fits():
    script = [*GAME[:-1], (2290, 3200, "play"), (3200, 3290, "quiet"), (3290, 3800, "play")]
    settings = PeriodSettings(min_period=300)

    proposal = propose_periods([make_curve(0, script), make_curve(1, script)], 4, settings)

    assert_near(resumes(proposal), [300, 1290, 2290, 3290])


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quiet_level": 1.5}, "quiet_level must be between 0 and 1"),
        ({"min_period": -1}, "min_period can't be negative"),
        ({"merge_gap": "15"}, "merge_gap must be a number"),
    ],
)
def test_settings_are_checked(changes, message):
    with pytest.raises(PeriodError, match=message):
        PeriodSettings(**changes)


def test_proposing_needs_curves_and_a_period():
    with pytest.raises(PeriodError, match="no activity curves"):
        propose_periods([])
    with pytest.raises(PeriodError, match="at least one period"):
        propose_periods([make_curve(0, GAME)], count=0)


@pytest.mark.parametrize(
    ("text", "number"),
    [("1", 1), ("p2", 2), (" P3 ", 3), ("ot", 4), ("OT", 4), ("OT2", 5), ("6", 6)],
)
def test_period_names_round_trip(text, number):
    assert parse_period(text) == number
    assert parse_period(period_name(number)) == number


@pytest.mark.parametrize("text", ["0", "P0", "OT0", "first", ""])
def test_bad_period_names_are_refused(text):
    with pytest.raises(PeriodError, match="isn't a period"):
        parse_period(text)


def test_our_goalie_changes_ends_every_period():
    assert [goalie_for([10, 20], 20, n) for n in range(1, 5)] == [20, 10, 20, 10]
    # One camera films our goalie every other period.
    assert [goalie_for([10], 10, n) for n in range(1, 4)] == [10, None, 10]
    # With three cameras the far end can't be known.
    assert [goalie_for([10, 20, 30], 10, n) for n in range(1, 4)] == [10, None, 10]
    assert goalie_for([10, 20], None, 1) is None


def at(offset: float) -> datetime:
    return GAME_START + timedelta(seconds=offset)


PERIODS = [Period(1, at(300)), Period(2, at(1290)), Period(3, at(2290))]
FOOTAGE = (at(0), at(3200))


def test_setting_the_goalie_alternates_and_one_period_can_differ():
    periods = set_goalie(PERIODS, [10, 20], 20)
    assert [p.goalie_camera_id for p in periods] == [20, 10, 20]

    periods = set_goalie_for(periods, 3, 10)
    assert [p.goalie_camera_id for p in periods] == [20, 10, 10]

    with pytest.raises(PeriodError, match="the game has no OT"):
        set_goalie_for(periods, 4, 10)


def test_a_new_proposal_keeps_our_goalies_ends():
    saved = set_goalie_for(set_goalie(PERIODS, [10, 20], 10), 2, 10)
    proposal = propose_periods([make_curve(0, GAME), make_curve(1, GAME)], 3)
    proposal.starts.append(at(3100))  # as if it found overtime

    periods = periods_from_proposal(proposal, saved, [10, 20])

    assert [p.goalie_camera_id for p in periods] == [10, 10, 10, 20]
    assert periods[0].start == proposal.starts[0]
    assert not any(p.moved for p in periods)


def test_moving_a_start_by_hand():
    periods = move_start(PERIODS, 2, at(1300), FOOTAGE, [10, 20])

    assert periods[1] == Period(2, at(1300), moved=True)
    assert periods[0] == PERIODS[0] and periods[2] == PERIODS[2]


def test_the_period_after_the_last_can_be_added():
    with_goalie = set_goalie(PERIODS, [10, 20], 10)

    periods = move_start(with_goalie, 4, at(3150), FOOTAGE, [10, 20])

    assert periods[3] == Period(4, at(3150), goalie_camera_id=20, moved=True)
    assert move_start([], 1, at(0), FOOTAGE, [10]) == [Period(1, at(0), moved=True)]


@pytest.mark.parametrize(
    ("number", "offset", "message"),
    [
        (2, 300, r"P2 must start after P1 \("),
        (2, 2290, r"P2 must start before P3 \("),
        (1, 3300, "is outside the game's footage"),
        (1, -1, "is outside the game's footage"),
        (5, 3100, "can't set OT2's start: the game has periods P1-P3"),
    ],
)
def test_moving_a_start_out_of_order_is_refused(number, offset, message):
    with pytest.raises(PeriodError, match=message):
        move_start(PERIODS, number, at(offset), FOOTAGE, [10, 20])


def test_clock_times_resolve_within_the_footage(denver):
    footage = (GAME_START, GAME_START + timedelta(hours=1))
    assert resolve_clock("12:15:27", footage) == at(1301)
    assert resolve_clock("12:15", footage) == at(1274)

    late = (at(12 * 3600), at(13 * 3600))  # 23:53:46 to 00:53:46
    assert resolve_clock("00:10", late) == at(12 * 3600 + 974)

    with pytest.raises(PeriodError, match="is outside the game's footage"):
        resolve_clock("13:00", footage)
    with pytest.raises(PeriodError, match="isn't a time"):
        resolve_clock("noon", footage)


@pytest.fixture
def conn(library):
    conn = db.connect(library)
    with conn:
        conn.execute("INSERT INTO game (id, title, date, created_at) VALUES (1, 't', 'd', 'c')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (10, 1, 'A')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (20, 1, 'B')")
    return conn


def test_periods_are_saved_and_replaced(conn):
    periods = move_start(set_goalie(PERIODS, [10, 20], 10), 2, at(1300), FOOTAGE, [10, 20])

    save_periods(conn, 1, periods)
    assert load_periods(conn, 1) == periods

    save_periods(conn, 1, periods[:2])
    assert load_periods(conn, 1) == periods[:2]


def test_removing_a_camera_forgets_it_as_our_goalies_end(conn):
    save_periods(conn, 1, set_goalie(PERIODS, [10, 20], 10))
    with conn:
        conn.execute("DELETE FROM camera WHERE id = 20")

    assert [p.goalie_camera_id for p in load_periods(conn, 1)] == [10, None, 10]
