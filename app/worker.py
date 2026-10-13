"""Background worker for GitHub Actions cleanup operations."""

from __future__ import annotations

import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, QThread, Signal

from .selection import (
    filter_failed_runs,
    list_runs,
    select_runs_to_delete,
)
from .settings import (
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
)

#: Bounds for the user-configurable per-run deletion cap (0 = unlimited).
MAX_DELETIONS_MAX = 5000


GH_LIST_TIMEOUT = 60
GH_DELETE_TIMEOUT = 30
GH_AUTH_TIMEOUT = 15

DRY_RUN_PROGRESS_STEP = 25


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


def delete_runs_batch(
    repo: str, run_ids: list[str], timeout: int = GH_DELETE_TIMEOUT
) -> list[tuple[str, bool, str]]:
    """Delete multiple workflow runs in a single `gh run delete` call.

    Returns list of ``(run_id, success, error_detail)`` tuples.
    """
    if not run_ids:
        return []
    try:
        cmd = ["gh", "run", "delete", "--repo", repo] + run_ids
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [(run_id, False, "timed out") for run_id in run_ids]
    except OSError as e:
        return [(run_id, False, str(e)) for run_id in run_ids]

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
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
                results = delete_runs_batch(self.repo, batch, self.delete_timeout)
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
