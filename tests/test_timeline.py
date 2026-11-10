from datetime import timedelta
from pathlib import Path

import pytest
from conftest import FULL_CHAPTER, GAME_START, make_info

from gamevids.grouping import group_files
from gamevids.timeline import camera_timeline


def at(seconds: float):
    return GAME_START + timedelta(seconds=seconds)


@pytest.fixture
def timeline():
    """Two chapters, then a 5-minute stop, then a second recording."""
    files = [
        make_info("DJI_20261024115345_0001_D.MP4", duration=2500, size=FULL_CHAPTER),
        make_info("DJI_20261024123549_0002_D.MP4", start=2501, duration=1000),
        make_info("DJI_20261024130000_0003_D.MP4", start=3800, duration=600),
    ]
    (camera,) = group_files(files).cameras
    return camera_timeline(camera)


def test_files_play_back_to_back_with_gaps_cut_out(timeline):
    assert [s.position for s in timeline.segments] == [0, 2500, 3500]
    assert [s.recording for s in timeline.segments] == [0, 0, 1]
    assert timeline.duration == 4100
    assert timeline.chapter_joins == [2500]
    assert timeline.recording_joins == [3500]


def test_wall_clock_follows_each_file(timeline):
    assert timeline.wall_clock(0) == GAME_START
    assert timeline.wall_clock(2600) == at(2600)  # the chapter is placed at the split
    assert timeline.wall_clock(3500) == at(3800)  # the gap is skipped
    assert timeline.wall_clock(3600) == at(3900)
    assert timeline.wall_clock(-5) == GAME_START
    assert timeline.wall_clock(9999) == at(4400)


def test_position_is_the_inverse_of_wall_clock(timeline):
    for position in (0, 1234.5, 2500, 3499, 3500, 4100):
        assert timeline.position(timeline.wall_clock(position)) == pytest.approx(position)


def test_position_in_a_gap_jumps_to_the_next_recording(timeline):
    assert timeline.position(at(3600)) == 3500
    assert timeline.position(at(-60)) == 0
    assert timeline.position(at(9999)) == 4100


def test_edl_length_prefixes_every_path():
    files = [
        make_info("a, b; c.MP4", duration=100, size=FULL_CHAPTER),
        make_info("Ünïcode.MP4", start=100, duration=50),
    ]
    (camera,) = group_files(files).cameras

    edl = camera_timeline(camera).edl()

    assert edl == "edl://%20%/footage/a, b; c.MP4;%22%/footage/Ünïcode.MP4"


def test_missing_files(timeline):
    assert timeline.missing_files() == [s.path for s in timeline.segments]
    assert timeline.missing_files()[0] == Path("/footage/DJI_20261024115345_0001_D.MP4")
