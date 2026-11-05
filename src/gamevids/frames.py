"""Decode frames from a video file with ffmpeg."""

import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np


class VideoError(RuntimeError):
    pass


def _ffmpeg(args: list[str], path: Path, *, hwaccel: bool) -> list[str]:
    # scale_vaapi crashes ffmpeg 8.0 on this footage, so only decoding runs on the
    # GPU; frames are downloaded and scaled on the CPU after `fps` has thinned them.
    accel = ["-hwaccel", "vaapi"] if hwaccel else []
    return ["ffmpeg", "-nostdin", "-v", "error", *accel, "-i", str(path), *args]


def _gray_frames(
    path: Path, rate: float, width: int, height: int, *, hwaccel: bool
) -> Iterator[np.ndarray]:
    filters = f"fps={rate},scale={width}:{height}:flags=area"
    cmd = _ffmpeg(
        ["-map", "0:v:0", "-vf", filters, "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        path,
        hwaccel=hwaccel,
    )
    size = width * height
    # stderr goes to a file so a chatty ffmpeg can't fill a pipe and stall.
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        except FileNotFoundError as e:
            raise VideoError("ffmpeg is not installed") from e
        try:
            assert proc.stdout is not None
            while len(buf := proc.stdout.read(size)) == size:
                yield np.frombuffer(buf, np.uint8).reshape(height, width)
            if proc.wait() != 0:
                errors.seek(0)
                message = errors.read().decode(errors="replace").strip()
                raise VideoError(f"{path}: ffmpeg failed: {message or proc.returncode}")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def gray_frames(
    path: Path, rate: float, width: int, height: int, *, hwaccel: bool = True
) -> Iterator[np.ndarray]:
    """Yield the video as `rate` grayscale frames per second, scaled to `width` x `height`.

    Decoding uses VA-API when `hwaccel` is set, and falls back to software
    decoding if that fails before the first frame.
    """
    if not hwaccel:
        yield from _gray_frames(path, rate, width, height, hwaccel=False)
        return
    started = False
    try:
        for frame in _gray_frames(path, rate, width, height, hwaccel=True):
            started = True
            yield frame
    except VideoError:
        if started:
            raise
        yield from _gray_frames(path, rate, width, height, hwaccel=False)


def still_frame(path: Path, at: float, width: int, height: int) -> np.ndarray:
    """Return the frame near `at` seconds into the file as RGB, `height` x `width` x 3."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path)]
    cmd += ["-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={width}:{height}"]
    cmd += ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise VideoError("ffmpeg is not installed") from e
    except subprocess.CalledProcessError as e:
        raise VideoError(f"{path}: {e.stderr.decode(errors='replace').strip()}") from e
    if len(result.stdout) != width * height * 3:
        raise VideoError(f"{path}: no frame at {at:.1f}s")
    return np.frombuffer(result.stdout, np.uint8).reshape(height, width, 3)
