from datetime import timedelta

import pytest
from conftest import FULL_CHAPTER, GAME_START, make_info

from gamevids.grouping import FileKind, classify_name, group_files


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
