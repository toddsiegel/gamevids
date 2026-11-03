from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import pytest

from gamevids.probe import ProbeError, find_videos, parse_ffprobe, probe_file


def ffprobe_output(**format_tags) -> dict:
    """Trimmed ffprobe JSON in the shape of a DJI original."""
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "r_frame_rate": "120000/1001",
                "width": 1920,
                "height": 1080,
                "duration": "2496.56",
                "disposition": {"attached_pic": 0},
            },
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            {"index": 2, "codec_type": "data", "codec_tag_string": "djmd"},
            {"index": 3, "codec_type": "data", "codec_tag_string": "tmcd"},
            {
                "index": 4,
                "codec_type": "video",
                "codec_name": "mjpeg",
                "r_frame_rate": "90000/1",
                "width": 1280,
                "height": 720,
                "disposition": {"attached_pic": 1},
            },
        ],
        "format": {"duration": "2496.560733", "size": "17191877994", "tags": format_tags},
    }


def test_parse_dji_original():
    data = ffprobe_output(
        creation_time="2026-10-24T17:54:48.000000Z", encoder="DJI OsmoAction5 Pro"
    )
    parsed = parse_ffprobe(data, Path("a.MP4"))

    info = parsed.info
    assert parsed.djmd_stream == 2
    assert info.fps == Fraction(120000, 1001)
    assert (info.width, info.height) == (1920, 1080)
    assert (info.video_codec, info.audio_codec) == ("hevc", "aac")
    assert info.duration == pytest.approx(2496.560733)
    assert info.size == 17191877994
    assert info.creation_time == datetime(2026, 10, 24, 17, 54, 48, tzinfo=UTC)


def test_parse_remuxed_file_without_metadata():
    data = ffprobe_output(encoder="Lavf62.3.100")
    data["streams"] = [s for s in data["streams"] if s["codec_type"] != "data"]

    parsed = parse_ffprobe(data, Path("a.mp4"))

    assert parsed.djmd_stream is None
    assert parsed.info.creation_time is None


def test_parse_rejects_file_without_video():
    with pytest.raises(ProbeError, match="no video stream"):
        parse_ffprobe({"streams": [], "format": {}}, Path("a.mp4"))


def test_probe_file_reads_a_real_file(make_clip):
    path = make_clip("clip.mp4", seconds=1, creation_time="2026-10-24T17:54:48Z")

    info = probe_file(path)

    assert info.duration == pytest.approx(1.0, abs=0.1)
    assert (info.width, info.height, info.fps) == (320, 180, Fraction(30))
    assert info.creation_time == datetime(2026, 10, 24, 17, 54, 48, tzinfo=UTC)
    assert info.serial is None


def test_probe_file_reports_unreadable_files(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ProbeError, match="bad.mp4"):
        probe_file(bad)


def test_find_videos_expands_folders(tmp_path):
    for name in ["b.MP4", "a.mp4", "notes.txt", "proj.llc", "stream-2-data-djmd.bin"]:
        (tmp_path / name).touch()
    (tmp_path / "sub").mkdir()

    assert find_videos([tmp_path]) == [tmp_path / "a.mp4", tmp_path / "b.MP4"]
