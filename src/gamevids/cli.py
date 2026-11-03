"""The `gamevids` command line."""

import argparse
import sys
from pathlib import Path

from gamevids import __version__
from gamevids.grouping import group_files
from gamevids.probe import ProbeError, find_videos, probe_file
from gamevids.report import format_grouping


def cmd_probe(args: argparse.Namespace) -> int:
    paths = find_videos(args.paths)
    if not paths:
        print("No video files found.", file=sys.stderr)
        return 1
    try:
        files = [probe_file(p) for p in paths]
    except ProbeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(format_grouping(group_files(files)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamevids",
        description="Turn full-game goalie footage into short game videos.",
    )
    parser.add_argument("--version", action="version", version=f"gamevids {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    probe = commands.add_parser(
        "probe",
        help="show cameras, chapters and stitched timelines for a game's files",
        description="Group a game's video files by camera and stitch each camera's chapters.",
    )
    probe.add_argument("paths", nargs="+", type=Path, help="video files or a game folder")
    probe.set_defaults(handler=cmd_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return args.handler(args)
