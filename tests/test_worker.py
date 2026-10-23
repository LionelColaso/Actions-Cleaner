"""Tests for CleanupWorker.run() with the gh CLI mocked out."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest

from app.selection import RunInfo
from app.settings import (
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    TIMEOUT_DELETE_DEFAULT,
    TIMEOUT_LIST_DEFAULT,
    TIMEOUT_MAX,
    TIMEOUT_MIN,
)
from app.worker import (
    CleanupWorker,
    check_gh_auth,
    check_gh_version,
    clear_gh_auth_cache,
    delete_run,
    delete_runs_batch,
    get_gh_version,
)

MakeRun = Callable[..., RunInfo]


class WorkerResult:
    """Captures everything a worker emits during a synchronous run()."""

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.finished: list[int] = []
        self.completions: list[tuple[int, int, int, int]] = []
        self.summaries: list[tuple[int, int, int]] = []
        self.maxes: list[int] = []
        self.progress: list[int] = []

    def record_summary(self, kept: int, deleted: int, failed: int) -> None:
        self.summaries.append((kept, deleted, failed))

    def record_completion(
        self, result: int, kept: int, deleted: int, failed: int
    ) -> None:
        self.completions.append((result, kept, deleted, failed))


class _GhMock:
    """Configurable stand-in for the module's gh helper functions."""

    def __init__(self) -> None:
        self.runs: list[RunInfo] = []
        self.auth_error = ""
        self.list_error: BaseException | None = None
        self.delete_error: BaseException | None = None
        self.delete_ok = True
        self.delete_detail = ""
        self.delete_calls: list[str] = []
        self.batch_delete_calls: list[list[str]] = []
        self.list_fetch_limit = 0
        self.list_workflow = ""
        self.list_timeout_seen = 0
        self.delete_timeout_seen = 0
        self.auth_timeout_seen = 0
        self.batch_delete_results: list[tuple[str, bool, str]] | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_auth(timeout: int = 30) -> str:
            self.auth_timeout_seen = timeout
            return self.auth_error

        def fake_list(
            repo: str,
            fetch_limit: int = 0,
            workflow: str = "",
            timeout: int = TIMEOUT_LIST_DEFAULT,
            log_signal: object = None,
        ) -> list[RunInfo]:
            self.list_timeout_seen = timeout
            if self.list_error is not None:
                raise self.list_error
            self.list_fetch_limit = fetch_limit
            self.list_workflow = workflow
            return list(self.runs)

        def fake_delete(
            repo: str,
            run_id: str,
            timeout: int = TIMEOUT_DELETE_DEFAULT,
            log_signal: object = None,
        ) -> tuple[bool, str]:
            self.delete_timeout_seen = timeout
            if self.delete_error is not None:
                raise self.delete_error
            self.delete_calls.append(run_id)
            return self.delete_ok, self.delete_detail

        def fake_batch_delete(
            repo: str,
            run_ids: list[str],
            timeout: int = TIMEOUT_DELETE_DEFAULT,
            log_signal: object = None,
        ) -> list[tuple[str, bool, str]]:
            self.delete_timeout_seen = timeout
            self.batch_delete_calls.append(list(run_ids))
            self.delete_calls.extend(run_ids)
            if self.batch_delete_results is not None:
                return self.batch_delete_results
            if self.delete_error is not None:
                raise self.delete_error
            return [(run_id, self.delete_ok, self.delete_detail) for run_id in run_ids]

        monkeypatch.setattr("app.worker.check_gh_auth", fake_auth)
        monkeypatch.setattr("app.worker.list_runs", fake_list)
        monkeypatch.setattr("app.worker.delete_run", fake_delete)
        monkeypatch.setattr("app.worker.delete_runs_batch", fake_batch_delete)


def _run_worker(worker: CleanupWorker) -> WorkerResult:
    result = WorkerResult()
    worker.log_signal.connect(result.logs.append)
    worker.finished_signal.connect(result.finished.append)
    worker.completion_signal.connect(result.record_completion)
    worker.summary_signal.connect(result.record_summary)
    worker.max_signal.connect(result.maxes.append)
    worker.progress_signal.connect(result.progress.append)
    worker.run()  # synchronous; same-thread signal connections are direct
    return result


