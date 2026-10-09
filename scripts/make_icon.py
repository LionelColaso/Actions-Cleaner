#!/usr/bin/env python3
"""Regenerate the app icon rasters (assets/icon.png + icon.ico) from icon.svg.

Uses PySide6's QtSvg module to the render SVG source into pixmaps at multiple
sizes, then writes a multi-resolution ICO (Windows) and a 256px PNG (all
platforms). Requires the `PySide6` package plus its `QtSvg` plugin (part of
the PySide6 wheel on Python 3.14).

Usage:
    just icon          # via justfile
    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
SVG_SOURCE = ASSETS_DIR / "icon.svg"
PNG_OUTPUT = ASSETS_DIR / "icon.png"
ICO_OUTPUT = ASSETS_DIR / "icon.ico"

# Sizes embedded in the multi-resolution ICO. 256 is the largest; smaller
# entries let Windows scale the icon for the taskbar / title bar.
ICO_SIZES = (256, 128, 64, 48, 32, 16)
# PNG is written at the largest size for crispness on HiDPI displays.
PNG_SIZE = 256


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


def _pixmap_to_argb_bytes(pixmap: QPixmap) -> bytes:
    """Convert a pixmap to a 32-bit ARGB byte buffer (BGRA on disk)."""
    image = pixmap.toImage()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    raw = image.bits()
    # image.bits() returns a pointer; read the full buffer.
    size = pixmap.width() * pixmap.height() * 4
    data = bytes(raw[:size])
    # Qt stores ARGB; ICO expects BGRA — swap R and B channels.
    arr = bytearray(data)
    for i in range(0, len(arr), 4):
        arr[i], arr[i + 2] = arr[i + 2], arr[i]
    return bytes(arr)


def _write_ico(pixmaps: list[QPixmap], output_path: Path) -> None:
    """Write a multi-resolution ICO file from a list of pixmaps."""
    entries: list[bytes] = []
    images: list[tuple[bytes, bytes, bytes]] = []

    for pixmap in pixmaps:
        w = pixmap.width()
        h = pixmap.height()
        # ICO directory entry: 16 bytes.
        entry = struct.pack(
            "<BBBBHHII",
            w if w < 256 else 0,  # 0 means 256
            h if h < 256 else 0,  # 0 means 256
            0,  # color count (0 = no palette)
            0,  # reserved
            1,  # planes
            32,  # bit count
            0,  # filled in later
            0,  # filled in later
        )
        argb = _pixmap_to_argb_bytes(pixmap)
        # AND mask: 1-bit, padded to 4-byte boundary, bottom-up.
        mask_row_bytes = ((w + 31) // 32) * 4
        mask_buf = bytearray(mask_row_bytes * h)
        images.append((entry, argb, bytes(mask_buf)))

    # Compute offsets: header (6) + entries (16 each) + image data.
    offset = 6 + 16 * len(images)
    for raw_entry, argb, mask_bytes in images:
        # Rebuild the directory entry with the correct byte size and offset.
        entry = struct.pack(
            "<BBBBHHII",
            *struct.unpack("<BBBBHHII", raw_entry)[:6],
            len(argb) + len(mask_bytes),
            offset,
        )
        entries.append(entry)
        offset += len(argb) + len(mask_bytes)

    with open(output_path, "wb") as f:
        # Header: reserved, type=1 (ICO), count.
        f.write(struct.pack("<HHH", 0, 1, len(images)))
        for entry in entries:
            f.write(entry)
        for _raw_entry, argb, mask_bytes in images:
            f.write(argb)
            f.write(mask_bytes)


def main() -> int:
    # QtSvg requires a QApplication to exist before rendering.
    if QApplication.instance() is None:
        QApplication(sys.argv)

    if not SVG_SOURCE.is_file():
        print(f"ERROR: {SVG_SOURCE} not found", file=sys.stderr)
        return 1

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # Render the PNG at the largest size.
    pixmap = render_svg_to_pixmap(SVG_SOURCE, PNG_SIZE)
    if pixmap.isNull():
        print("ERROR: rendered pixmap is null", file=sys.stderr)
        return 1

    if not pixmap.save(str(PNG_OUTPUT), "PNG"):
        print(f"ERROR: could not write {PNG_OUTPUT}", file=sys.stderr)
        return 1
    print(f"Wrote {PNG_OUTPUT.relative_to(ROOT)} ({pixmap.width()}x{pixmap.height()})")

    # Render at each ICO size and write a multi-resolution container.
    pixmaps = [render_svg_to_pixmap(SVG_SOURCE, size) for size in ICO_SIZES]
    _write_ico(pixmaps, ICO_OUTPUT)
    print(f"Wrote {ICO_OUTPUT.relative_to(ROOT)} ({len(pixmaps)} sizes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
