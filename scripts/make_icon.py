#!/usr/bin/env python3
"""Regenerate the app icon rasters (assets/icon.png + icon.ico) from icon.svg.

Uses PySide6's QtSvg module to render the SVG source into a pixmap, then saves
it as both PNG (all platforms) and ICO (Windows). Requires the `PySide6`
package plus its `QtSvg` plugin (part of the PySide6 wheel on Python 3.14).

Usage:
    just icon          # via justfile
    python scripts/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
SVG_SOURCE = ASSETS_DIR / "icon.svg"
PNG_OUTPUT = ASSETS_DIR / "icon.png"
ICO_OUTPUT = ASSETS_DIR / "icon.ico"

# Render at the largest size for the sharpest raster; ICO/PNG writers accept
# the same pixmap. Windows scales the ICO to taskbar/title-bar sizes.
RENDER_SIZE = 256


def render_svg_to_pixmap(svg_path: Path, size: int) -> QPixmap:
    """Render an SVG file to a pixmap at the given pixel size."""
    if not svg_path.is_file():
        raise FileNotFoundError(f"SVG source not found: {svg_path}")
    svg_bytes = svg_path.read_bytes()
    renderer = QSvgRenderer(svg_bytes)
    if not renderer.isValid():
        raise RuntimeError(f"failed to parse SVG: {svg_path}")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def main() -> int:
    # QtSvg requires a QApplication to exist before rendering.
    if QApplication.instance() is None:
        QApplication(sys.argv)

    if not SVG_SOURCE.is_file():
        print(f"ERROR: {SVG_SOURCE} not found", file=sys.stderr)
        return 1

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    pixmap = render_svg_to_pixmap(SVG_SOURCE, RENDER_SIZE)
    if pixmap.isNull():
        print("ERROR: rendered pixmap is null", file=sys.stderr)
        return 1

    if not pixmap.save(str(PNG_OUTPUT), "PNG"):
        print(f"ERROR: could not write {PNG_OUTPUT}", file=sys.stderr)
        return 1
    print(f"Wrote {PNG_OUTPUT.relative_to(ROOT)} ({pixmap.width()}x{pixmap.height()})")

    if not pixmap.save(str(ICO_OUTPUT), "ICO"):
        print(f"ERROR: could not write {ICO_OUTPUT}", file=sys.stderr)
        return 1
    print(f"Wrote {ICO_OUTPUT.relative_to(ROOT)} ({pixmap.width()}x{pixmap.height()})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
