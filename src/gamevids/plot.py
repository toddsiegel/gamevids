"""A PNG of each camera's zone, activity curve and clips, for checking a scan by eye."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from matplotlib import dates as mdates
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as PolygonPatch

from gamevids.activity import ActivityCurve, moving_average
from gamevids.detect import Clip, Detection
from gamevids.grouping import CameraGroup
from gamevids.zones import Polygon

SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
CLIP = "#f0a830"
SMOOTHING = 5.0  # seconds averaged for the dark line when there are no clips


@dataclass
class CameraPanel:
    camera: CameraGroup
    zone: Polygon | None
    curve: ActivityCurve | None
    still: np.ndarray | None  # RGB frame from the camera
    detection: Detection | None = None
    clips: list[Clip] = field(default_factory=list)

    @property
    def fresh_clips(self) -> bool:
        """Whether the clips were detected from the curve being drawn."""
        return (
            self.curve is not None
            and self.detection is not None
            and self.detection.curve_scanned_at == self.curve.scanned_at
        )


def _num(moment: datetime) -> float:
    return mdates.date2num(moment)


def _draw_still(ax, panel: CameraPanel) -> None:
    camera = panel.camera
    ax.set_title(f"Camera {camera.label}  {camera.serial}", loc="left", color=INK, fontsize=11)
    if panel.still is not None:
        ax.imshow(panel.still, extent=(0, 1, 1, 0), aspect="auto")
    else:
        ax.set_facecolor(GRID)
    if panel.zone:
        ax.add_patch(
            PolygonPatch(panel.zone, closed=True, facecolor=SERIES + "33", edgecolor=SERIES, lw=2)
        )
    else:
        ax.text(0.5, 0.5, "no zone", ha="center", va="center", color=INK_2, fontsize=12)
    ticks = np.linspace(0, 1, 11)
    ax.set_xticks(ticks, [f"{t:.1f}" for t in ticks], fontsize=7, color=MUTED)
    ax.set_yticks(ticks, [f"{t:.1f}" for t in ticks], fontsize=7, color=MUTED)
    ax.grid(color="black", alpha=0.3, lw=0.5)
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)


def _draw_curve(ax, panel: CameraPanel, tz) -> None:
    camera, curve = panel.camera, panel.curve

    for before, after in zip(camera.recordings, camera.recordings[1:], strict=False):
        ax.axvspan(_num(before.end), _num(after.start), color=GRID, lw=0)
    for recording in camera.recordings:
        for chapter in recording.chapters[1:]:
            ax.axvline(_num(chapter.start), color=MUTED, lw=1, ls=":")

    if curve is None:
        ax.text(0.5, 0.5, "not scanned", transform=ax.transAxes, ha="center", va="center")
        ax.set_ylim(0, 10)
    else:
        x = _num(curve.start) + np.arange(len(curve.values)) / curve.rate / 86400
        percent = curve.values * 100
        smoothing = SMOOTHING
        if panel.fresh_clips:
            # Draw the curve the detector thresholded, under its clips.
            assert panel.detection is not None
            smoothing = panel.detection.settings.smoothing
            for clip in panel.clips:
                ax.axvspan(_num(clip.start), _num(clip.end), color=CLIP, alpha=0.3, lw=0)
            threshold = panel.detection.settings.threshold * 100
            ax.axhline(threshold, color=INK_2, lw=0.75, ls="--")
        ax.plot(x, percent, color=SERIES, alpha=0.25, lw=0.5)
        window = max(1, round(smoothing * curve.rate))
        ax.plot(x, moving_average(percent, window), color=SERIES, lw=1.5)
        top = np.nanpercentile(percent, 99.9) if np.isfinite(percent).any() else 1.0
        ax.set_ylim(0, max(1.0, top * 1.1))
        note = f"scanned {curve.scanned_at.astimezone():%Y-%m-%d %H:%M}"
        if panel.zone is not None and tuple(panel.zone) != tuple(curve.polygon):
            note += "  (zone has changed since; scan again)"
        if panel.fresh_clips:
            note = f"{len(panel.clips)} clips  ·  " + note
        elif panel.detection is not None:
            note = "clips are from an earlier scan; detect again  ·  " + note
        ax.set_title(note, loc="right", color=INK_2, fontsize=9)

    ax.set_ylabel("% of zone moving", color=INK_2)
    ax.grid(axis="y", color=GRID, lw=0.75)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=range(0, 60, 5), tz=tz))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)


def plot_activity(title: str, panels: list[CameraPanel], out: Path) -> None:
    """Write one row per camera: its still with the zone, then its activity over time.

    Grey bands mark gaps between recordings, dotted lines chapter splits, and
    orange bands detected clips, with the detection threshold dashed. The
    still's axes run 0-1 so zone corners can be read straight off it.
    """
    start = min(p.camera.start for p in panels)
    # The local offset when the game was played, which may differ from today's (DST).
    tz = start.astimezone().tzinfo
    rows = len(panels)
    fig = Figure(figsize=(20, 3.4 * rows + 0.6), facecolor=SURFACE, layout="constrained")
    fig.suptitle(title, x=0.01, ha="left", color=INK, fontsize=14)
    grid = fig.add_gridspec(rows, 2, width_ratios=[1, 3.4])

    end = max(p.camera.end for p in panels) + timedelta(minutes=1)
    start -= timedelta(minutes=1)
    shared = None
    for row, panel in enumerate(panels):
        _draw_still(fig.add_subplot(grid[row, 0]), panel)
        ax = fig.add_subplot(grid[row, 1], sharex=shared)
        ax.set_facecolor(SURFACE)
        shared = shared or ax
        _draw_curve(ax, panel, tz)
        ax.set_xlim(_num(start), _num(end))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=100)
