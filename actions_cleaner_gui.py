#!/usr/bin/env python3
"""Clean up GitHub Actions workflow runs with a PySide6 GUI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, TypedDict, cast

from PySide6.QtCore import QObject, QRect, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

VERSION = "0.1.0"

#: GitHub slugs: alphanumerics plus `-`, `_`, `.` on both sides of the slash.
REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+")
#: `github.com/owner/repo...` with optional scheme/`www.` — first two path
#: segments only, so issue/PR/action URLs resolve to their repository.
GITHUB_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s?#]+/[^/\s?#]+)",
    re.IGNORECASE,
)
#: SSH clone remotes: `git@github.com:owner/repo[.git]`.
GITHUB_SSH_PATTERN = re.compile(
    r"^git@github\.com:([^/\s]+/[^/\s]+)$",
    re.IGNORECASE,
)

#: Saved repositories — a bare, human-readable JSON array (AGENTS.md §4.5).
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".actions-cleaner-repos.json")
#: Saved preferences (`keep`, `failed_only`). Dry-run is deliberately *not*
#: persisted so the app always starts in safe dry-run mode (AGENTS.md §4.3).
PREFERENCES_PATH = os.path.join(
    os.path.expanduser("~"), ".actions-cleaner-settings.json"
)

GH_LIST_TIMEOUT = 60  # seconds for `gh run list`
GH_DELETE_TIMEOUT = 30  # seconds per `gh run delete`
GH_AUTH_TIMEOUT = 15  # seconds for `gh auth status` (fail fast)
#: Bounds for the user-configurable timeouts (seconds).
TIMEOUT_MIN = 10  # allows lower defaults like auth=15
TIMEOUT_MAX = 180  # generous upper bound for slow networks
TIMEOUT_LIST_DEFAULT = 60  # reasonable for potentially large run lists
TIMEOUT_DELETE_DEFAULT = 30  # match GH_DELETE_TIMEOUT — delete is fast
TIMEOUT_AUTH_DEFAULT = 15  # MUST be low for fast auth failure detection

#: Granularity of one `gh run list` page; total fetch capped by `fetch_limit`.
GH_PAGE_SIZE = 100
#: Bounds for the user-configurable fetch limit (runs fetched per cleanup).
FETCH_LIMIT_MIN = 100
FETCH_LIMIT_MAX = 3000
FETCH_LIMIT_DEFAULT = 1000
#: Bounds for the user-configurable per-run deletion cap (0 = unlimited).
MAX_DELETIONS_MIN = 0
MAX_DELETIONS_MAX = 5000
#: Bounds for the user-configurable deletion concurrency (1 = sequential).
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 10
CONCURRENCY_DEFAULT = 1

DRY_RUN_PROGRESS_STEP = 25  # throttle progress signals during dry runs
LOG_MAX_BLOCKS = 5000  # cap on the log widget's line count

#: Run conclusions treated as "failed" by the failed-only filter.
FAILED_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "startup_failure"})


class RunInfo(TypedDict):
    """One workflow run as returned by `gh run list --json`."""

    databaseId: int
    headSha: str
    createdAt: str
    conclusion: str


def is_valid_repo(repo: str) -> bool:
    """Return True for a plausible GitHub `owner/repo` slug."""
    return REPO_PATTERN.fullmatch(repo) is not None


def normalize_repo(value: str) -> str:
    """Return `owner/repo` from a slug, GitHub URL, or SSH remote.

    Accepts plain `owner/repo` slugs unchanged, and extracts the slug from
    forms like ``https://github.com/owner/repo`` (any scheme, optional
    ``www.``, trailing paths such as ``/actions/runs/42``, query strings,
    ``.git`` suffixes) and ``git@github.com:owner/repo.git``. Input that is
    not a GitHub remote is returned unchanged (and must then pass
    :func:`is_valid_repo`).
    """
    text = value.strip()
    match = GITHUB_URL_PATTERN.match(text) or GITHUB_SSH_PATTERN.match(text)
    if match is None:
        return text
    slug = match.group(1)
    slug = slug.removesuffix(".git")
    return slug


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow a decoded-JSON value to an object (``{}`` when it isn't one)."""
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


