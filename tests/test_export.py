import json
import shutil
import subprocess
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta

import pytest
from conftest import GAME, GAME_START, make_info, make_play_curve

from gamevids import db
from gamevids.detect import DEFAULT_SETTINGS as DETECT
from gamevids.detect import Segment, load_clips, save_clips
from gamevids.export import (
    ExportError,
    ExportResult,
    ExportSettings,
    Piece,
    Shot,
    default_path,
    export_video,
    file_name,
    kept_clips,
    load_exports,
    plan_shots,
    save_export,
    title_lines,
    video_length,
)
from gamevids.games import add_game, load_game
from gamevids.grouping import group_files
from gamevids.triage import save_triage

SMALL = ExportSettings(width=320, height=180, preset="ultrafast", title_seconds=1.0)


def at(seconds):
    return GAME_START + timedelta(seconds=seconds)


def test_file_name_is_the_title_with_a_suffix_and_no_unsafe_characters():
    assert file_name("2026-10-24 vs Lafayette") == "2026-10-24 vs Lafayette - DZone.mp4"
    assert file_name('A/B: "final"?') == "A-B- -final-- - DZone.mp4"
    assert file_name(" .. ") == "game - DZone.mp4"


def test_default_path_is_in_the_game_vids_folder(tmp_path, monkeypatch, conn):
    monkeypatch.setenv("HOME", str(tmp_path))
    game = two_camera_game(conn, tmp_path)
    assert default_path(game) == tmp_path / "Videos" / "Game Vids" / "vs Lafayette - DZone.mp4"


def test_title_shrinks_to_one_line_then_wraps():
    assert title_lines("vs Lafayette") == (["vs Lafayette"], 72)
    lines, size = title_lines("2026-10-24 - Aspen Fall Faceoff - Hyland Hills")
    assert len(lines) == 1 and 48 < size < 72
    title = "Rocky Mountain Invitational  Championship Game vs   Lafayette, Saturday Morning"
    lines, size = title_lines(title)
    assert lines == [  # at most 58 characters a line at 48 px
        "Rocky Mountain Invitational Championship Game vs",
        "Lafayette, Saturday Morning",
    ]
    assert size == 48


# -- planning ------------------------------------------------------------------


@pytest.fixture
def conn(library):
    with closing(db.connect(library)) as conn:
        yield conn


def two_camera_game(conn, tmp_path):
    """Camera A in two 600 s chapters (one recording), camera B in one 1,200 s file."""
    infos = [
        make_info("a1.MP4", duration=600, size=17_300_000_000),
        make_info("a2.MP4", start=600, duration=600),
        make_info("b.MP4", serial="CAMB", duration=1200),
    ]
    infos[2] = replace(infos[2], audio_codec=None)
    return load_game(conn, add_game(conn, "vs Lafayette", group_files(infos), date(2026, 10, 24)))


def keep(conn, game, label, spans, status="kept"):
    camera_id = game.camera_ids[label]
    segments = [Segment(at(s), at(e), 0.1) for s, e in spans]
    save_clips(conn, camera_id, segments, DETECT, make_play_curve(0, GAME))
    for clip in load_clips(conn, camera_id):
        save_triage(conn, replace(clip, status=status))


def test_shots_interleave_cameras_and_merge_overlapping_clips(conn, tmp_path):
    game = two_camera_game(conn, tmp_path)
    keep(conn, game, "A", [(100, 110), (590, 605), (603, 620)])
    keep(conn, game, "B", [(50, 60), (300, 310)])
    with conn:  # one of B's clips was discarded
        conn.execute("UPDATE clip SET status = 'discarded' WHERE start = ?", (at(300).isoformat(),))

    shots = plan_shots(game, kept_clips(conn, game))

    assert [(s.camera, s.start, s.end) for s in shots] == [
        ("B", at(50), at(60)),
        ("A", at(100), at(110)),
        ("A", at(590), at(620)),  # two overlapping clips, one shot
    ]
    assert len(shots[2].clip_ids) == 2
    # The merged shot crosses A's chapter split, so it plays from both files.
    a1, a2 = shots[2].pieces
    assert (a1.path.name, a1.offset, a1.duration) == ("a1.MP4", 590, 10)
    assert (a2.path.name, a2.offset, a2.duration) == ("a2.MP4", 0, 20)
    assert shots[0].pieces[0].audio is False and a1.audio is True
    assert video_length(shots) == 3 + 10 + 10 + 30


