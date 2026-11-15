"""The `gamevids` command line."""

import argparse
import sqlite3
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime
from datetime import time as dt_time
from pathlib import Path

from gamevids import __version__, config, db
from gamevids.activity import ScanError, load_curve, save_curve, scan_camera
from gamevids.config import ConfigError, load_config
from gamevids.detect import DetectError, detect_clips, load_clips, load_detection, save_clips
from gamevids.export import (
    ExportError,
    default_path,
    export_video,
    kept_clips,
    plan_shots,
    save_export,
)
from gamevids.frames import VideoError, still_frame
from gamevids.games import (
    GAME_SORTS,
    Game,
    GameError,
    add_game,
    game_contents,
    list_games,
    load_game,
    remove_game,
    rename_game,
)
from gamevids.grouping import (
    Assignment,
    Chapter,
    Grouping,
    GroupingError,
    assign_files,
    camera_label,
    estimate_start,
    group_files,
)
from gamevids.periods import (
    DEFAULT_COUNT,
    PeriodError,
    load_periods,
    move_start,
    parse_period,
    periods_from_proposal,
    propose_periods,
    resolve_clock,
    save_periods,
    set_goalie,
    set_goalie_for,
)
from gamevids.plot import CameraPanel, PeriodMark, plot_activity
from gamevids.probe import ProbeError, find_videos, probe_file
from gamevids.report import (
    clock,
    format_clips,
    format_detect_settings,
    format_game,
    format_game_list,
    format_grouping,
    format_period_settings,
    format_periods,
    format_triage,
    hms,
    removal_items,
    removal_note,
)
from gamevids.triage import load_triage
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


def _local_time(text: str, day: date) -> datetime:
    try:
        of_day = dt_time.fromisoformat(text.strip())
    except ValueError:
        raise CommandError(f"{text!r} isn't a time; use HH:MM:SS, e.g. 14:53:59") from None
    return datetime.combine(day, of_day).astimezone()


def _find_unassigned(grouping: Grouping, name: str, flag: str) -> Chapter:
    """The file with no serial that `name` (a file name or path) points at."""
    for chapter in grouping.unassigned:
        path = chapter.file.path
        if name in (path.name, str(path)) or Path(name).resolve() == path.resolve():
            return chapter
    known = "".join(f"\n  {c.file.path.name}" for c in grouping.unassigned) or " none"
    raise CommandError(f"{flag} {name}: not one of the files with no camera serial:{known}")


def _assign(grouping: Grouping, args: argparse.Namespace) -> Grouping:
    """Place the files named with --assign on cameras, at --start or their timecode."""
    starts = {}
    for name, text in args.start or []:
        starts[_find_unassigned(grouping, name, "--start").file.path] = text
    assignments = {}
    cameras = {c.label: c for c in grouping.cameras}
    for name, label in args.assign or []:
        chapter = _find_unassigned(grouping, name, "--assign")
        info = chapter.file
        try:
            label = camera_label(label)
        except GroupingError as e:
            raise CommandError(str(e)) from None
        start = None
        if info.path in starts:
            known = grouping.date or info.creation_time
            day = args.date or (known.astimezone().date() if known else date.today())
            start = _local_time(starts.pop(info.path), day)
        elif info.creation_time is None:
            camera = cameras.get(label)
            start = estimate_start(info, camera) if camera else None
            if start is None:
                raise CommandError(
                    f"{info.path.name} has no start time, and camera {label} has no timecode "
                    f"to place it by. Give one with --start {info.path.name!r} HH:MM:SS."
                )
            print(
                f"Placing {info.path.name} at {clock(start)}, from its timecode.",
                file=sys.stderr,
            )
        assignments[info.path] = Assignment(label, chapter.period, start)
    if starts:
        names = ", ".join(p.name for p in starts)
        raise CommandError(f"--start needs --assign for the same file: {names}")
    try:
        return assign_files(grouping, assignments)
    except GroupingError as e:
        raise CommandError(str(e)) from None