def list_runs(
    repo: str,
    fetch_limit: int = FETCH_LIMIT_DEFAULT,
    workflow: str = "",
    timeout: int = GH_LIST_TIMEOUT,
) -> list[RunInfo]:
    """Fetch up to `fetch_limit` most-recent workflow runs for `repo`.

    ``gh run list --limit N`` means "maximum number of runs to fetch" and
    pages the REST API internally (100 runs/page), so raising the limit needs
    no manual pagination code on our side. When `workflow` is non-empty the
    ``--workflow`` flag narrows the fetch to a single workflow name or ID.
    """
    limit = max(FETCH_LIMIT_MIN, min(fetch_limit, FETCH_LIMIT_MAX))
    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--limit",
        str(limit),
        "--json",
        "databaseId,headSha,createdAt,conclusion",
    ]
    if workflow:
        cmd.extend(["--workflow", workflow])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    data: Any = json.loads(result.stdout)
    if not isinstance(data, list):
        raise TypeError(
            f"unexpected gh output (expected a list, got {type(data).__name__})"
        )
    items = cast(list[dict[str, Any]], data)
    runs: list[RunInfo] = []
    for item in items:
        obj = _as_dict(item)
        if "databaseId" not in obj or "headSha" not in obj:
            continue
        runs.append(
            RunInfo(
                databaseId=int(obj["databaseId"]),
                headSha=str(obj["headSha"]),
                createdAt=str(obj.get("createdAt") or ""),
                conclusion=str(obj.get("conclusion") or ""),
            )
        )
    return runs


def filter_failed_runs(runs: list[RunInfo]) -> list[RunInfo]:
    """Keep only runs whose conclusion counts as failed."""
    return [run for run in runs if run["conclusion"] in FAILED_CONCLUSIONS]


def select_runs_to_delete(
    runs: list[RunInfo], keep: int
) -> tuple[list[str], list[str]]:
    """Split runs into ``(delete, keep)`` id lists.

    Runs are ordered by ``createdAt`` (newest first) so the newest ``keep``
    distinct head SHAs — and every run belonging to them — are preserved.
    """
    ordered = sorted(runs, key=lambda run: run["createdAt"], reverse=True)
    newest_shas: list[str] = []
    seen: set[str] = set()
    for run in ordered:
        sha = run["headSha"]
        if sha not in seen:
            seen.add(sha)
            newest_shas.append(sha)
    keep_shas = set(newest_shas[:keep])
    delete_ids = [
        str(run["databaseId"]) for run in ordered if run["headSha"] not in keep_shas
    ]
    kept_ids = [
        str(run["databaseId"]) for run in ordered if run["headSha"] in keep_shas
    ]
    return delete_ids, kept_ids


def load_repos() -> list[str]:
    """Load saved repositories; tolerates bare-list and legacy dict formats."""
    if not os.path.exists(SETTINGS_PATH):
        return []
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return []
    raw: Any
    if isinstance(data, dict):
        raw = _as_dict(data).get("repos", [])
    else:
        raw = data
    if not isinstance(raw, list):
        return []
    items = cast(list[object], raw)
    repos: list[str] = []
    for entry in items:
        if isinstance(entry, str) and entry and entry not in repos:
            repos.append(entry)
    return repos