def _run_with_release(
    worker: CleanupWorker, release: Callable[[], object]
) -> WorkerResult:
    """Run the worker, firing `release` on a timer so a paused run() wakes."""
    timer = threading.Timer(0.2, release)
    timer.start()
    try:
        return _run_worker(worker)
    finally:
        timer.join()


def _sample_runs(make_run: MakeRun) -> list[RunInfo]:
    return [
        make_run(1, "new", "2024-06-01T00:00:00Z"),
        make_run(2, "new", "2024-06-01T01:00:00Z"),
        make_run(3, "old", "2024-05-01T00:00:00Z"),
    ]


def _four_run_sample(make_run: MakeRun) -> list[RunInfo]:
    """Three old commits + one new — used by max-deletions and concurrency tests."""
    return [
        make_run(1, "old-a", "2024-05-01T00:00:00Z"),
        make_run(2, "old-b", "2024-05-02T00:00:00Z"),
        make_run(3, "old-c", "2024-05-03T00:00:00Z"),
        make_run(4, "new", "2024-06-01T00:00:00Z"),
    ]


def _install_worker(
    monkeypatch: pytest.MonkeyPatch,
    make_run: MakeRun,
    runs: list[RunInfo],
    keep: int = 1,
    dry_run: bool = True,
    fetch_limit: int = 1000,
    max_deletions: int = 0,
    workflow: str = "",
    concurrency: int = 1,
    list_timeout: int = 60,
    delete_timeout: int = 30,
    auth_timeout: int = 15,
    older_than_days: int = 0,
) -> tuple[_GhMock, CleanupWorker, WorkerResult]:
    """Install a fresh mock with `runs`, build a worker, run it, return all three."""
    gh = _GhMock()
    gh.runs = runs
    gh.install(monkeypatch)
    worker = CleanupWorker(
        "owner/repo",
        keep=keep,
        dry_run=dry_run,
        fetch_limit=fetch_limit,
        max_deletions=max_deletions,
        workflow=workflow,
        concurrency=concurrency,
        list_timeout=list_timeout,
        delete_timeout=delete_timeout,
        auth_timeout=auth_timeout,
        older_than_days=older_than_days,
    )
    result = _run_worker(worker)
    return gh, worker, result


