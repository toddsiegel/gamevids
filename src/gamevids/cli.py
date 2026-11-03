"""The `gamevids` command line."""

import argparse

from gamevids import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamevids",
        description="Turn full-game goalie footage into short game videos.",
    )
    parser.add_argument("--version", action="version", version=f"gamevids {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
