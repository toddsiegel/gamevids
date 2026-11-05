from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from conftest import GAME_START

from gamevids import db
from gamevids.activity import ActivityCurve, ScanSettings
from gamevids.detect import (
    DetectError,
    DetectSettings,
    detect_clips,
    load_clips,
    load_detection,
    save_clips,
)

RATE = 10.0
WHOLE_FRAME = ((0, 0), (1, 0), (1, 1), (0, 1))
# No smoothing or padding, so clips land exactly on the bursts.
PLAIN = DetectSettings(
    threshold=0.05, smoothing=0, min_duration=1, merge_gap=2, pad_before=0, pad_after=0
)


def make_curve(seconds: float, bursts=(), gaps=()) -> ActivityCurve:
    """A quiet curve with `(start, end, level)` bursts and `(start, end)` NaN gaps, in seconds."""
    values = np.full(round(seconds * RATE), 0.001, dtype=np.float32)
    for start, end, level in bursts:
        values[round(start * RATE) : round(end * RATE)] = level
    for start, end in gaps:
        values[round(start * RATE) : round(end * RATE)] = np.nan
    return ActivityCurve(
        GAME_START, RATE, values, WHOLE_FRAME, ScanSettings(), datetime(2026, 11, 5, tzinfo=UTC)
    )


def spans(segments) -> list[tuple[float, float]]:
    """Each segment as seconds from the start of the curve."""
    return [
        (
            round((s.start - GAME_START).total_seconds(), 3),
            round((s.end - GAME_START).total_seconds(), 3),
        )
        for s in segments
    ]


def test_bursts_above_the_threshold_become_clips_scored_by_their_peak():
    curve = make_curve(60, bursts=[(10, 13, 0.2), (13, 15, 0.08), (30, 35, 0.06)])

    clips = detect_clips(curve, PLAIN)

    assert spans(clips) == [(10, 15), (30, 35)]
    assert [round(c.score, 3) for c in clips] == [0.2, 0.06]
    assert clips[0].duration == 5


def test_short_bursts_are_dropped():
    curve = make_curve(60, bursts=[(10, 10.5, 0.3), (30, 31, 0.3)])

    assert spans(detect_clips(curve, PLAIN)) == [(30, 31)]


def test_bursts_within_the_merge_gap_join_before_the_length_check():
    # Three 0.5 s bursts, each too short alone, 1.5 s apart: one 3.5 s clip.
    curve = make_curve(60, bursts=[(10, 10.5, 0.3), (12, 12.5, 0.3), (14, 14.5, 0.3)])

    assert spans(detect_clips(curve, PLAIN)) == [(10, 14.5)]


def test_padding_is_added_and_padded_clips_never_end_up_closer_than_the_merge_gap():
    settings = DetectSettings(
        threshold=0.05, smoothing=0, min_duration=1, merge_gap=2, pad_before=3, pad_after=2
    )
    # 6 s apart: separate bursts, but only 1 s apart once padded, so they merge.
    curve = make_curve(120, bursts=[(10, 12, 0.3), (18, 20, 0.3), (60, 62, 0.3)])

    assert spans(detect_clips(curve, settings)) == [(7, 22), (57, 64)]


def test_clips_stay_inside_their_recording():
    settings = DetectSettings(
        threshold=0.05, smoothing=0, min_duration=1, merge_gap=5, pad_before=3, pad_after=3
    )
    # Bursts touching both edges of a 2 s gap between recordings, and the curve's ends.
    curve = make_curve(
        60, bursts=[(0, 2, 0.3), (18, 20, 0.3), (22, 24, 0.3), (58, 60, 0.3)], gaps=[(20, 22)]
    )

    assert spans(detect_clips(curve, settings)) == [(0, 5), (15, 20), (22, 27), (55, 60)]


def test_smoothing_spreads_a_spike_below_the_threshold():
    curve = make_curve(60, bursts=[(10, 11, 0.08)])
    smoothed = DetectSettings(threshold=0.05, smoothing=4, min_duration=0.5)

    assert detect_clips(curve, smoothed) == []
    assert len(detect_clips(curve, PLAIN)) == 1


def test_a_quiet_or_empty_curve_has_no_clips():
    assert detect_clips(make_curve(60)) == []
    assert detect_clips(make_curve(10, gaps=[(0, 10)])) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"threshold": 0}, "threshold must be between 0 and 1"),
        ({"threshold": 4}, "threshold must be between 0 and 1"),
        ({"merge_gap": -1}, "merge_gap can't be negative"),
        ({"smoothing": "2"}, "smoothing must be a number"),
        ({"pad_after": True}, "pad_after must be a number"),
    ],
)
def test_settings_are_checked(changes, message):
    with pytest.raises(DetectError, match=message):
        DetectSettings(**changes)


@pytest.fixture
def conn(library):
    conn = db.connect(library)
    with conn:
        conn.execute("INSERT INTO game (id, title, date, created_at) VALUES (1, 't', 'd', 'c')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (1, 1, 'A')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (2, 1, 'B')")
    return conn


def test_clips_and_their_detection_are_saved_and_loaded(conn):
    curve = make_curve(60, bursts=[(30, 35, 0.06), (10, 15, 0.2)])
    segments = detect_clips(curve, PLAIN)

    save_clips(conn, 1, segments, PLAIN, curve)
    clips = load_clips(conn, 1)
    detection = load_detection(conn, 1)

    assert [(c.start, c.end, c.score) for c in clips] == [
        (s.start, s.end, pytest.approx(s.score)) for s in segments
    ]
    assert all(c.status == "candidate" and not c.triaged for c in clips)
    assert clips[0].start == GAME_START + timedelta(seconds=10)
    assert detection is not None
    assert detection.settings == PLAIN
    assert detection.curve_scanned_at == curve.scanned_at
    assert load_clips(conn, 2) == []
    assert load_detection(conn, 2) is None


def test_detecting_again_replaces_untriaged_clips(conn):
    curve = make_curve(60, bursts=[(10, 15, 0.2), (30, 35, 0.06)])
    save_clips(conn, 1, detect_clips(curve, PLAIN), PLAIN, curve)
    save_clips(conn, 2, detect_clips(curve, PLAIN), PLAIN, curve)

    stricter = DetectSettings(threshold=0.1, smoothing=0)
    save_clips(conn, 1, detect_clips(curve, stricter), stricter, curve)

    assert len(load_clips(conn, 1)) == 1
    assert len(load_clips(conn, 2)) == 2  # the other camera is untouched
    detection = load_detection(conn, 1)
    assert detection is not None and detection.settings == stricter


@pytest.mark.parametrize(
    "triage",
    ["status = 'kept'", "status = 'discarded'", "starred = 1", "trim_in = start"],
)
def test_triaged_clips_are_only_replaced_when_asked(conn, triage):
    curve = make_curve(60, bursts=[(10, 15, 0.2), (30, 35, 0.06)])
    save_clips(conn, 1, detect_clips(curve, PLAIN), PLAIN, curve)
    with conn:
        conn.execute(f"UPDATE clip SET {triage} WHERE id = 1")

    with pytest.raises(DetectError, match="1 of this camera's clips have been triaged"):
        save_clips(conn, 1, [], PLAIN, curve)
    assert len(load_clips(conn, 1)) == 2

    save_clips(conn, 1, [], PLAIN, curve, replace_triaged=True)
    assert load_clips(conn, 1) == []
