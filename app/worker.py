"""Background worker for GitHub Actions cleanup operations."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from .gh_retry import get_friendly_error_message, run_gh_with_retry
from .selection import (
    ALL_CONCLUSIONS,
    filter_failed_runs,
    filter_runs_by_conclusions,
    list_runs,
    select_runs_to_delete,
)
from .settings import (
    CHECKPOINT_PATH,
    CONCURRENCY_DEFAULT,
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    FETCH_LIMIT_DEFAULT,
    FETCH_LIMIT_MAX,
    FETCH_LIMIT_MIN,
    MAX_DELETIONS_MIN,
    TIMEOUT_AUTH_DEFAULT,
    TIMEOUT_DELETE_DEFAULT,
    TIMEOUT_LIST_DEFAULT,
    TIMEOUT_MAX,
    TIMEOUT_MIN,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)

#: Bounds for the user-configurable per-run deletion cap (0 = unlimited).
MAX_DELETIONS_MAX = 5000

#: Minimum required gh CLI version (major.minor)
GH_MIN_VERSION = (2, 0)

GH_LIST_TIMEOUT = 60
GH_DELETE_TIMEOUT = 30
GH_AUTH_TIMEOUT = 15

DRY_RUN_PROGRESS_STEP = 25

#: Cache for gh auth status per session
_gh_auth_cache: dict[str, str] = {}


def clear_gh_auth_cache() -> None:
    """Clear the gh auth status cache (useful for testing)."""
    _gh_auth_cache.clear()


def _find_gh_on_path() -> str | None:
    """Find gh CLI executable, checking common install locations if not on PATH."""
    # First try PATH
    gh_path = shutil.which("gh")
    if gh_path:
        return gh_path

    # Common install locations
    candidates: list[Path] = []

    if sys.platform == "win32":
        # Windows: GitHub CLI installer locations
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(Path(local_app_data) / "GitHub CLI" / "gh.exe")
        program_files = os.environ.get("PROGRAMFILES", "")
        if program_files:
            candidates.append(Path(program_files) / "GitHub CLI" / "gh.exe")
        # Scoop/Chocolatey
        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            candidates.append(
                Path(user_profile)
                / "scoop"
                / "apps"
                / "gh"
                / "current"
                / "bin"
                / "gh.exe"
            )
            candidates.append(Path(user_profile) / ".chocolatey" / "bin" / "gh.exe")
    else:
        # macOS/Linux: Homebrew, apt, etc.
        candidates.extend(
            [
                Path("/usr/local/bin/gh"),
                Path("/opt/homebrew/bin/gh"),
                Path("/usr/bin/gh"),
                Path.home() / ".local" / "bin" / "gh",
            ]
        )

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def get_gh_version() -> tuple[int, int] | None:
    """Return parsed (major, minor) version of gh CLI, or None if unavailable."""
    gh_path = _find_gh_on_path()
    if gh_path is None:
        return None
    try:
        result = subprocess.run(
            [gh_path, "version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired, OSError:
        return None
    if result.returncode != 0:
        return None
    # Output format: "gh version 2.45.0 (2024-01-15)..." or similar
    match = re.search(r"gh version\s+(\d+)\.(\d+)\.(\d+)", result.stdout)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


def check_gh_version() -> str:
    """Check gh version meets minimum requirement; return warning message or empty string."""
    version = get_gh_version()
    if version is None:
        return "could not determine `gh` version"
    if version < GH_MIN_VERSION:
        return f"`gh` version {version[0]}.{version[1]} is older than minimum {GH_MIN_VERSION[0]}.{GH_MIN_VERSION[1]}; some features may not work"
    return ""


def check_gh_auth(timeout: int = GH_AUTH_TIMEOUT) -> str:
    """Preflight `gh`; return an error message or "" when ready to go.

    Uses a simple session cache to avoid repeated auth checks.
    """
    # Check cache first
    cache_key = f"auth_{timeout}"
    if cache_key in _gh_auth_cache:
        return _gh_auth_cache[cache_key]

    gh_path = _find_gh_on_path()
    if gh_path is None:
        error = (
            "the `gh` CLI was not found on PATH (install from https://cli.github.com/)"
        )
        _gh_auth_cache[cache_key] = error
        return error

    try:
        result = subprocess.run(
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        error = "`gh auth status` timed out"
        _gh_auth_cache[cache_key] = error
        return error
    except OSError as e:
        error = f"could not run `gh`: {e}"
        _gh_auth_cache[cache_key] = error
        return error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        error = f"`gh` is not authenticated — run `gh auth login`. {detail}".strip()
        _gh_auth_cache[cache_key] = error
        return error
    _gh_auth_cache[cache_key] = ""
    return ""


def delete_run(
    repo: str,
    run_id: str,
    timeout: int = GH_DELETE_TIMEOUT,
    log_signal: object = None,
) -> tuple[bool, str]:
    """Delete a single workflow run. Returns ``(success, error detail)``."""
    from PySide6.QtCore import Signal

    cmd = ["gh", "run", "delete", run_id, "--repo", repo]
    result = run_gh_with_retry(cmd, timeout, log_signal or Signal(str))
    if result is None:
        return False, "failed after retries or cancelled"
    if result.returncode != 0:
        detail = get_friendly_error_message(result.stderr or "", result.stdout or "")
        if not detail:
            detail = f"exit code {result.returncode}"
        return False, detail
    return True, ""


def delete_runs_batch(
    repo: str,
    run_ids: list[str],
    timeout: int = GH_DELETE_TIMEOUT,
    log_signal: object = None,
) -> list[tuple[str, bool, str]]:
    """Delete multiple workflow runs in a single `gh run delete` call.

    Returns list of ``(run_id, success, error_detail)`` tuples.
    """
    if not run_ids:
        return []
    from PySide6.QtCore import Signal

    cmd = ["gh", "run", "delete", "--repo", repo] + run_ids
    result = run_gh_with_retry(cmd, timeout, log_signal or Signal(str))
    if result is None:
        return [
            (run_id, False, "failed after retries or cancelled") for run_id in run_ids
        ]
    if result.returncode != 0:
        detail = get_friendly_error_message(result.stderr or "", result.stdout or "")
        if not detail:
            detail = f"exit code {result.returncode}"
        return [(run_id, False, detail) for run_id in run_ids]

    return [(run_id, True, "") for run_id in run_ids]


class CleanupWorker(QThread):
    """Runs all `gh` CLI work off the GUI thread."""

    log_signal = Signal(str)
    max_signal = Signal(int)
    progress_signal = Signal(int)
    finished_signal = Signal(int)  # 0 success, 1 failure, 2 cancelled
    summary_signal = Signal(int, int, int)  # kept, deleted/targeted, failed
    completion_signal = Signal(int, int, int, int)  # result, kept, deleted, failed

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
        older_than_days: int = 0,
        conclusions: set[str] | None = None,
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
        # Date range filter: 0 = disabled, >0 = only consider runs older than N days
        self.older_than_days = max(0, older_than_days)
        # Conclusion filter: None = all, empty set = no filter, otherwise filter by conclusions
        self.conclusions = conclusions if conclusions is not None else ALL_CONCLUSIONS
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
                self.completion_signal.emit(1, 0, 0, 0)
                return
            # Check gh version
            version_warning = check_gh_version()
            if version_warning:
                self.log_signal.emit(f"Warning: {version_warning}")
            self.log_signal.emit(f"Fetching workflow runs for {self.repo}...")
            if self.workflow:
                self.log_signal.emit(f"  (filtered to workflow: {self.workflow})")
            runs = list_runs(
                self.repo,
                self.fetch_limit,
                self.workflow,
                self.list_timeout,
                self.log_signal,
            )
            if self.failed_only:
                runs = filter_failed_runs(runs)
                self.log_signal.emit(
                    "Failed-only mode: considering failure/cancelled/timed-out runs."
                )
            elif self.conclusions != ALL_CONCLUSIONS:
                runs = filter_runs_by_conclusions(runs, self.conclusions)
                if self.conclusions:
                    self.log_signal.emit(
                        f"Filtering by conclusions: {', '.join(sorted(self.conclusions))}"
                    )
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or str(e)).strip()
            self.log_signal.emit(f"Error fetching runs: {detail}")
            self.completion_signal.emit(1, 0, 0, 0)
            return
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as e:
            self.log_signal.emit(f"Error fetching runs: {e}")
            self.completion_signal.emit(1, 0, 0, 0)
            return

        if not runs:
            self.log_signal.emit("No matching workflow runs found.")
            self.summary_signal.emit(0, 0, 0)
            self.completion_signal.emit(0, 0, 0, 0)
            return

        delete_ids, kept_ids = select_runs_to_delete(
            runs, self.keep, self.older_than_days
        )
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
                ok, detail = delete_run(
                    self.repo, run_id, self.delete_timeout, self.log_signal
                )
                done += 1
                if ok:
                    self.log_signal.emit(f"  Deleted run {run_id}")
                else:
                    failed += 1
                    failed_ids.append(run_id)
                    self.log_signal.emit(f"  Failed to delete run {run_id}: {detail}")
                self.progress_signal.emit(done)
        else:
            # Batch deletion path: group delete_ids into batches of `concurrency`
            # and delete each batch in a single `gh run delete` call.
            # Check the pause gate between batches.
            batch_size = self.concurrency
            for start in range(0, total, batch_size):
                if self._cancelled:
                    break
                if not self._wait_if_paused():
                    break
                batch = delete_ids[start : start + batch_size]
                results = delete_runs_batch(
                    self.repo, batch, self.delete_timeout, self.log_signal
                )
                for run_id, ok, detail in results:
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

        # Emit a single combined signal so the GUI never races on `_last_summary`.
        deleted_count = done - failed
        self.summary_signal.emit(kept, deleted_count, failed)
        if self._cancelled:
            self.log_signal.emit("Cleanup cancelled.")
            self.completion_signal.emit(2, kept, deleted_count, failed)
            return
        if failed:
            self.log_signal.emit(
                f"Cleanup finished with {failed} failure(s): {', '.join(failed_ids)}"
            )
            self.completion_signal.emit(1, kept, deleted_count, failed)
            return
        self.log_signal.emit("Cleanup complete.")
        self.completion_signal.emit(0, kept, deleted_count, failed)
