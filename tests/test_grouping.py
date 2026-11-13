from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import FULL_CHAPTER, GAME_START, make_info

from gamevids.grouping import (
    Assignment,
    FileKind,
    GroupingError,
    assign_files,
    classify_name,
    estimate_start,
    group_files,
    timecode_seconds,
)


@pytest.mark.parametrize(
    ("name", "kind", "period"),
    [
        ("DJI_20261024115345_0001_D.MP4", FileKind.ORIGINAL, None),
        ("2026-09-12 @ Arvada 1st Period.mp4", FileKind.PERIOD, "1"),
        ("2026-10-17 vs Lafayette - 2nd Period Full.mp4", FileKind.PERIOD, "2"),
        ("2026-10-02 - Silver Stick - Game 2 - Foothills - 3rd Period.mp4", FileKind.PERIOD, "3"),
        ("Game - OT.mp4", FileKind.PERIOD, "OT"),
        ("2026-09-13 vs Foothills - 1st Period - D Zone.mp4", FileKind.EXPORT, None),
        ("Foothills - 1st Period-D Zone.mp4", FileKind.EXPORT, None),
        ("Foothills - 2nd Period-D-Zone.mp4", FileKind.EXPORT, None),
        ("Silver Stick - Game 1 - Boulder.mp4", FileKind.OTHER, None),
    ],
)
def test_classify_name(name, kind, period):
    assert classify_name(name) == (kind, period)


def test_chapters_after_a_size_split_are_stitched_end_to_end():
    first = make_info("DJI_20261024115345_0001_D.MP4", duration=2522.95, size=FULL_CHAPTER)
    second = make_info("DJI_20261024123549_0002_D.MP4", start=2524.0, duration=1402.0)

    (camera,) = group_files([second, first]).cameras

    assert len(camera.recordings) == 1
    assert [c.file for c in camera.chapters] == [first, second]
    assert camera.chapters[1].start == GAME_START + timedelta(seconds=2522.95)
    assert camera.duration == pytest.approx(3924.95)


def test_gap_after_a_size_split_starts_a_new_recording():
    first = make_info("a.MP4", duration=1000, size=FULL_CHAPTER)
    later = make_info("b.MP4", start=1000 + 30)

    (camera,) = group_files([first, later]).cameras

    assert len(camera.recordings) == 2


def test_close_start_without_a_size_split_is_not_a_chapter():
    # A hand-trimmed period file that ends a few seconds before the next one starts.
    first = make_info("Game - 1st Period.mp4", duration=2050, size=13_900_000_000)
    third = make_info("Game - 3rd Period.mp4", start=2054)

    (camera,) = group_files([first, third]).cameras

    assert len(camera.recordings) == 2
    assert camera.recordings[1].start == GAME_START + timedelta(seconds=2054)
    assert [c.period for c in camera.chapters] == ["1", "3"]


def test_cameras_are_split_by_serial_and_labelled_by_start_time():
    late = make_info("x.MP4", start=60, serial="LATE")
    early = make_info("y.MP4", start=0, serial="EARLY")

    grouping = group_files([late, early])

    assert [(c.label, c.serial) for c in grouping.cameras] == [("A", "EARLY"), ("B", "LATE")]
    assert grouping.date == GAME_START
    assert grouping.footage == (GAME_START, GAME_START + timedelta(seconds=660))


def test_a_grouping_with_no_cameras_has_no_footage():
    with pytest.raises(GroupingError, match="no cameras"):
        group_files([]).footage  # noqa: B018


def test_files_without_serial_or_start_time_are_unassigned():
    no_serial = make_info("Game - 1st Period.mp4", serial=None)
    no_start = make_info("Game - 2nd Period.mp4", start=None)

    grouping = group_files([no_serial, no_start])

    assert grouping.cameras == []
    assert [c.file for c in grouping.unassigned] == [no_serial, no_start]
    assert [c.period for c in grouping.unassigned] == ["1", "2"]


def test_d_zone_exports_are_skipped():
    export = make_info("Game - 1st Period - D Zone.mp4", serial=None)
    assert group_files([export]).exports == [export]


def test_overlapping_files_on_one_camera_are_flagged():
    first = make_info("a.mp4", duration=600)
    overlap = make_info("b.mp4", start=300)

    grouping = group_files([first, overlap])

    assert len(grouping.cameras[0].recordings) == 2
    assert grouping.warnings == [
        "b.mp4 starts 300s before the previous file on the same camera ends"
    ]


