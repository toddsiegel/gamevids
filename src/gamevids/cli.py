"""The `gamevids` command line."""

import argparse
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path

from gamevids import __version__, db
from gamevids.games import GameError, add_game, list_games, load_game, remove_game
from gamevids.grouping import Grouping, group_files
from gamevids.probe import ProbeError, find_videos, probe_file
from gamevids.report import format_game, format_game_list, format_grouping


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
        print(format_game_list(list_games(conn)))
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

    game = commands.add_parser("game", help="add, list, show and remove games")
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
    listing.set_defaults(handler=cmd_game_list)

    for name, handler, text in [
        ("show", cmd_game_show, "show a game's cameras and files"),
        ("remove", cmd_game_remove, "remove a game from the library (files are kept)"),
    ]:
        sub = game_commands.add_parser(name, help=text)
        sub.add_argument("game", type=int, help="game id, from `gamevids game list`")
        sub.set_defaults(handler=handler)

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
    except (ProbeError, GameError, db.DatabaseError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
    return 1