def test_dry_run_never_deletes(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert gh.delete_calls == []
    assert result.completions == [(0, 2, 1, 0)]
    assert result.summaries == [(2, 1, 0)]
    assert result.maxes == [1]
    assert result.progress == [1]
    assert any("DRY-RUN" in line for line in result.logs)


def test_real_run_deletes_and_reports_success(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False)
    result = _run_worker(worker)

    assert gh.delete_calls == ["3"]
    assert result.completions == [(0, 2, 1, 0)]
    assert result.summaries == [(2, 1, 0)]
    assert any(
        'href="https://github.com/owner/repo/actions/runs/3"' in line
        for line in result.logs
    )


def test_failed_deletions_produce_failure_code(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.delete_ok = False
    gh.delete_detail = "boom"
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False)
    result = _run_worker(worker)

    assert result.completions == [(1, 2, 0, 1)]  # never report success with failures
    assert result.summaries == [(2, 0, 1)]
    assert any(
        'href="https://github.com/owner/repo/actions/runs/3"' in line
        for line in result.logs
    )
    gh = _GhMock()
    gh.list_error = FileNotFoundError("gh")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.completions == [(1, 0, 0, 0)]
    assert any("Error fetching runs" in line for line in result.logs)


def test_called_process_error_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.list_error = subprocess.CalledProcessError(128, ["gh"], stderr="boom")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.completions == [(1, 0, 0, 0)]
    assert any("boom" in line for line in result.logs)


def test_unexpected_output_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.list_error = TypeError("unexpected gh output")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.completions == [(1, 0, 0, 0)]


def test_auth_failure_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.auth_error = "`gh` is not authenticated"
    gh.list_error = AssertionError("list_runs must not be called")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.completions == [(1, 0, 0, 0)]
    assert any("not authenticated" in line for line in result.logs)


def test_no_runs_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.completions == [(0, 0, 0, 0)]
    assert result.summaries == [(0, 0, 0)]
    assert any("No matching" in line for line in result.logs)


def test_failed_only_considers_only_failed_runs(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = [
        make_run(1, "new", "2024-06-01T00:00:00Z", "success"),
        make_run(3, "old", "2024-05-01T00:00:00Z", "failure"),
    ]
    gh.delete_error = AssertionError("no run should be deleted")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False, failed_only=True)
    result = _run_worker(worker)

    # Only the failed run remains, and it belongs to the single kept commit.
    assert result.completions == [(0, 1, 0, 0)]
    assert result.summaries == [(1, 0, 0)]
    assert any("Failed-only" in line for line in result.logs)


def test_cancel_stops_before_deleting(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.delete_error = AssertionError("cancelled worker must not delete")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.cancel()
    result = _run_worker(worker)

    assert result.completions == [(2, 2, 0, 0)]
    assert any("cancelled" in line.lower() for line in result.logs)


def test_fetch_limit_is_passed_to_list_runs(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, fetch_limit=250)
    _run_worker(worker)

    assert gh.list_fetch_limit == 250


def test_max_deletions_caps_processed_runs(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh, _worker, result = _install_worker(
        monkeypatch,
        make_run,
        _four_run_sample(make_run),
        keep=1,
        dry_run=False,
        max_deletions=2,
    )

    # keep=1 protects the "new" commit; 3 old runs would be deleted, but the
    # cap stops after the first 2.
    assert len(gh.delete_calls) == 2
    assert result.completions == [(0, 1, 2, 0)]
    assert result.maxes == [2]
    assert any("Max deletions cap of 2" in line for line in result.logs)


def test_max_deletions_zero_means_unlimited(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh, _worker, result = _install_worker(
        monkeypatch,
        make_run,
        _sample_runs(make_run),
        keep=1,
        dry_run=False,
        max_deletions=0,
    )

    assert len(gh.delete_calls) == 1  # only run 3 is deletable here
    assert result.completions == [(0, 2, 1, 0)]


def test_concurrency_one_is_sequential(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh, _worker, result = _install_worker(
        monkeypatch,
        make_run,
        _sample_runs(make_run),
        keep=1,
        dry_run=False,
        concurrency=1,
    )

    assert gh.delete_calls == ["3"]
    assert result.completions == [(0, 2, 1, 0)]


def test_concurrency_parallel_deletes_all(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """A pool of workers still deletes every targeted run; failed IDs are
    reported by name in the log instead of just a count."""
    gh = _GhMock()
    gh.runs = _four_run_sample(make_run)
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False, concurrency=4)
    result = _run_worker(worker)

    # With batch delete, all runs are deleted in one batch call
    assert set(gh.batch_delete_calls[0]) == {"1", "2", "3"}
    assert result.completions == [(0, 1, 3, 0)]


def test_concurrency_reports_failed_ids_by_name(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """A parallel run still reports failed IDs by name in the log."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "old-a", "2024-05-01T00:00:00Z"),
        make_run(2, "old-b", "2024-05-02T00:00:00Z"),
        make_run(3, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.install(monkeypatch)

    # Override delete_runs_batch to make run "1" fail
    original_batch_delete = "app.worker.delete_runs_batch"

    def flaky_batch_delete(
        repo: str,
        run_ids: list[str],
        timeout: int = TIMEOUT_DELETE_DEFAULT,
        log_signal: object = None,
    ) -> list[tuple[str, bool, str]]:
        results = []
        for run_id in run_ids:
            if run_id == "1":
                results.append((run_id, False, "boom"))
            else:
                results.append((run_id, True, ""))
        return results

    monkeypatch.setattr(original_batch_delete, flaky_batch_delete)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False, concurrency=3)
    result = _run_worker(worker)

    assert result.completions == [(1, 1, 1, 1)]
    assert any(
        'href="https://github.com/owner/repo/actions/runs/1"' in line
        for line in result.logs
    )
    assert any(
        line.startswith("Cleanup finished with 1 failure(s):") and "1" in line
        for line in result.logs
    )


def test_concurrency_clamped_to_bounds() -> None:
    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, concurrency=0)
    assert worker.concurrency == CONCURRENCY_MIN

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, concurrency=99)
    assert worker.concurrency == CONCURRENCY_MAX


def test_pause_blocks_until_resumed(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = [
        make_run(10, "old", "2024-05-01T00:00:00Z"),
        make_run(11, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.set_paused(True)
    assert worker.paused is True

    # Released on a timer: proves run() blocked on the gate, not ignored it.
    result = _run_with_release(worker, lambda: worker.set_paused(False))

    assert result.completions == [(0, 1, 1, 0)]
    assert gh.delete_calls == ["10"]


def test_cancel_releases_a_paused_worker(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = [
        make_run(20, "old", "2024-05-01T00:00:00Z"),
        make_run(21, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.delete_error = AssertionError("paused-then-cancelled run must not delete")
    gh.install(monkeypatch)

    worker = CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.set_paused(True)

    # A pause + cancel must not deadlock; run() exits as cancelled.
    result = _run_with_release(worker, worker.cancel)

    assert result.completions == [(2, 1, 0, 0)]
    assert gh.delete_calls == []


# --- get_gh_version ---------------------------------------------------------


def test_get_gh_version_success() -> None:
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "gh version 2.45.0 (2024-01-15)"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        version = get_gh_version()
        assert version == (2, 45)


def test_get_gh_version_timeout() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("gh", 5)

        version = get_gh_version()
        assert version is None


def test_get_gh_version_oserror() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = OSError("not found")

        version = get_gh_version()
        assert version is None


def test_get_gh_version_nonzero_exit() -> None:
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"
        mock_run.return_value = mock_result

        version = get_gh_version()
        assert version is None


def test_get_gh_version_unparseable_output() -> None:
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "some weird output"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        version = get_gh_version()
        assert version is None


# --- check_gh_version -------------------------------------------------------


def test_check_gh_version_ok() -> None:
    with patch("app.worker.get_gh_version", return_value=(2, 45)):
        assert check_gh_version() == ""


def test_check_gh_version_not_found() -> None:
    with patch("app.worker.get_gh_version", return_value=None):
        msg = check_gh_version()
        assert "could not determine" in msg


def test_check_gh_version_too_old() -> None:
    with patch("app.worker.get_gh_version", return_value=(1, 9)):
        msg = check_gh_version()
        assert "older than minimum" in msg
        assert "1.9" in msg


# --- check_gh_auth ----------------------------------------------------------


def test_check_gh_auth_missing() -> None:
    clear_gh_auth_cache()
    with patch("app.worker._find_gh_on_path", return_value=None):
        msg = check_gh_auth()
        assert "not found" in msg
        assert "cli.github.com" in msg


def test_check_gh_auth_timeout() -> None:
    clear_gh_auth_cache()
    with (
        patch("app.worker._find_gh_on_path", return_value="/usr/bin/gh"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = subprocess.TimeoutExpired("gh", 15)

        msg = check_gh_auth()
        assert "timed out" in msg


def test_check_gh_auth_oserror() -> None:
    clear_gh_auth_cache()
    with (
        patch("app.worker._find_gh_on_path", return_value="/usr/bin/gh"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = OSError("permission denied")

        msg = check_gh_auth()
        assert "could not run" in msg
        assert "permission denied" in msg


def test_check_gh_auth_not_authenticated() -> None:
    clear_gh_auth_cache()
    with (
        patch("app.worker._find_gh_on_path", return_value="/usr/bin/gh"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "not logged in"
        mock_run.return_value = mock_result

        msg = check_gh_auth()
        assert "not authenticated" in msg
        assert "gh auth login" in msg


def test_check_gh_auth_success() -> None:
    clear_gh_auth_cache()
    with (
        patch("app.worker._find_gh_on_path", return_value="/usr/bin/gh"),
        patch("subprocess.run") as mock_run,
    ):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Logged in"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        msg = check_gh_auth()
        assert msg == ""


# --- delete_run -------------------------------------------------------------


def test_delete_run_success() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_retry.return_value = mock_result

        ok, detail = delete_run("owner/repo", "123")
        assert ok is True
        assert detail == ""
        mock_retry.assert_called_once()


def test_delete_run_failure() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "run not found"
        mock_retry.return_value = mock_result

        ok, detail = delete_run("owner/repo", "123")
        assert ok is False
        assert "may have already been deleted" in detail


def test_delete_run_retry_exhausted() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_retry.return_value = None  # All retries exhausted

        ok, detail = delete_run("owner/repo", "123")
        assert ok is False
        assert "failed after retries" in detail


# --- delete_runs_batch ------------------------------------------------------


def test_delete_runs_batch_empty() -> None:
    results = delete_runs_batch("owner/repo", [])
    assert results == []


def test_delete_runs_batch_success() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_retry.return_value = mock_result

        results = delete_runs_batch("owner/repo", ["1", "2", "3"])
        assert len(results) == 3
        assert all(r[1] is True for r in results)


def test_delete_runs_batch_failure() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "batch failed"
        mock_retry.return_value = mock_result

        results = delete_runs_batch("owner/repo", ["1", "2"])
        assert len(results) == 2
        assert all(r[1] is False for r in results)
        assert all("batch failed" in r[2] for r in results)


def test_delete_runs_batch_retry_exhausted() -> None:
    with patch("app.worker.run_gh_with_retry") as mock_retry:
        mock_retry.return_value = None  # All retries exhausted

        results = delete_runs_batch("owner/repo", ["1", "2"])
        assert len(results) == 2
        assert all(r[1] is False for r in results)
        assert all("failed after retries" in r[2] for r in results)


# --- CleanupWorker with conclusions filter ----------------------------------


def test_conclusions_filter_applied_in_worker(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """When conclusions filter is set, worker applies it before selecting runs."""
    gh = _GhMock()
    # Two runs: one success, one failure
    gh.runs = [
        make_run(1, "sha1", "2024-06-01T00:00:00Z", "success"),
        make_run(2, "sha2", "2024-05-01T00:00:00Z", "failure"),
    ]
    gh.install(monkeypatch)

    # Filter to only "failure" conclusions
    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        conclusions={"failure"},
    )
    result = _run_worker(worker)

    # Only the failure run should be considered; it becomes the only commit
    # So no runs should be deleted (keep=1 protects it)
    assert result.completions == [(0, 1, 0, 0)]
    assert result.summaries == [(1, 0, 0)]


def test_conclusions_filter_with_multiple_conclusions(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """Worker respects multiple selected conclusions."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "sha1", "2024-06-01T00:00:00Z", "success"),
        make_run(2, "sha2", "2024-05-01T00:00:00Z", "failure"),
        make_run(3, "sha3", "2024-04-01T00:00:00Z", "cancelled"),
        make_run(4, "sha4", "2024-03-01T00:00:00Z", "skipped"),
    ]
    gh.install(monkeypatch)

    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        conclusions={"failure", "cancelled"},
    )
    result = _run_worker(worker)

    # Only failure and cancelled runs considered; they have different SHAs
    # keep=1 protects the newest (failure), cancelled gets deleted
    # kept=1 (run 2), deleted=1 (run 3)
    assert result.completions == [(0, 1, 1, 0)]
    assert result.summaries == [(1, 1, 0)]
    assert gh.delete_calls == ["3"]


def test_conclusions_filter_empty_set_means_no_filter(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """Empty conclusions set means no filter (all conclusions included)."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "sha1", "2024-06-01T00:00:00Z", "success"),
        make_run(2, "sha2", "2024-05-01T00:00:00Z", "failure"),
    ]
    gh.install(monkeypatch)

    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        conclusions=set(),  # Empty set = no filter
    )
    result = _run_worker(worker)

    # Both runs considered, failure is older and gets deleted
    assert result.completions == [(0, 1, 1, 0)]
    assert gh.delete_calls == ["2"]


def test_conclusions_filter_none_means_all(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """None conclusions means all conclusions included (default behavior)."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "sha1", "2024-06-01T00:00:00Z", "success"),
        make_run(2, "sha2", "2024-05-01T00:00:00Z", "failure"),
    ]
    gh.install(monkeypatch)

    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        conclusions=None,  # None = all
    )
    result = _run_worker(worker)

    # Both runs considered
    assert result.completions == [(0, 1, 1, 0)]
    assert gh.delete_calls == ["2"]


# --- CleanupWorker with older_than_days -------------------------------------


def test_older_than_days_filter_in_worker(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """Worker respects older_than_days filter."""
    from datetime import UTC, datetime, timedelta

    old_date = (
        (datetime.now(UTC) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    )
    new_date = (
        (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    )

    gh = _GhMock()
    gh.runs = [
        make_run(1, "old_sha", old_date, "success"),
        make_run(2, "new_sha", new_date, "success"),
    ]
    gh.install(monkeypatch)

    # Only consider runs older than 5 days
    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        older_than_days=5,
    )
    result = _run_worker(worker)

    # Only the old run is considered; it's the only commit so it's kept
    assert result.completions == [(0, 1, 0, 0)]
    assert result.summaries == [(1, 0, 0)]
    assert gh.delete_calls == []


def test_older_than_days_zero_disabled(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """older_than_days=0 means disabled (all runs considered)."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "old_sha", "2024-05-01T00:00:00Z", "success"),
        make_run(2, "new_sha", "2024-06-01T00:00:00Z", "success"),
    ]
    gh.install(monkeypatch)

    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=False,
        older_than_days=0,
    )
    result = _run_worker(worker)

    # Both runs considered; old_sha is older and gets deleted
    assert result.completions == [(0, 1, 1, 0)]
    assert gh.delete_calls == ["1"]


# --- CleanupWorker timeout clamping -----------------------------------------


def test_worker_clamps_timeouts_to_bounds() -> None:
    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=True,
        list_timeout=1,  # below TIMEOUT_MIN
        delete_timeout=1,
        auth_timeout=1,
    )
    assert worker.list_timeout == TIMEOUT_MIN
    assert worker.delete_timeout == TIMEOUT_MIN
    assert worker.auth_timeout == TIMEOUT_MIN

    worker = CleanupWorker(
        "owner/repo",
        keep=1,
        dry_run=True,
        list_timeout=9999,  # above TIMEOUT_MAX
        delete_timeout=9999,
        auth_timeout=9999,
    )
    assert worker.list_timeout == TIMEOUT_MAX
    assert worker.delete_timeout == TIMEOUT_MAX
    assert worker.auth_timeout == TIMEOUT_MAX


# --- CleanupWorker fetch_limit clamping -------------------------------------


def test_worker_clamps_fetch_limit_to_bounds() -> None:
    from app.settings import FETCH_LIMIT_MAX, FETCH_LIMIT_MIN

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, fetch_limit=1)
    assert worker.fetch_limit == FETCH_LIMIT_MIN

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, fetch_limit=99999)
    assert worker.fetch_limit == FETCH_LIMIT_MAX


# --- CleanupWorker max_deletions clamping -----------------------------------


def test_worker_clamps_max_deletions_to_non_negative() -> None:
    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, max_deletions=-10)
    assert worker.max_deletions == 0

    worker = CleanupWorker("owner/repo", keep=1, dry_run=True, max_deletions=100)
    assert worker.max_deletions == 100
