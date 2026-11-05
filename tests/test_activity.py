import math
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest
from conftest import FULL_CHAPTER, GAME_START, make_info

from gamevids import db, frames
from gamevids.activity import (
    ActivityCurve,
    ScanSettings,
    frame_activity,
    load_curve,
    moving_average,
    save_curve,
    scan_camera,
)
from gamevids.frames import VideoError, gray_frames
from gamevids.grouping import group_files

WHOLE_FRAME = ((0, 0), (1, 0), (1, 1), (0, 1))
TINY = ScanSettings(rate=2, width=4, height=2)


def test_activity_is_the_fraction_of_pixels_that_moved():
    before = np.full(8, 100, dtype=np.uint8)
    after = before.copy()
    after[:2] = 200

    assert frame_activity(before, before, 12) == 0
    assert frame_activity(before, after, 12) == 0.25


def test_small_changes_and_uniform_brightness_shifts_are_ignored():
    before = np.full(8, 100, dtype=np.uint8)
    noisy = before + np.array([5, -5, 12, -12, 0, 0, 0, 0]).astype(np.uint8)
    brighter = before + 40

    assert frame_activity(before, noisy, 12) == 0
    assert frame_activity(before, brighter, 12) == 0


def toggling_frames(path, rate, width, height, *, hwaccel):
    """Fake decoder: every other frame lights 2 of the 8 pixels, so activity is 0.25."""
    seconds = {"a1.MP4": 10, "a2.MP4": 10, "a3.MP4": 10}[path.name]
    for k in range(round(seconds * rate)):
        frame = np.zeros((height, width), dtype=np.uint8)
        if k % 2:
            frame[0, :2] = 200
        yield frame


def test_scan_places_samples_on_the_camera_wall_clock(monkeypatch):
    monkeypatch.setattr("gamevids.activity.gray_frames", toggling_frames)
    camera = group_files(
        [
            make_info("a1.MP4", duration=10, size=FULL_CHAPTER),
            make_info("a2.MP4", start=10.5, duration=10),  # next chapter
            make_info("a3.MP4", start=25, duration=10),  # new recording after a 5 s gap
        ]
    ).cameras[0]
    calls = []

    curve = scan_camera(camera, WHOLE_FRAME, TINY, progress=lambda d, t: calls.append((d, t)))

    v = curve.values
    assert curve.start == GAME_START
    assert len(v) == 70
    assert v[0] == 0  # nothing to compare the first sample with
    assert (v[1:40] == 0.25).all()  # including across the chapter split at sample 20
    assert np.isnan(v[40:50]).all()
    assert v[50] == 0
    assert (v[51:] == 0.25).all()
    assert calls[-1] == (30, 30)
    assert all(a <= b for (a, _), (b, _) in zip(calls, calls[1:], strict=False))


def test_curves_are_saved_and_loaded(library):
    conn = db.connect(library)
    with conn:
        conn.execute("INSERT INTO game (id, title, date, created_at) VALUES (1, 't', 'd', 'c')")
        conn.execute("INSERT INTO camera (id, game_id, label) VALUES (1, 1, 'A')")
    values = np.array([0, 0.5, np.nan, 0.25], dtype=np.float32)

    curve = ActivityCurve(GAME_START, 2.0, values, WHOLE_FRAME, TINY, datetime.now(UTC))
    save_curve(conn, 1, curve)
    loaded = load_curve(conn, 1)

    assert loaded is not None
    assert np.array_equal(loaded.values, values, equal_nan=True)
    assert (loaded.start, loaded.rate, loaded.settings) == (GAME_START, 2.0, TINY)
    assert loaded.polygon == WHOLE_FRAME
    assert loaded.end == loaded.time(4)
    assert load_curve(conn, 2) is None


@pytest.fixture
def moving_box(tmp_path):
    """2 s of video: a white box moving back and forth across the left half."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "box.mp4"
    graph = (
        "color=c=gray:s=160x90:r=30:d=2[bg];color=c=white:s=12x12:r=30[box];"
        "[bg][box]overlay=x='mod(t*60,70)':y=10:shortest=1"
    )
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", graph]
    subprocess.run([*cmd, "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def test_scan_sees_motion_only_where_it_happens(moving_box):
    info = replace(make_info("box.mp4", duration=2), path=moving_box)
    camera = group_files([info]).cameras[0]
    left = ((0, 0), (0.5, 0), (0.5, 1), (0, 1))
    right = ((0.5, 0), (1, 0), (1, 1), (0.5, 1))
    settings = ScanSettings(rate=5, width=80, height=45)

    moving = scan_camera(camera, left, settings, hwaccel=False).values
    still = scan_camera(camera, right, settings, hwaccel=False).values

    assert len(moving) == 10
    assert np.nanmean(moving) > 0.01
    assert np.nanmax(still) == 0


def test_hardware_decode_falls_back_to_software(monkeypatch, tmp_path):
    tries = []

    def fake(path, rate, width, height, *, hwaccel):
        tries.append(hwaccel)
        if hwaccel:
            raise VideoError("no VA-API here")
        yield np.zeros((height, width), dtype=np.uint8)

    monkeypatch.setattr(frames, "_gray_frames", fake)

    assert len(list(gray_frames(tmp_path / "x.mp4", 1, 4, 2))) == 1
    assert tries == [True, False]


def test_a_failure_after_frames_arrived_is_not_retried(monkeypatch, tmp_path):
    def fake(path, rate, width, height, *, hwaccel):
        yield np.zeros((height, width), dtype=np.uint8)
        raise VideoError("broken file")

    monkeypatch.setattr(frames, "_gray_frames", fake)

    with pytest.raises(VideoError, match="broken file"):
        list(gray_frames(tmp_path / "x.mp4", 1, 4, 2))


def test_moving_average_skips_gaps():
    values = np.array([1, 3, np.nan, 5, 7], dtype=float)

    smoothed = moving_average(values, 3)

    assert smoothed[0] == 2  # (1 + 3) / 2 at the edge
    assert smoothed[1] == 2
    assert math.isnan(smoothed[2])
    assert smoothed[3] == 6


def test_moving_average_keeps_the_length_of_short_curves():
    assert len(moving_average(np.ones(3), 50)) == 3
