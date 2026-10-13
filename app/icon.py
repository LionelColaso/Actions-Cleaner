"""Application icon handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

#: Directory holding the packaged icon assets (SVG source + rendered rasters).
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def _icon_candidate_dirs() -> list[Path]:
    """Directories searched for the icon (dev checkout and frozen builds)."""
    dirs = [ASSETS_DIR]
    if getattr(sys, "frozen", False):
        # In a Nuitka standalone build the data files ship next to the exe.
        dirs.append(Path(sys.executable).resolve().parent / "assets")
    return dirs


def find_icon_file() -> Path | None:
    """Return the packaged icon path, preferring ``.ico`` on Windows."""
    names = ("icon.ico", "icon.png") if os.name == "nt" else ("icon.png", "icon.ico")
    for directory in _icon_candidate_dirs():
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_app_icon() -> QIcon:
    """Load the designed icon asset, falling back to the drawn icon."""
    icon_file = find_icon_file()
    if icon_file is not None:
        icon = QIcon(str(icon_file))
        if not icon.isNull():
            return icon
    return build_window_icon()


def build_window_icon() -> QIcon:
    """Draw a simple trash-can icon (no external assets needed)."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(70, 70, 70), 5))
    painter.drawLine(24, 11, 40, 11)
    painter.drawLine(17, 19, 47, 19)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(200, 65, 60))
    painter.drawRoundedRect(QRect(20, 23, 24, 31), 3, 3)
    painter.setBrush(QColor(245, 245, 245))
    painter.drawRect(QRect(26, 28, 4, 20))
    painter.drawRect(QRect(34, 28, 4, 20))
    painter.end()
    return QIcon(pixmap)