def at(seconds: float) -> datetime:
    return GAME_START + timedelta(seconds=seconds)


def test_assigned_files_join_a_camera_with_their_hand_set_period_and_start():
    third = make_info("Game - 3rd Period.mp4", start=3000)
    first = make_info("Game - 1st Period.mp4", start=None, serial=None)
    grouping = group_files([third, first])

    placed = assign_files(grouping, {first.path: Assignment("a", "2", at(0))})

    (camera,) = placed.cameras
    assert (camera.label, camera.serial) == ("A", "CAMA")
    assert [(c.file, c.start, c.period) for c in camera.chapters] == [
        (first, at(0), "2"),
        (third, at(3000), "3"),
    ]
    assert len(camera.recordings) == 2
    assert placed.unassigned == []
    assert grouping.unassigned  # the original grouping is left alone


def test_a_new_letter_makes_a_camera_with_no_serial():
    a = make_info("a.MP4", duration=1000, size=FULL_CHAPTER)
    loose = make_info("b - 2nd Period.mp4", start=900, serial=None)
    kept_out = make_info("c - 3rd Period.mp4", start=2000, serial=None)

    placed = assign_files(group_files([a, loose, kept_out]), {loose.path: Assignment("B", "2")})

    assert [(c.label, c.serial) for c in placed.cameras] == [("A", "CAMA"), ("B", None)]
    assert placed.cameras[1].start == at(900)  # from the file's own creation time
    assert [c.file for c in placed.unassigned] == [kept_out]


def test_an_assigned_file_can_be_the_next_chapter():
    # A camera split, with the second chapter re-saved without its serial.
    first = make_info("a.MP4", duration=1000, size=FULL_CHAPTER)
    second = make_info("b.MP4", start=1002, serial=None)

    (camera,) = assign_files(
        group_files([first, second]), {second.path: Assignment("A", None)}
    ).cameras

    assert len(camera.recordings) == 1
    assert camera.chapters[1].start == at(1000)


@pytest.mark.parametrize(
    ("assignment", "message"),
    [
        (Assignment("A", "1"), "has no start time"),
        (Assignment("AB", "1", GAME_START), "isn't a letter"),
        (Assignment("A", "1", datetime(2026, 10, 24, 11)), "time zone"),
    ],
)
def test_assign_files_rejects(assignment, message):
    loose = make_info("x - 1st Period.mp4", start=None, serial=None)
    with pytest.raises(GroupingError, match=message):
        assign_files(group_files([make_info("a.MP4"), loose]), {loose.path: assignment})


def test_only_files_without_a_serial_can_be_assigned():
    a = make_info("a.MP4")
    with pytest.raises(GroupingError, match="a.MP4"):
        assign_files(group_files([a]), {Path("/footage/a.MP4"): Assignment("B", None)})


@pytest.mark.parametrize(
    ("timecode", "seconds"),
    [
        ("09:05:51;006", 9 * 3600 + 5 * 60 + 51 + 6 / 120),
        ("17:56:59;30", 17 * 3600 + 56 * 60 + 59 + 30 / 120),
        ("00:00:01:00", 1.0),
        (None, None),
        ("garbled", None),
    ],
)
def test_timecode_seconds(timecode, seconds):
    seconds_read = timecode_seconds(make_info("a.MP4", timecode=timecode))
    assert seconds_read == (pytest.approx(seconds) if seconds is not None else None)


def test_start_is_estimated_from_timecode_on_the_same_camera():
    # Measured on 2026-09-26: P3 at 15:43:12 local with timecode 09:55:04;18.
    third = make_info("P3.mp4", start=3000, timecode="09:55:04;18")
    first = make_info("P1.mp4", start=None, serial=None, timecode="09:05:51;006")
    camera = group_files([third, first]).cameras[0]

    assert estimate_start(first, camera) == at(3000 - 49 * 60 - 13)


def test_estimate_wraps_at_midnight():
    late = make_info("a.mp4", start=0, timecode="23:59:50;00")
    after = make_info("b.mp4", start=None, serial=None, timecode="00:00:20;00")
    camera = group_files([late, after]).cameras[0]

    assert estimate_start(after, camera) == at(30)


def test_no_estimate_without_timecodes():
    camera = group_files([make_info("a.mp4")]).cameras[0]
    assert estimate_start(make_info("b.mp4", timecode="01:00:00;00"), camera) is None
    assert estimate_start(make_info("b.mp4"), camera) is None