def test_a_trimmed_clip_plays_from_its_in_to_its_out_point(conn, tmp_path):
    game = two_camera_game(conn, tmp_path)
    keep(conn, game, "A", [(100, 110)])
    clip = load_clips(conn, game.camera_ids["A"])[0]
    save_triage(conn, replace(clip, trim_in=at(97), trim_out=at(108.5)))

    (shot,) = plan_shots(game, kept_clips(conn, game))

    assert (shot.start, shot.end, shot.duration) == (at(97), at(108.5), 11.5)


def test_nothing_kept_plans_no_shots(conn, tmp_path):
    game = two_camera_game(conn, tmp_path)
    keep(conn, game, "A", [(100, 110)], status="candidate")
    assert plan_shots(game, kept_clips(conn, game)) == []


def test_exports_are_saved_newest_first(conn, tmp_path):
    game = two_camera_game(conn, tmp_path)
    for name in ("first.mp4", "second.mp4"):
        save_export(conn, game.id, ExportResult(tmp_path / name, 42.5, 7, 6, 1000), SMALL)

    exports = load_exports(conn, game.id)

    assert [e.path.name for e in exports] == ["second.mp4", "first.mp4"]
    assert (exports[0].clips, exports[0].duration) == (7, 42.5)
    assert json.loads(exports[0].settings)["fps"] == "60000/1001"


# -- encoding ------------------------------------------------------------------


def probe(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,r_frame_rate,width",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    ).stdout
    return json.loads(out)


@pytest.fixture
def footage(make_clip, tmp_path):
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=120",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(silent),
        ],
        check=True,
    )
    return make_clip("one.mp4", seconds=2), make_clip("two.mp4", seconds=2), silent


def test_export_makes_one_video_from_the_title_and_every_shot(footage, tmp_path):
    one, two, silent = footage
    shots = [
        # a shot across a chapter split: the end of one file and the start of the next
        Shot("A", at(0), at(2), (1,), (Piece(one, 1.0, 1.0, True), Piece(two, 0.0, 1.0, True))),
        Shot("B", at(5), at(6.5), (2, 3), (Piece(silent, 0.25, 1.5, False),)),
    ]
    output = tmp_path / "out" / "game.mp4"
    output.parent.mkdir()
    output.write_bytes(b"an earlier export")
    seen = []

    result = export_video(
        "vs Lafayette", shots, output, SMALL, lambda d, t: seen.append((d, t)), hwaccel=False
    )

    assert result.path == output and result.clips == 3 and result.shots == 2
    assert result.duration == 1.0 + 2.0 + 1.5
    info = probe(output)
    streams = {s["codec_name"]: s for s in info["streams"]}
    assert set(streams) == {"h264", "aac"}
    assert streams["h264"]["r_frame_rate"] == "60000/1001" and streams["h264"]["width"] == 320
    assert float(info["format"]["duration"]) == pytest.approx(4.5, abs=0.1)
    assert seen[0] == (0.0, 4.5) and seen[-1] == (4.5, 4.5)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert sorted(p.name for p in output.parent.iterdir()) == ["game.mp4"]  # no leftovers


def test_a_stopped_export_leaves_no_trace(footage, tmp_path):
    one, _, _ = footage
    shots = [Shot("A", at(0), at(1), (1,), (Piece(one, 0.0, 1.0, True),))]
    output = tmp_path / "game.mp4"

    def stop(done, total):
        if done > 0:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        export_video("vs Lafayette", shots, output, SMALL, stop, hwaccel=False)

    assert list(tmp_path.glob("game*")) == [] and list(tmp_path.glob(".gamevids-*")) == []


def test_export_refuses_missing_footage_and_no_shots(tmp_path):
    with pytest.raises(ExportError, match="no kept clips"):
        export_video("vs Lafayette", [], tmp_path / "game.mp4", SMALL)
    gone = Shot("A", at(0), at(1), (1,), (Piece(tmp_path / "gone.mp4", 0.0, 1.0, True),))
    with pytest.raises(ExportError, match="footage not found: .*gone.mp4"):
        export_video("vs Lafayette", [gone], tmp_path / "game.mp4", SMALL)
    assert not (tmp_path / "game.mp4").exists()