def save_repos(repos: list[str]) -> bool:
    """Persist repositories as a bare JSON array; False on I/O failure."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(repos, f, indent=2)
        return True
    except OSError:
        return False


def load_preferences() -> tuple[int, bool, int, int, str, int, int, int, int]:
    """Return ``(keep, failed_only, fetch_limit, max_deletions, last_repo,
    concurrency, list_timeout, delete_timeout, auth_timeout)``."""
    keep = 2
    failed_only = False
    fetch_limit = FETCH_LIMIT_DEFAULT
    max_deletions = MAX_DELETIONS_MIN
    last_repo = ""
    concurrency = CONCURRENCY_DEFAULT
    list_timeout = TIMEOUT_LIST_DEFAULT
    delete_timeout = TIMEOUT_DELETE_DEFAULT
    auth_timeout = TIMEOUT_AUTH_DEFAULT
    try:
        with open(PREFERENCES_PATH, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return (
            keep,
            failed_only,
            fetch_limit,
            max_deletions,
            last_repo,
            concurrency,
            list_timeout,
            delete_timeout,
            auth_timeout,
        )
    obj = _as_dict(data)
    raw_keep = obj.get("keep")
    if (
        isinstance(raw_keep, int)
        and not isinstance(raw_keep, bool)
        and 1 <= raw_keep <= 100
    ):
        keep = raw_keep
    failed_candidate = obj.get("failed_only")
    if isinstance(failed_candidate, bool):
        failed_only = failed_candidate
    raw_limit = obj.get("fetch_limit")
    if (
        isinstance(raw_limit, int)
        and not isinstance(raw_limit, bool)
        and FETCH_LIMIT_MIN <= raw_limit <= FETCH_LIMIT_MAX
    ):
        fetch_limit = raw_limit
    raw_max_del = obj.get("max_deletions")
    if (
        isinstance(raw_max_del, int)
        and not isinstance(raw_max_del, bool)
        and MAX_DELETIONS_MIN <= raw_max_del <= MAX_DELETIONS_MAX
    ):
        max_deletions = raw_max_del
    raw_last_repo = obj.get("last_repo")
    if isinstance(raw_last_repo, str) and is_valid_repo(raw_last_repo):
        last_repo = raw_last_repo
    raw_concurrency = obj.get("concurrency")
    if (
        isinstance(raw_concurrency, int)
        and not isinstance(raw_concurrency, bool)
        and CONCURRENCY_MIN <= raw_concurrency <= CONCURRENCY_MAX
    ):
        concurrency = raw_concurrency
    raw_list_timeout = obj.get("list_timeout")
    if (
        isinstance(raw_list_timeout, int)
        and not isinstance(raw_list_timeout, bool)
        and TIMEOUT_MIN <= raw_list_timeout <= TIMEOUT_MAX
    ):
        list_timeout = raw_list_timeout
    raw_delete_timeout = obj.get("delete_timeout")
    if (
        isinstance(raw_delete_timeout, int)
        and not isinstance(raw_delete_timeout, bool)
        and TIMEOUT_MIN <= raw_delete_timeout <= TIMEOUT_MAX
    ):
        delete_timeout = raw_delete_timeout
    raw_auth_timeout = obj.get("auth_timeout")
    if (
        isinstance(raw_auth_timeout, int)
        and not isinstance(raw_auth_timeout, bool)
        and TIMEOUT_MIN <= raw_auth_timeout <= TIMEOUT_MAX
    ):
        auth_timeout = raw_auth_timeout
    return (
        keep,
        failed_only,
        fetch_limit,
        max_deletions,
        last_repo,
        concurrency,
        list_timeout,
        delete_timeout,
        auth_timeout,
    )


def save_preferences(
    keep: int,
    failed_only: bool,
    fetch_limit: int = FETCH_LIMIT_DEFAULT,
    max_deletions: int = MAX_DELETIONS_MIN,
    last_repo: str = "",
    concurrency: int = CONCURRENCY_DEFAULT,
    list_timeout: int = TIMEOUT_LIST_DEFAULT,
    delete_timeout: int = TIMEOUT_DELETE_DEFAULT,
    auth_timeout: int = TIMEOUT_AUTH_DEFAULT,
) -> bool:
    """Persist preferences (never dry-run — see AGENTS.md §4.3)."""
    try:
        with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "keep": keep,
                    "failed_only": failed_only,
                    "fetch_limit": fetch_limit,
                    "max_deletions": max_deletions,
                    "last_repo": last_repo,
                    "concurrency": concurrency,
                    "list_timeout": list_timeout,
                    "delete_timeout": delete_timeout,
                    "auth_timeout": auth_timeout,
                },
                f,
                indent=2,
            )
        return True
    except OSError:
        return False


def delete_run(
    repo: str, run_id: str, timeout: int = GH_DELETE_TIMEOUT
) -> tuple[bool, str]:
    """Delete a single workflow run. Returns ``(success, error detail)``."""
    try:
        result = subprocess.run(
            ["gh", "run", "delete", run_id, "--repo", repo],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as e:
        return False, str(e)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if not detail:
            detail = f"exit code {result.returncode}"
        return False, detail
    return True, ""


def _delete_one(repo: str, run_id: str, timeout: int) -> tuple[bool, str]:
    """Module-level helper for ThreadPoolExecutor.map (avoids lambda typing)."""
    return delete_run(repo, run_id, timeout)


def check_gh_auth(timeout: int = GH_AUTH_TIMEOUT) -> str:
    """Preflight `gh`; return an error message or "" when ready to go."""
    if shutil.which("gh") is None:
        return (
            "the `gh` CLI was not found on PATH (install from https://cli.github.com/)"
        )
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "`gh auth status` timed out"
    except OSError as e:
        return f"could not run `gh`: {e}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return f"`gh` is not authenticated — run `gh auth login`. {detail}".strip()
    return ""


class CleanupWorker(QThread):
    """Runs all `gh` CLI work off the GUI thread (AGENTS.md §4.1)."""

    log_signal = Signal(str)
    max_signal = Signal(int)
    progress_signal = Signal(int)
    finished_signal = Signal(int)  # 0 success, 1 failure, 2 cancelled
    summary_signal = Signal(int, int, int)  # kept, deleted/targeted, failed

    def __init__(
        self,
        repo: str,
        keep: int,
        dry_run: bool,
        failed_only: bool = False,
        fetch_limit: int = FETCH_LIMIT_DEFAULT,
        max_deletions: int = MAX_DELETIONS_MIN,
        workflow: str = "",
        concurrency: int = CONCURRENCY_DEFAULT,
        list_timeout: int = TIMEOUT_LIST_DEFAULT,
        delete_timeout: int = TIMEOUT_DELETE_DEFAULT,
        auth_timeout: int = TIMEOUT_AUTH_DEFAULT,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.keep = keep
        self.dry_run = dry_run
        self.failed_only = failed_only
        self.fetch_limit = max(FETCH_LIMIT_MIN, min(fetch_limit, FETCH_LIMIT_MAX))
        # 0 (or negative) means "no cap".
        self.max_deletions = max(0, max_deletions)
        # Empty string means "all workflows"; non-empty narrows the fetch.
        self.workflow = workflow.strip()
        # 1 == sequential; >1 spawns a pool of that size for parallel deletes.
        self.concurrency = max(CONCURRENCY_MIN, min(concurrency, CONCURRENCY_MAX))
        # Per-`gh`-command timeouts in seconds (clamped to TIMEOUT_MIN..MAX).
        self.list_timeout = max(TIMEOUT_MIN, min(list_timeout, TIMEOUT_MAX))
        self.delete_timeout = max(TIMEOUT_MIN, min(delete_timeout, TIMEOUT_MAX))
        self.auth_timeout = max(TIMEOUT_MIN, min(auth_timeout, TIMEOUT_MAX))
        self._cancelled = False
        self._pause_gate = threading.Event()
        self._pause_gate.set()  # set == running, cleared == paused
        self._paused = False

    def cancel(self) -> None:
        """Request a stop; releases the gate so a paused run() wakes up."""
        self._cancelled = True
        self._pause_gate.set()

    def set_paused(self, paused: bool) -> None:
        """Pause (True) or resume (False) the worker between `gh` calls."""
        self._paused = paused
        if paused:
            self._pause_gate.clear()
        else:
            self._pause_gate.set()

    @property
    def paused(self) -> bool:
        """Whether the worker is currently paused."""
        return self._paused

    def _wait_if_paused(self) -> bool:
        """Block while paused; return False when cancelled during the wait."""
        while not self._pause_gate.wait(timeout=0.1):
            if self._cancelled:
                return False
        return not self._cancelled

    def run(self) -> None:
        try:
            self.log_signal.emit("Checking gh CLI authentication...")
            auth_error = check_gh_auth(self.auth_timeout)
            if auth_error:
                self.log_signal.emit(f"Error: {auth_error}")
                self.finished_signal.emit(1)
                return
            self.log_signal.emit(f"Fetching workflow runs for {self.repo}...")
            if self.workflow:
                self.log_signal.emit(f"  (filtered to workflow: {self.workflow})")
            runs = list_runs(
                self.repo, self.fetch_limit, self.workflow, self.list_timeout
            )
            if self.failed_only:
                runs = filter_failed_runs(runs)
                self.log_signal.emit(
                    "Failed-only mode: considering failure/cancelled/timed-out runs."
                )
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or str(e)).strip()
            self.log_signal.emit(f"Error fetching runs: {detail}")
            self.finished_signal.emit(1)
            return
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as e:
            self.log_signal.emit(f"Error fetching runs: {e}")
            self.finished_signal.emit(1)
            return

        if not runs:
            self.log_signal.emit("No matching workflow runs found.")
            self.summary_signal.emit(0, 0, 0)
            self.finished_signal.emit(0)
            return

        delete_ids, kept_ids = select_runs_to_delete(runs, self.keep)
        kept = len(kept_ids)
        total = len(delete_ids)

        # Optional per-run deletion cap (0 == unlimited).
        if self.max_deletions and total > self.max_deletions:
            self.log_signal.emit(
                f"Max deletions cap of {self.max_deletions} reached; "
                f"processing the first {self.max_deletions} of {total} run(s)."
            )
            delete_ids = delete_ids[: self.max_deletions]
            total = len(delete_ids)

        if total == 0:
            self.log_signal.emit(
                f"Nothing to delete — all {kept} run(s) belong to the "
                f"latest {self.keep} commit(s)."
            )
        else:
            action = "Would delete" if self.dry_run else "Deleting"
            self.log_signal.emit(
                f"{action} {total} run(s), keeping {kept} from the "
                f"latest {self.keep} commit(s)."
            )

        self.max_signal.emit(max(total, 1))
        done = 0
        failed = 0
        failed_ids: list[str] = []
        if self.dry_run:
            # Dry run is just logging — keep it sequential so the output
            # reads top-to-bottom and the pause gate is checked per item.
            for run_id in delete_ids:
                if not self._wait_if_paused():
                    break
                done += 1
                self.log_signal.emit(f"  DRY-RUN: would delete run {run_id}")
                if done % DRY_RUN_PROGRESS_STEP == 0 or done == total:
                    self.progress_signal.emit(done)
        elif self.concurrency <= 1 or total <= 1:
            # Sequential path: check the pause gate between each delete so a
            # pause/cancel is observed promptly rather than after the batch.
            for run_id in delete_ids:
                if not self._wait_if_paused():
                    break
                ok, detail = delete_run(self.repo, run_id, self.delete_timeout)
                done += 1
                if ok:
                    self.log_signal.emit(f"  Deleted run {run_id}")
                else:
                    failed += 1
                    failed_ids.append(run_id)
                    self.log_signal.emit(f"  Failed to delete run {run_id}: {detail}")
                self.progress_signal.emit(done)
        else:
            # Parallel path: split delete_ids into batches of `concurrency`,
            # submit each batch to a ThreadPoolExecutor, then check the pause
            # gate between batches. Results are collected as (run_id, ok,
            # detail) tuples; failed IDs are reported by name at the end.
            from concurrent.futures import ThreadPoolExecutor

            batch_size = self.concurrency
            for start in range(0, total, batch_size):
                if self._cancelled:
                    break
                if not self._wait_if_paused():
                    break
                batch = delete_ids[start : start + batch_size]
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    results = list(
                        executor.map(
                            _delete_one,
                            [self.repo] * len(batch),
                            batch,
                            [self.delete_timeout] * len(batch),
                        )
                    )
                for run_id, (ok, detail) in zip(batch, results):
                    done += 1
                    if ok:
                        self.log_signal.emit(f"  Deleted run {run_id}")
                    else:
                        failed += 1
                        failed_ids.append(run_id)
                        self.log_signal.emit(
                            f"  Failed to delete run {run_id}: {detail}"
                        )
                self.progress_signal.emit(done)

        self.summary_signal.emit(kept, done - failed, failed)
        if self._cancelled:
            self.log_signal.emit("Cleanup cancelled.")
            self.finished_signal.emit(2)
            return
        if failed:
            self.log_signal.emit(
                f"Cleanup finished with {failed} failure(s): {', '.join(failed_ids)}"
            )
            self.finished_signal.emit(1)
            return
        self.log_signal.emit("Cleanup complete.")
        self.finished_signal.emit(0)


#: Directory holding the packaged icon assets (SVG source + rendered rasters).
ASSETS_DIR = Path(__file__).resolve().parent / "assets"


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


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"GitHub Actions Cleaner {VERSION}")
        self.setWindowIcon(load_app_icon())
        self.worker: CleanupWorker | None = None
        self._close_pending = False
        self._repos: list[str] = load_repos()
        self._run_started = 0.0
        self._last_dry_run = True
        self._last_summary = (0, 0, 0)
        self.setup_ui()
        (
            keep,
            failed_only,
            fetch_limit,
            max_deletions,
            last_repo,
            concurrency,
            list_timeout,
            delete_timeout,
            auth_timeout,
        ) = load_preferences()
        self.keep_spin.setValue(keep)
        self.failed_only_check.setChecked(failed_only)
        self.fetch_limit_spin.setValue(fetch_limit)
        self.max_deletions_spin.setValue(max_deletions)
        self.concurrency_spin.setValue(concurrency)
        self.list_timeout_spin.setValue(list_timeout)
        self.delete_timeout_spin.setValue(delete_timeout)
        self.auth_timeout_spin.setValue(auth_timeout)
        if last_repo:
            self.repo_input.setText(last_repo)
        self._refresh_cleanup_enabled()

    def setup_ui(self) -> None:
        form = QGroupBox("Settings")
        form_layout = QFormLayout()

        repo_row = QHBoxLayout()
        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText(
            "owner/repo or https://github.com/owner/repo"
        )
        self.repo_input.setToolTip(
            "GitHub repository — owner/repo slug or a GitHub URL"
        )
        self.repo_input.textChanged.connect(self._refresh_cleanup_enabled)
        self.add_repo_btn = QPushButton("Add")
        self.add_repo_btn.setToolTip("Save the repository above to the list")
        self.add_repo_btn.clicked.connect(self.add_repo)
        self.remove_repo_btn = QPushButton("Remove")
        self.remove_repo_btn.setToolTip("Remove the repository above from the list")
        self.remove_repo_btn.clicked.connect(self.remove_repo)
        repo_row.addWidget(self.repo_input)
        repo_row.addWidget(self.add_repo_btn)
        repo_row.addWidget(self.remove_repo_btn)
        form_layout.addRow("Repository:", repo_row)

        self.repo_combo = QComboBox()
        self.repo_combo.setEditable(False)
        self.repo_combo.setToolTip(
            "Saved repositories — selecting one loads it above for edit/remove"
        )
        self.load_repo_list()
        form_layout.addRow("Saved repositories:", self.repo_combo)
        self.repo_combo.currentTextChanged.connect(self.on_repo_selected)

        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(1, 100)
        self.keep_spin.setValue(2)
        self.keep_spin.setToolTip("How many newest commits' runs to preserve")
        form_layout.addRow("Commits to keep:", self.keep_spin)

        self.fetch_limit_spin = QSpinBox()
        self.fetch_limit_spin.setRange(FETCH_LIMIT_MIN, FETCH_LIMIT_MAX)
        self.fetch_limit_spin.setSingleStep(100)
        self.fetch_limit_spin.setValue(FETCH_LIMIT_DEFAULT)
        self.fetch_limit_spin.setToolTip(
            "How many most-recent runs to fetch (gh paginates automatically)"
        )
        form_layout.addRow("Runs to fetch:", self.fetch_limit_spin)

        self.max_deletions_spin = QSpinBox()
        self.max_deletions_spin.setRange(MAX_DELETIONS_MIN, MAX_DELETIONS_MAX)
        self.max_deletions_spin.setSingleStep(10)
        self.max_deletions_spin.setValue(MAX_DELETIONS_MIN)
        self.max_deletions_spin.setSpecialValueText("No limit")
        self.max_deletions_spin.setToolTip(
            "Stop after deleting this many runs in one pass (0 = no limit)"
        )
        form_layout.addRow("Max deletions:", self.max_deletions_spin)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(CONCURRENCY_MIN, CONCURRENCY_MAX)
        self.concurrency_spin.setValue(CONCURRENCY_DEFAULT)
        self.concurrency_spin.setToolTip(
            "Parallel `gh run delete` calls (1 = sequential; "
            "only used when there are >1 run to delete)"
        )
        form_layout.addRow("Deletion concurrency:", self.concurrency_spin)

        self.dry_run_check = QCheckBox("Dry run (don't actually delete)")
        self.dry_run_check.setChecked(True)
        form_layout.addRow("", self.dry_run_check)

        self.failed_only_check = QCheckBox("Failed / cancelled only")
        form_layout.addRow("", self.failed_only_check)

        self.workflow_input = QLineEdit()
        self.workflow_input.setPlaceholderText(
            "workflow name or ID (optional, leave blank for all)"
        )
        self.workflow_input.setToolTip(
            "Restrict cleanup to one workflow by name or ID "
            "(e.g. 'ci.yml' or '12345678-abcd-...')"
        )
        form_layout.addRow("Workflow filter:", self.workflow_input)

        form.setLayout(form_layout)

        # Timeouts live in a collapsible group so the default Settings
        # panel stays compact; power users can widen them if `gh` is slow.
        advanced = QGroupBox("Advanced (timeouts)")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QFormLayout()

        self.list_timeout_spin = QSpinBox()
        self.list_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.list_timeout_spin.setValue(TIMEOUT_LIST_DEFAULT)
        self.list_timeout_spin.setSingleStep(10)
        self.list_timeout_spin.setToolTip("Seconds before `gh run list` is cancelled")
        advanced_layout.addRow("List timeout (s):", self.list_timeout_spin)

        self.delete_timeout_spin = QSpinBox()
        self.delete_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.delete_timeout_spin.setValue(TIMEOUT_DELETE_DEFAULT)
        self.delete_timeout_spin.setSingleStep(10)
        self.delete_timeout_spin.setToolTip(
            "Seconds before each `gh run delete` is cancelled"
        )
        advanced_layout.addRow("Delete timeout (s):", self.delete_timeout_spin)

        self.auth_timeout_spin = QSpinBox()
        self.auth_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.auth_timeout_spin.setValue(TIMEOUT_AUTH_DEFAULT)
        self.auth_timeout_spin.setSingleStep(5)
        self.auth_timeout_spin.setToolTip(
            "Seconds before `gh auth status` is cancelled"
        )
        advanced_layout.addRow("Auth timeout (s):", self.auth_timeout_spin)

        advanced.setLayout(advanced_layout)

        self.cleanup_btn = QPushButton("Clean Up Actions")
        self.cleanup_btn.clicked.connect(self.start_cleanup)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_cleanup)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(LOG_MAX_BLOCKS)

        self.summary_label = QLabel()
        self.summary_label.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(form)
        layout.addWidget(advanced)
        button_row = QHBoxLayout()
        button_row.addWidget(self.cleanup_btn)
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.pause_btn)
        layout.addLayout(button_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)
        layout.addWidget(self.summary_label)
        self.setLayout(layout)

    def _refresh_cleanup_enabled(self) -> None:
        busy = self.worker is not None
        has_repo = bool(self.repo_input.text().strip())
        self.cleanup_btn.setEnabled(not busy and has_repo)

    def load_repo_list(self) -> None:
        """Repopulate the combo without firing selection signals."""
        self.repo_combo.blockSignals(True)
        try:
            self.repo_combo.clear()
            self.repo_combo.addItems(self._repos)
        finally:
            self.repo_combo.blockSignals(False)

    def on_repo_selected(self, text: str) -> None:
        if text:
            self.repo_input.setText(text)

    def add_repo(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if not is_valid_repo(repo):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository as owner/repo or a GitHub URL.",
            )
            return
        if repo in self._repos:
            QMessageBox.information(self, "Info", "Repository already exists.")
            return
        self._repos.append(repo)
        if not save_repos(self._repos):
            self._repos.pop()
            QMessageBox.critical(self, "Error", f"Could not write {SETTINGS_PATH}")
            return
        self.repo_input.setText(repo)  # show the canonical owner/repo form
        self.load_repo_list()
        self.repo_combo.setCurrentText(repo)

    def remove_repo(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if not repo:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter or select a repository to remove.",
            )
            return
        if repo not in self._repos:
            QMessageBox.information(self, "Info", "Repository not found in saved list.")
            return
        index = self._repos.index(repo)
        self._repos.remove(repo)
        if not save_repos(self._repos):
            self._repos.insert(index, repo)
            QMessageBox.critical(self, "Error", f"Could not write {SETTINGS_PATH}")
            return
        self.load_repo_list()
        self.repo_input.setText(self._repos[0] if self._repos else "")

    def start_cleanup(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if not is_valid_repo(repo):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository as owner/repo or a GitHub URL.",
            )
            return
        if self.repo_input.text().strip() != repo:
            self.repo_input.setText(repo)  # keep the canonical slug visible
        if shutil.which("gh") is None:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                "The `gh` CLI was not found on PATH.\n"
                "Install it from https://cli.github.com/ and run `gh auth login`.",
            )
            return
        if self.worker is not None:
            return

        dry_run = self.dry_run_check.isChecked()
        self._last_dry_run = dry_run
        self._run_started = time.monotonic()
        self._last_summary = (0, 0, 0)
        self.log_output.clear()
        self.summary_label.setVisible(False)
        if not save_preferences(
            self.keep_spin.value(),
            self.failed_only_check.isChecked(),
            self.fetch_limit_spin.value(),
            self.max_deletions_spin.value(),
            repo,
            self.concurrency_spin.value(),
            self.list_timeout_spin.value(),
            self.delete_timeout_spin.value(),
            self.auth_timeout_spin.value(),
        ):
            self.log_output.appendPlainText(
                f"Warning: could not write {PREFERENCES_PATH}"
            )

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.pause_btn.setVisible(True)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")

        self.worker = CleanupWorker(
            repo,
            self.keep_spin.value(),
            dry_run,
            self.failed_only_check.isChecked(),
            fetch_limit=self.fetch_limit_spin.value(),
            max_deletions=self.max_deletions_spin.value(),
            workflow=self.workflow_input.text(),
            concurrency=self.concurrency_spin.value(),
            list_timeout=self.list_timeout_spin.value(),
            delete_timeout=self.delete_timeout_spin.value(),
            auth_timeout=self.auth_timeout_spin.value(),
        )
        self.worker.log_signal.connect(self.log_output.appendPlainText)
        self.worker.max_signal.connect(self.progress_bar.setMaximum)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.summary_signal.connect(self.on_summary)
        self.worker.finished_signal.connect(self.on_cleanup_finished)
        self.worker.finished.connect(self.on_thread_finished)
        self.worker.start()
        self._refresh_cleanup_enabled()

    def cancel_cleanup(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.log_output.appendPlainText(
                "Cancelling — waiting for the current operation to finish..."
            )

    def toggle_pause(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        pausing = not self.worker.paused
        self.worker.set_paused(pausing)
        self.pause_btn.setText("Resume" if pausing else "Pause")
        self.log_output.appendPlainText(
            "Paused — click Resume to continue." if pausing else "Resuming..."
        )

    def on_summary(self, kept: int, deleted: int, failed: int) -> None:
        self._last_summary = (kept, deleted, failed)

    def on_cleanup_finished(self, result: int) -> None:
        """Reset UI state and report the outcome (0 ok, 1 fail, 2 cancel)."""
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)
        self.pause_btn.setVisible(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.progress_bar.setVisible(False)
        self._refresh_cleanup_enabled()

        kept, deleted, failed = self._last_summary
        elapsed = time.monotonic() - self._run_started if self._run_started else 0.0
        if self._last_dry_run:
            state = "Dry run"
        elif result == 2:
            state = "Cancelled"
        elif result != 0:
            state = "Failed"
        else:
            state = "Done"
        self.summary_label.setText(
            f"{state} · kept {kept} · deleted {deleted} · "
            f"failed {failed} · {elapsed:.1f}s"
        )
        self.summary_label.setVisible(True)

        if self._close_pending:
            return  # window is closing — don't stack dialogs on the way out
        if result == 0:
            if self._last_dry_run:
                message = (
                    f"Dry run complete: would delete {deleted} run(s), "
                    f"keeping {kept} ({elapsed:.1f}s)."
                )
            else:
                message = (
                    f"Cleanup completed: deleted {deleted} run(s), "
                    f"keeping {kept} ({elapsed:.1f}s)."
                )
            QMessageBox.information(self, "Success", message)
        elif result == 2:
            QMessageBox.information(
                self,
                "Cancelled",
                f"Cleanup cancelled after {elapsed:.1f}s ({deleted} run(s) processed).",
            )
        else:
            QMessageBox.critical(
                self,
                "Error",
                "Cleanup failed. Check the log for details.\n"
                f"({deleted} deleted, {failed} failed, {kept} kept)",
            )

    def on_thread_finished(self) -> None:
        """Worker thread fully stopped — release it and honour pending close."""
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_cleanup_enabled()
        if self._close_pending:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            # Never destroy a running QThread; cancel and close once it ends.
            self._close_pending = True
            self.worker.cancel()
            self.log_output.appendPlainText(
                "Closing — waiting for the running cleanup to stop..."
            )
            event.ignore()
            return
        event.accept()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="actions-cleaner",
        description=(
            "Clean up GitHub Actions workflow runs by keeping only the "
            "latest N commits' runs."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"actions-cleaner {VERSION}",
    )
    return parser


def main() -> int:
    # The standalone build may run with --windows-console-mode=disable,
    # where stdio handles don't exist; fall back to devnull so --help and
    # --version still exit cleanly instead of raising.
    if sys.stdout is None:
        sys.stdout = open(  # noqa: SIM115 — must outlive main()
            os.devnull, "w", encoding="utf-8"
        )
    if sys.stderr is None:
        sys.stderr = open(  # noqa: SIM115 — must outlive main()
            os.devnull, "w", encoding="utf-8"
        )
    try:
        make_parser().parse_args()
    except OSError:
        return 0

    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(640, 520)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
