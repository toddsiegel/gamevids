"""The core package must stay free of Qt so it can run headless."""

import ast
from pathlib import Path

import gamevids

CORE_DIR = Path(gamevids.__file__).parent
QT_MODULES = {"PySide6", "PyQt5", "PyQt6", "shiboken6", "gamevids_ui"}


def imported_roots(path: Path) -> set[str]:
    roots = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_core_does_not_import_qt():
    offenders = {
        str(path.relative_to(CORE_DIR)): sorted(imported_roots(path) & QT_MODULES)
        for path in CORE_DIR.rglob("*.py")
    }
    assert {name: mods for name, mods in offenders.items() if mods} == {}
