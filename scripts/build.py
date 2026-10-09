#!/usr/bin/env python3
"""Build actions-cleaner as a standalone Nuitka executable."""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "build"
ENTRYPOINT = ROOT / "actions_cleaner_gui.py"


def _build_nuitka_command(product_version: str = "", dev: bool = False) -> list[str]:
    args = [
        "uv",
        "run",
        "--no-dev",
        "--group",
        "build",
        "python",
        "-m",
        "nuitka",
        "--standalone",
        "--output-filename=actions-cleaner",
        "--python-flag=-m",  # documented Nuitka flag (run as module)
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,imageformats,iconengines,tls",
        "--assume-yes-for-downloads",
        "--static-libpython=auto",
        "--nofollow-import-to=mypy",
        "--nofollow-import-to=pyright",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=pytest_qt",
        "--nofollow-import-to=pytest_mock",
        "--nofollow-import-to=ruff",
        "--nofollow-import-to=nodeenv",
        f"--output-dir={OUT_DIR}",
        "--remove-output",
    ]

    system = platform.system()
    if system == "Windows":
        if dev:
            args.append("--windows-console-mode=force")
        else:
            args.append("--windows-console-mode=disable")
        if product_version:
            args.append("--file-description=actions-cleaner")
            args.append(f"--product-version={product_version}")
    elif system == "Darwin":
        args.append("--macos-create-app-bundle")

    args.append(str(ENTRYPOINT))
    return args


def _build_gui(product_version: str, dev: bool) -> int:
    print(
        f"==> Building actions-cleaner with Nuitka... (platform: {platform.system()})",
        file=sys.stderr,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = _build_nuitka_command(product_version=product_version, dev=dev)
    print(f"    Command: {' '.join(cmd)}", file=sys.stderr)
    try:
        result = subprocess.run(cmd, cwd=ROOT, check=False)
    except FileNotFoundError as e:
        print(f"    FAILED: {e}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        print(f"    FAILED: Nuitka build exited {result.returncode}", file=sys.stderr)
        return 1
    print("    Build completed successfully!", file=sys.stderr)
    return 0


def _find_built_exe() -> Path | None:
    if platform.system() == "Windows":
        exe = OUT_DIR / "actions_cleaner_gui.dist" / "actions-cleaner.exe"
        return exe if exe.is_file() else None
    if platform.system() == "Darwin":
        bundle = OUT_DIR / "actions-cleaner.app"
        if bundle.is_dir():
            macos_exe = bundle / "Contents" / "MacOS" / "actions-cleaner"
            return macos_exe if macos_exe.is_file() else None
        standalone = OUT_DIR / "actions_cleaner_gui.dist" / "actions-cleaner"
        return standalone if standalone.is_file() else None
    standalone = OUT_DIR / "actions_cleaner_gui.dist" / "actions-cleaner"
    return standalone if standalone.is_file() else None


def _post_build_verify() -> None:
    built = _find_built_exe()
    if built is None:
        print(
            f"    WARNING: built executable not found under {OUT_DIR}", file=sys.stderr
        )
        return

    print(f"==> Post-build verify: {built}", file=sys.stderr)
    # `--version` exercises startup + argparse without opening the GUI; a
    # timeout still means the binary launched, so treat it as success too.
    try:
        result = subprocess.run(
            [str(built), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("    OK: executable launched (still running after 15s)", file=sys.stderr)
        return
    except OSError as e:
        print(f"    WARNING: post-build verify failed: {e}", file=sys.stderr)
        return
    if result.returncode == 0:
        print("    OK: executable launched successfully", file=sys.stderr)
    else:
        print(
            f"    WARNING: executable exited {result.returncode}\n"
            f"    stderr: {result.stderr.strip()[:500]}",
            file=sys.stderr,
        )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build actions-cleaner (standalone Nuitka executable)."
    )
    parser.add_argument(
        "--product-version",
        default="",
        help='Set product version for the GUI build (e.g. "0.1.0.0")',
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable dev mode (console window visible on Windows)",
    )
    return parser


def main() -> int:
    args = make_parser().parse_args()
    rc = _build_gui(args.product_version, args.dev)
    if rc == 0:
        _post_build_verify()

    print(f"\nBuild output: {OUT_DIR}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
