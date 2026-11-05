"""The `gamevids` command line."""

import argparse
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

from gamevids import __version__, db
from gamevids.activity import ScanError, load_curve, save_curve, scan_camera
from gamevids.frames import VideoError, still_frame
from gamevids.games import (
    GAME_SORTS,
    Game,
    GameError,
    add_game,
    list_games,
    load_game,
    remove_game,
    rename_game,
)
from gamevids.grouping import Grouping, group_files
from gamevids.plot import CameraPanel, plot_activity
from gamevids.probe import ProbeError, find_videos, probe_file
from gamevids.report import format_game, format_game_list, format_grouping, hms
from gamevids.zones import ZoneError, load_zone, parse_polygon, save_zone

ZONE_HELP = (
    'corners as "x,y x,y x,y ...", fractions of the frame from the top-left, '
    'e.g. "0,.55 .26,.46 .74,.47 1,.58 1,1 0,1"'
)


class CommandError(RuntimeError):
    pass


def _group(paths: list[Path]) -> Grouping:
    videos = find_videos(paths)
    if not videos:
        raise CommandError("No video files found.")
    return group_files([probe_file(p) for p in videos])


@contextmanager
def _open(args: argparse.Namespace) -> Iterator[sqlite3.Connection]:
    with closing(db.connect(args.db)) as conn:
        yield conn


def cmd_probe(args: argparse.Namespace) -> int:
    print(format_grouping(_group(args.paths)))
    return 0


def cmd_game_add(args: argparse.Namespace) -> int:
    grouping = _group(args.paths)
    if grouping.unassigned:
        names = "".join(f"\n  {c.file.path.name}" for c in grouping.unassigned)
        if not args.skip_unassigned:
            raise CommandError(
                f"These files have no camera serial:{names}\n"
                "Leave them out, or pass --skip-unassigned to add the game without them."
            )
        print(f"Skipping files with no camera serial:{names}", file=sys.stderr)
        grouping.unassigned = []
    for warning in grouping.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    with _open(args) as conn:
        game_id = add_game(conn, args.title, grouping, args.date)
        print(f"Added game {game_id}.")
        if grouping.exports:
            print(f"Skipped {len(grouping.exports)} D-zone export(s).")
        print()
        print(format_game(load_game(conn, game_id)))
    return 0


