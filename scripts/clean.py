#!/usr/bin/env python3
"""Clean up build artifacts, caches, and generated files."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DIRS = [
    "build",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "htmlcov",
    "junit",
]
FILES = ["coverage.xml", ".coverage"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove build artifacts, caches, and generated files (cross-platform)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting",
    )
    args = parser.parse_args()

    removed: list[Path] = []

    for name in DIRS:
        p = ROOT / name
        if p.exists():
            removed.append(p)
            if not args.dry_run:
                shutil.rmtree(p, ignore_errors=True)

    for name in FILES:
        p = ROOT / name
        if p.exists():
            removed.append(p)
            if not args.dry_run:
                p.unlink(missing_ok=True)

    for p in ROOT.rglob("__pycache__"):
        if p.is_dir():
            removed.append(p)
            if not args.dry_run:
                shutil.rmtree(p, ignore_errors=True)

    for p in removed:
        print(f"{'[dry-run] ' if args.dry_run else ''}removed: {p.relative_to(ROOT)}")

    if not removed:
        print("Nothing to clean.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