def cmd_game_add(args: argparse.Namespace) -> int:
    grouping = _assign(_group(args.paths), args)
    if grouping.unassigned:
        names = "".join(f"\n  {c.file.path.name}" for c in grouping.unassigned)
        if not args.skip_unassigned:
            raise CommandError(
                f"These files have no camera serial:{names}\n"
                "Place each on a camera with --assign FILE CAMERA, or pass --skip-unassigned "
                "to add the game without them."
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


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question on the terminal; anything but yes is no."""
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_game_remove(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        contents = game_contents(conn, args.game)
        if not args.yes:
            print(f"Game {contents.id}: {contents.title} ({contents.date.isoformat()})")
            print("Removing it deletes from the library:")
            for item in removal_items(contents):
                print(f"  {item}")
            print(f"{removal_note(contents)} This can't be undone.")
            if not sys.stdin.isatty():
                raise CommandError("Not removed. Pass --yes to remove it without being asked.")
            if not _confirm("Remove it?"):
                print("Not removed.")
                return 1
        remove_game(conn, args.game)
    print(f"Removed game {args.game} ({contents.title}). Its video files were not touched.")
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
    """Shows progress on stderr: one updating line on a terminal, else every 10%.

    `label` names what's progressing, e.g. "Camera A".
    """

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
        text = f"{self.label}: {hms(done)} of {hms(total)} ({fraction:.0%}){speed}"
        print(
            f"\r{text}\033[K" if self.tty else text,
            end="" if self.tty else "\n",
            file=sys.stderr,
            flush=True,
        )

    def finish(self) -> None:
        if self.tty and self.shown_at:
            print(file=sys.stderr)


def _labels(game: Game, cameras: list[str] | None) -> list[str]:
    """The cameras named with --camera, in order and once each, else all of them."""
    chosen = [game.camera(c).label for c in cameras or []]
    return list(dict.fromkeys(chosen)) or [c.label for c in game.grouping.cameras]


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
                detection=load_detection(conn, camera_id),
                clips=load_clips(conn, camera_id),
            )
        )
    labels = {camera_id: label for label, camera_id in game.camera_ids.items()}
    marks = [
        PeriodMark(p.name, p.start, labels.get(p.goalie_camera_id))
        for p in load_periods(conn, game.id)
    ]
    title = f"Game {game.id}: {game.title}  ({game.date.isoformat()})"
    plot_activity(title, panels, path, marks)
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
        labels = _labels(game, args.camera)

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
            progress = _ProgressLine(f"Camera {label}")
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


def cmd_detect(args: argparse.Namespace) -> int:
    settings_file = load_config(args.config)
    settings = settings_file.detect
    with _open(args) as conn:
        game = load_game(conn, args.game)
        labels = _labels(game, args.camera)
        curves = {}
        for label in labels:
            camera_id = game.camera_ids[label]
            curve = load_curve(conn, camera_id)
            if curve is None:
                raise CommandError(
                    f"Camera {label} hasn't been scanned yet. "
                    f"Run `gamevids scan {game.id} --camera {label}` first."
                )
            zone = load_zone(conn, camera_id)
            if zone is not None and tuple(zone) != tuple(curve.polygon):
                print(
                    f"warning: camera {label}'s zone has changed since it was scanned; "
                    "these clips use the old zone until you scan again.",
                    file=sys.stderr,
                )
            triaged = sum(c.triaged for c in load_clips(conn, camera_id))
            if triaged and not args.force:
                raise CommandError(
                    f"{triaged} of camera {label}'s clips have been triaged, and detecting "
                    "again would replace them.\nPass --force to replace them anyway."
                )
            curves[label] = curve

        for label, curve in curves.items():
            segments = detect_clips(curve, settings)
            camera_id = game.camera_ids[label]
            save_clips(conn, camera_id, segments, settings, curve, replace_triaged=True)

        clips = _detected_clips(conn, game)
        source = f"from {settings_file.path}" if settings_file.path else "defaults"
        print(f"Settings ({source}): {format_detect_settings(settings)}")
        print()
        print(format_clips(game, clips))
        _write_plot(conn, game, args.plot or Path(f"game-{game.id}-activity.png"))
    return 0


def _detected_clips(conn: sqlite3.Connection, game: Game) -> dict:
    """Each detected camera's clips by label; cameras never detected are left out."""
    clips = {}
    for camera in game.grouping.cameras:
        camera_id = game.camera_ids[camera.label]
        if load_detection(conn, camera_id) is not None:
            clips[camera.label] = load_clips(conn, camera_id)
    return clips


def cmd_periods(args: argparse.Namespace) -> int:
    settings_file = load_config(args.config)
    with _open(args) as conn:
        game = load_game(conn, args.game)
        cameras = game.grouping.cameras
        camera_ids = [game.camera_ids[c.label] for c in cameras]
        footage = game.grouping.footage
        saved = load_periods(conn, game.id)
        curves = {c.label: load_curve(conn, game.camera_ids[c.label]) for c in cameras}
        unscanned = [label for label, curve in curves.items() if curve is None]

        # With nothing saved, --start alone lays out periods by hand without a scan.
        propose = args.propose or args.count is not None or not (saved or args.start)
        if propose and unscanned:
            raise CommandError(
                f"Camera {unscanned[0]} hasn't been scanned yet. "
                f"Run `gamevids scan {game.id} --camera {unscanned[0]}` first."
            )
        count = args.count or len(saved) or DEFAULT_COUNT
        proposal = None
        if not unscanned:
            scanned = [curve for curve in curves.values() if curve is not None]
            proposal = propose_periods(scanned, count, settings_file.periods)

        periods = saved
        if propose:
            assert proposal is not None
            moved = [p.name for p in saved if p.moved]
            if moved and not args.force:
                names = " and ".join(moved)
                raise CommandError(
                    f"{names}'s start was set by hand, and proposing again would replace it.\n"
                    "Pass --force to replace it anyway."
                )
            periods = periods_from_proposal(proposal, saved, camera_ids)
        for number, text in args.start:
            periods = move_start(periods, number, resolve_clock(text, footage), footage, camera_ids)
        for spec in args.goalie:
            name, _, label = spec.rpartition("=")
            camera_id = game.camera_ids[game.camera(label).label]
            if name:
                periods = set_goalie_for(periods, parse_period(name), camera_id)
            else:
                periods = set_goalie(periods, camera_ids, camera_id)
        if periods != saved:
            save_periods(conn, game.id, periods)

        if propose:
            assert proposal is not None
            source = f"from {settings_file.path}" if settings_file.path else "defaults"
            print(f"Settings ({source}): {format_period_settings(settings_file.periods)}")
            if proposal.warmup_end:
                print(f"Warmup until {clock(proposal.warmup_end)}.")
            else:
                print("No warmup found, so P1 starts with the footage.")
            if len(proposal.starts) < count:
                print(
                    f"Only {len(proposal.starts)} of {count} periods fit between quiet spells; "
                    "add the rest with --start."
                )
            print()
        print(format_periods(game, periods, _detected_clips(conn, game), proposal))

        hints = []
        if propose:
            hints.append("Move a start with --start P2=HH:MM:SS.")
        if periods and periods[0].goalie_camera_id is None:
            hints.append(
                "Choose the camera behind our goalie's net in P1 with --goalie CAM; "
                "later periods alternate ends."
            )
        if hints:
            print()
            print("\n".join(hints))
        _write_plot(conn, game, args.plot or Path(f"game-{game.id}-activity.png"))
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        game = load_game(conn, args.game)
        triage = load_triage(conn, game)
        if triage.missing == "clips":
            raise CommandError(
                f"Game {game.id} has no clips yet. Run `gamevids scan {game.id}`, then "
                f"`gamevids detect {game.id}`, to find them."
            )
        if triage.missing == "periods":
            raise CommandError(
                f"Game {game.id} has no periods yet. Run `gamevids periods {game.id}` first."
            )
        if triage.missing == "goalie":
            raise CommandError(
                "Our goalie's end isn't chosen, so their clips aren't known. "
                f"Run `gamevids periods {game.id} --goalie CAM` first."
            )
        print(format_triage(game, triage))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with _open(args) as conn:
        game = load_game(conn, args.game)
        shots = plan_shots(game, kept_clips(conn, game))
        if not shots:
            raise CommandError(
                f"Game {game.id} has no kept clips. Keep some on the app's Triage tab first "
                f"(`gamevids triage {game.id}` shows how far triage has got)."
            )
        triage = load_triage(conn, game)
        undecided = len(triage.clips) - triage.decided
        if undecided:
            print(
                f"warning: {undecided} of our goalie's clips are still undecided and left out.",
                file=sys.stderr,
            )
        output = args.output or default_path(game)
        if output.is_dir() or not output.suffix:  # a folder, perhaps not made yet
            output = output / default_path(game).name
        if output.exists() and not args.force:
            raise CommandError(f"{output} already exists. Pass --force to replace it.")

        progress = _ProgressLine("Exporting")
        started = time.monotonic()
        try:
            result = export_video(game.title, shots, output, progress=progress)
        except KeyboardInterrupt:
            progress.finish()
            print("Stopped. Nothing was written.", file=sys.stderr)
            return 130
        progress.finish()
        save_export(conn, game.id, result)
    took = time.monotonic() - started
    size = result.size / 1e6
    clips = f"{result.clips} clip" + ("" if result.clips == 1 else "s")
    print(f"Wrote {result.path}: {clips} in {hms(result.duration)}, {size:.0f} MB, in {hms(took)}.")
    return 0


def _period_time(text: str) -> tuple[int, str]:
    """`P2=12:15:30` as `(2, "12:15:30")`; the time is checked against the game later."""
    name, sep, value = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"{text!r} needs a period and a time, e.g. P2=12:15:30")
    try:
        return parse_period(name), value
    except PeriodError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


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
    parser.add_argument(
        "--config",
        type=Path,
        default=config.default_path(),
        help=f"settings file (default: ${config.CONFIG_ENV}, or %(default)s)",
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
        "--assign",
        nargs=2,
        action="append",
        metavar=("FILE", "CAMERA"),
        help="put a file with no camera serial on camera A, B, ... (a new letter makes a new "
        "camera); a file with no start time is placed by its timecode",
    )
    add.add_argument(
        "--start",
        nargs=2,
        action="append",
        metavar=("FILE", "HH:MM:SS"),
        help="local start time for a file given with --assign, on the game date",
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

    show = game_commands.add_parser("show", help="show a game's cameras and files")
    show.add_argument("game", type=int, help="game id, from `gamevids game list`")
    show.set_defaults(handler=cmd_game_show)

    remove = game_commands.add_parser(
        "remove",
        help="remove a game from the library (files are kept)",
        description="Delete everything the library holds for a game: its cameras and file "
        "list, D-zones, scans, clips and their triage, periods, and export history. Its "
        "video files and exported videos are left alone. Says what will go and asks first.",
    )
    remove.add_argument("game", type=int, help="game id, from `gamevids game list`")
    remove.add_argument("-y", "--yes", action="store_true", help="remove it without asking")
    remove.set_defaults(handler=cmd_game_remove)

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

    detect = commands.add_parser(
        "detect",
        help="find candidate clips in the activity curves",
        description="Turn each scanned camera's activity curve into candidate clips, replace "
        "the camera's earlier clips with them, list every clip in the game, and redraw the "
        "PNG with the clips marked. The settings come from the [detect] section of the "
        "--config file; anything it leaves out keeps its default.",
    )
    detect.add_argument("game", type=int, help="game id, from `gamevids game list`")
    detect.add_argument(
        "--camera", action="append", metavar="CAM", help="detect only this camera (repeatable)"
    )
    detect.add_argument(
        "--plot", type=Path, help="where to write the PNG (default: game-<id>-activity.png)"
    )
    detect.add_argument(
        "--force",
        action="store_true",
        help="replace clips even when some have been triaged (that triage is lost)",
    )
    detect.set_defaults(handler=cmd_detect)

    periods = commands.add_parser(
        "periods",
        help="propose where each period starts and set our goalie's end",
        description="Show the game's periods. The first time, or with --propose, propose "
        "where each period starts from the quiet spells between periods on the activity "
        "curves, and save them. Then move any start by hand with --start, and choose the "
        "camera behind our goalie's net with --goalie. Redraws the PNG with the periods "
        "marked. The proposal's settings come from the [periods] section of the --config "
        "file.",
    )
    periods.add_argument("game", type=int, help="game id, from `gamevids game list`")
    periods.add_argument(
        "--propose",
        action="store_true",
        help="propose the starts again, replacing the saved ones (our goalie's ends are kept)",
    )
    periods.add_argument(
        "--count",
        type=int,
        help=f"propose this many periods, e.g. 4 with overtime (default: as saved, else "
        f"{DEFAULT_COUNT})",
    )
    periods.add_argument(
        "--force",
        action="store_true",
        help="propose again even when a start was set by hand (that start is lost)",
    )
    periods.add_argument(
        "--start",
        action="append",
        default=[],
        type=_period_time,
        metavar="P=TIME",
        help="set a period's start by hand as local time, e.g. P2=12:15:30 (repeatable); "
        "the period after the last adds one",
    )
    periods.add_argument(
        "--goalie",
        action="append",
        default=[],
        metavar="CAM|P=CAM",
        help="the camera behind our goalie's net: CAM sets P1 and alternates ends after "
        "it, P=CAM sets one period, e.g. OT=B (repeatable)",
    )
    periods.add_argument(
        "--plot", type=Path, help="where to write the PNG (default: game-<id>-activity.png)"
    )
    periods.set_defaults(handler=cmd_periods)

    triage = commands.add_parser(
        "triage",
        help="list our goalie's clips and how far triage has got",
        description="List the clips that start on the camera behind our goalie's net in "
        "each period, in the order the Triage tab plays them, with each one's status, star "
        "and trim, and the clip triage resumes at.",
    )
    triage.add_argument("game", type=int, help="game id, from `gamevids game list`")
    triage.set_defaults(handler=cmd_triage)

    export = commands.add_parser(
        "export",
        help="make the game video from the kept clips",
        description="Make the game video: a title card with the game's title, then every "
        "kept clip from every camera in the order they happened, with a short dip to black "
        "between clips. The video is 1080p60 H.264 with the rink audio, written to "
        '"~/Videos/Game Vids/<title> - DZone.mp4" unless --output says otherwise.',
    )
    export.add_argument("game", type=int, help="game id, from `gamevids game list`")
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        help="the video file to write, or a folder to write it in (a name with no extension is a "
        "folder)",
    )
    export.add_argument(
        "--force", action="store_true", help="replace the video if it already exists"
    )
    export.set_defaults(handler=cmd_export)

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
        DetectError,
        PeriodError,
        ConfigError,
        VideoError,
        ZoneError,
        ExportError,
        db.DatabaseError,
        sqlite3.Error,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
    return 1