def cmd_game_list(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        print(format_game_list(list_games(conn, args.sort, args.desc)))
    return 0


def cmd_game_show(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        print(format_game(load_game(conn, args.game)))
    return 0


def cmd_game_remove(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        title = load_game(conn, args.game).title
        remove_game(conn, args.game)
    print(f"Removed game {args.game} ({title}). Its video files were not touched.")
    return 0


def cmd_game_rename(args: argparse.Namespace) -> int:
    if args.title is None and args.date is None:
        raise CommandError("Give a new title, a new --date, or both.")
    with _open(args) as conn:
        before = load_game(conn, args.game)
        rename_game(conn, args.game, args.title, args.date)
        after = load_game(conn, args.game)
    if after.title != before.title:
        print(f"Title: {before.title} -> {after.title}")
    if after.date != before.date:
        print(f"Date: {before.date.isoformat()} -> {after.date.isoformat()}")
    if (after.title, after.date) == (before.title, before.date):
        print(f"Game {args.game} already has that title and date.")
    return 0


class _ProgressLine:
    """Shows scan progress on stderr: one updating line on a terminal, else every 10%."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()
        self.tty = sys.stderr.isatty()
        self.shown_at = 0.0
        self.next_step = 0.1

    def __call__(self, done: float, total: float) -> None:
        now = time.monotonic()
        fraction = done / total if total else 1.0
        if self.tty:
            if now - self.shown_at < 0.5 and fraction < 1:
                return
            self.shown_at = now
        elif fraction < self.next_step:
            return
        else:
            self.next_step = int(fraction * 10 + 1) / 10
        elapsed = now - self.started
        speed = f", {done / elapsed:.1f}x real time" if elapsed > 1 else ""
        text = f"Camera {self.label}: {hms(done)} of {hms(total)} ({fraction:.0%}){speed}"
        print(
            f"\r{text}\033[K" if self.tty else text,
            end="" if self.tty else "\n",
            file=sys.stderr,
            flush=True,
        )

    def finish(self) -> None:
        if self.tty and self.shown_at:
            print(file=sys.stderr)


def _still(game: Game, label: str):
    first = game.camera(label).chapters[0]
    try:
        return still_frame(first.file.path, first.file.duration / 2, 640, 360)
    except VideoError as e:
        print(f"warning: no still for camera {label}: {e}", file=sys.stderr)
        return None


def _write_plot(conn: sqlite3.Connection, game: Game, path: Path) -> None:
    panels = []
    for camera in game.grouping.cameras:
        camera_id = game.camera_ids[camera.label]
        panels.append(
            CameraPanel(
                camera=camera,
                zone=load_zone(conn, camera_id),
                curve=load_curve(conn, camera_id),
                still=_still(game, camera.label),
            )
        )
    plot_activity(f"Game {game.id}: {game.title}  ({game.date.isoformat()})", panels, path)
    print(f"Wrote {path}")


def cmd_scan(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        game = load_game(conn, args.game)
        given = {}
        for label, corners in args.zone:
            camera = game.camera(label)
            try:
                given[camera.label] = parse_polygon(corners)
            except ZoneError as e:
                raise CommandError(f"camera {camera.label}'s zone: {e}") from None
        chosen = [game.camera(c).label for c in args.camera or []]
        labels = list(dict.fromkeys(chosen)) or [c.label for c in game.grouping.cameras]

        zones = {}
        if not args.plot_only:
            for label in labels:
                zones[label] = given.get(label) or load_zone(conn, game.camera_ids[label])
                if zones[label] is None:
                    raise CommandError(
                        f"Camera {label} has no D-zone yet. Give one with --zone {label} CORNERS,\n"
                        f"{ZONE_HELP}.\n"
                        "Add --plot-only to draw each camera's still on a grid to read "
                        "corners from, without scanning."
                    )
        for label, polygon in given.items():
            save_zone(conn, game.camera_ids[label], polygon)

        for label in [] if args.plot_only else labels:
            camera = game.camera(label)
            progress = _ProgressLine(label)
            started = time.monotonic()
            try:
                curve = scan_camera(camera, zones[label], progress=progress)
            except KeyboardInterrupt:
                progress.finish()
                print(f"Stopped. Camera {label}'s scan wasn't saved.", file=sys.stderr)
                return 130
            progress.finish()
            save_curve(conn, game.camera_ids[label], curve)
            took = time.monotonic() - started
            print(
                f"Camera {label}: scanned {hms(camera.duration)} of footage in {hms(took)}"
                f" ({camera.duration / max(took, 0.001):.1f}x real time)."
            )

        _write_plot(conn, game, args.plot or Path(f"game-{game.id}-activity.png"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamevids",
        description="Turn full-game goalie footage into short game videos.",
    )
    parser.add_argument("--version", action="version", version=f"gamevids {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=db.default_path(),
        help=f"library database (default: ${db.DB_ENV}, or %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    probe = commands.add_parser(
        "probe",
        help="show how a game's files group into cameras",
        description="Group a game's video files by camera and place them on each camera's "
        "timeline. Nothing is saved.",
    )
    probe.add_argument("paths", nargs="+", type=Path, help="video files or a game folder")
    probe.set_defaults(handler=cmd_probe)

    game = commands.add_parser("game", help="add, list, show, rename and remove games")
    game_commands = game.add_subparsers(dest="game_command", metavar="<action>", required=True)

    add = game_commands.add_parser(
        "add",
        help="add a game from its video files",
        description="Group the files by camera, as `probe` does, and save them as a game.",
    )
    add.add_argument("paths", nargs="+", type=Path, help="video files or a game folder")
    add.add_argument("--title", required=True, help='shown on the title card, e.g. "vs Lafayette"')
    add.add_argument(
        "--date",
        type=date.fromisoformat,
        help="game date as YYYY-MM-DD (default: from the files)",
    )
    add.add_argument(
        "--skip-unassigned",
        action="store_true",
        help="leave out files with no camera serial instead of failing",
    )
    add.set_defaults(handler=cmd_game_add)

    listing = game_commands.add_parser("list", help="list the games in the library")
    listing.add_argument(
        "--sort",
        choices=list(GAME_SORTS),
        default="id",
        help="order by game id or by game date (default: %(default)s)",
    )
    listing.add_argument("--desc", action="store_true", help="newest or highest first")
    listing.set_defaults(handler=cmd_game_list)

    for name, handler, text in [
        ("show", cmd_game_show, "show a game's cameras and files"),
        ("remove", cmd_game_remove, "remove a game from the library (files are kept)"),
    ]:
        sub = game_commands.add_parser(name, help=text)
        sub.add_argument("game", type=int, help="game id, from `gamevids game list`")
        sub.set_defaults(handler=handler)

    rename = game_commands.add_parser(
        "rename",
        help="change a game's title or date",
        description="Change a game's title, its date, or both. Leave out the title to "
        "change only the date.",
    )
    rename.add_argument("game", type=int, help="game id, from `gamevids game list`")
    rename.add_argument("title", nargs="?", help="the new title")
    rename.add_argument("--date", type=date.fromisoformat, help="new game date as YYYY-MM-DD")
    rename.set_defaults(handler=cmd_game_rename)

    scan = commands.add_parser(
        "scan",
        help="measure motion in each camera's D-zone",
        description="Measure how much moves inside each camera's D-zone over the game, "
        "save the activity curves, and write a PNG of them to check by eye. A zone given "
        "with --zone is saved for the game, so later scans can leave it out.",
    )
    scan.add_argument("game", type=int, help="game id, from `gamevids game list`")
    scan.add_argument(
        "--zone",
        nargs=2,
        action="append",
        default=[],
        metavar=("CAM", "CORNERS"),
        help=f"set a camera's D-zone: {ZONE_HELP}".replace("%", "%%"),
    )
    scan.add_argument(
        "--camera", action="append", metavar="CAM", help="scan only this camera (repeatable)"
    )
    scan.add_argument(
        "--plot", type=Path, help="where to write the PNG (default: game-<id>-activity.png)"
    )
    scan.add_argument(
        "--plot-only",
        action="store_true",
        help="save any --zone and redraw the PNG without scanning",
    )
    scan.set_defaults(handler=cmd_scan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except CommandError as e:
        print(e, file=sys.stderr)
    except (
        ProbeError,
        GameError,
        ScanError,
        VideoError,
        ZoneError,
        db.DatabaseError,
        sqlite3.Error,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
    return 1
