"""The `gamevids-ui` command: open the desktop app on a library."""

import argparse
import sys
from contextlib import closing
from pathlib import Path

from gamevids import __version__, db

NO_LIBMPV = (
    "gamevids-ui needs libmpv to play video, and it isn't installed.\n"
    "On Ubuntu or Debian: sudo apt install libmpv2"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamevids-ui", description="Open the Game Vids desktop app."
    )
    parser.add_argument("--version", action="version", version=f"gamevids-ui {__version__}")
    parser.add_argument(
        "--db",
        type=Path,
        default=db.default_path(),
        help=f"library database (default: ${db.DB_ENV}, else %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args, qt_args = build_parser().parse_known_args(argv)
    try:
        import mpv  # noqa: F401  (fails here, with a clear message, when libmpv is missing)
    except OSError:
        print(NO_LIBMPV, file=sys.stderr)
        return 1

    from PySide6.QtWidgets import QApplication

    from gamevids_ui.window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Game Vids")
    with closing(db.connect(args.db)) as conn:
        window = MainWindow(conn)
        window.resize(1440, 900)
        window.show()
        return app.exec()
