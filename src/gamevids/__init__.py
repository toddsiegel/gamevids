"""Core of Game Vids: probing, stitching, scanning, detection and export.

This package must never import Qt, so every feature stays usable from the CLI and tests.
"""

from importlib.metadata import version

__version__ = version("gamevids")
