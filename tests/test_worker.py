"""Tests for CleanupWorker.run() with the gh CLI mocked out."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

import pytest

import actions_cleaner_gui as app

MakeRun = Callable[..., app.RunInfo]


class WorkerResult:
    """Captures everything a worker emits during a synchronous run()."""

    def __init__(self) -> None:
        self.logs: list[str] = []
        self.finished: list[int] = []
        self.summaries: list[tuple[int, int, int]] = []
        self.maxes: list[int] = []
        self.progress: list[int] = []

    def record_summary(self, kept: int, deleted: int, failed: int) -> None:
        self.summaries.append((kept, deleted, failed))


class _GhMock:
    """Configurable stand-in for the module's gh helper functions."""

    def __init__(self) -> None:
        self.runs: list[app.RunInfo] = []
        self.auth_error = ""
        self.list_error: BaseException | None = None
        self.delete_error: BaseException | None = None
        self.delete_ok = True
        self.delete_detail = ""
        self.delete_calls: list[str] = []
        self.list_fetch_limit = 0
        self.list_workflow = ""

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_auth() -> str:
            return self.auth_error

        def fake_list(
            repo: str, fetch_limit: int = 0, workflow: str = ""
        ) -> list[app.RunInfo]:
            if self.list_error is not None:
                raise self.list_error
            self.list_fetch_limit = fetch_limit
            self.list_workflow = workflow
            return list(self.runs)

        def fake_delete(repo: str, run_id: str) -> tuple[bool, str]:
            if self.delete_error is not None:
                raise self.delete_error
            self.delete_calls.append(run_id)
            return self.delete_ok, self.delete_detail

        monkeypatch.setattr(app, "check_gh_auth", fake_auth)
        monkeypatch.setattr(app, "list_runs", fake_list)
        monkeypatch.setattr(app, "delete_run", fake_delete)


def _run_worker(worker: app.CleanupWorker) -> WorkerResult:
    result = WorkerResult()
    worker.log_signal.connect(result.logs.append)
    worker.finished_signal.connect(result.finished.append)
    worker.summary_signal.connect(result.record_summary)
    worker.max_signal.connect(result.maxes.append)
    worker.progress_signal.connect(result.progress.append)
    worker.run()  # synchronous; same-thread signal connections are direct
    return result


def _run_with_release(
    worker: app.CleanupWorker, release: Callable[[], object]
) -> WorkerResult:
    """Run the worker, firing `release` on a timer so a paused run() wakes."""
    timer = threading.Timer(0.2, release)
    timer.start()
    try:
        return _run_worker(worker)
    finally:
        timer.join()


def _sample_runs(make_run: MakeRun) -> list[app.RunInfo]:
    return [
        make_run(1, "new", "2024-06-01T00:00:00Z"),
        make_run(2, "new", "2024-06-01T01:00:00Z"),
        make_run(3, "old", "2024-05-01T00:00:00Z"),
    ]


def test_dry_run_never_deletes(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert gh.delete_calls == []
    assert result.finished == [0]
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

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False)
    result = _run_worker(worker)

    assert gh.delete_calls == ["3"]
    assert result.finished == [0]
    assert result.summaries == [(2, 1, 0)]
    assert any("Deleted run 3" in line for line in result.logs)


def test_failed_deletions_produce_failure_code(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.delete_ok = False
    gh.delete_detail = "boom"
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False)
    result = _run_worker(worker)

    assert result.finished == [1]  # never report success with failures
    assert result.summaries == [(2, 0, 1)]
    assert any("Failed to delete run 3: boom" in line for line in result.logs)


def test_missing_gh_cli_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.list_error = FileNotFoundError("gh")
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.finished == [1]
    assert any("Error fetching runs" in line for line in result.logs)


def test_called_process_error_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.list_error = subprocess.CalledProcessError(128, ["gh"], stderr="boom")
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.finished == [1]
    assert any("boom" in line for line in result.logs)


def test_unexpected_output_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.list_error = TypeError("unexpected gh output")
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.finished == [1]


def test_auth_failure_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.auth_error = "`gh` is not authenticated"
    gh.list_error = AssertionError("list_runs must not be called")
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.finished == [1]
    assert any("not authenticated" in line for line in result.logs)


def test_no_runs_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gh = _GhMock()
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True)
    result = _run_worker(worker)

    assert result.finished == [0]
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

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False, failed_only=True)
    result = _run_worker(worker)

    # Only the failed run remains, and it belongs to the single kept commit.
    assert result.finished == [0]
    assert result.summaries == [(1, 0, 0)]
    assert any("Failed-only" in line for line in result.logs)


def test_cancel_stops_before_deleting(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.delete_error = AssertionError("cancelled worker must not delete")
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.cancel()
    result = _run_worker(worker)

    assert result.finished == [2]
    assert any("cancelled" in line.lower() for line in result.logs)


def test_fetch_limit_is_passed_to_list_runs(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=True, fetch_limit=250)
    _run_worker(worker)

    assert gh.list_fetch_limit == 250


def test_max_deletions_caps_processed_runs(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = [
        make_run(1, "old-a", "2024-05-01T00:00:00Z"),
        make_run(2, "old-b", "2024-05-02T00:00:00Z"),
        make_run(3, "old-c", "2024-05-03T00:00:00Z"),
        make_run(4, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.install(monkeypatch)

    # keep=1 protects the "new" commit; 3 old runs would be deleted, but the
    # cap stops after the first 2.
    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False, max_deletions=2)
    result = _run_worker(worker)

    assert len(gh.delete_calls) == 2
    assert result.finished == [0]
    assert result.maxes == [2]
    assert any("Max deletions cap of 2" in line for line in result.logs)


def test_max_deletions_zero_means_unlimited(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False, max_deletions=0)
    result = _run_worker(worker)

    assert len(gh.delete_calls) == 1  # only run 3 is deletable here
    assert result.finished == [0]


def test_concurrency_one_is_sequential(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = _sample_runs(make_run)
    gh.install(monkeypatch)

    worker = app.CleanupWorker(
        "owner/repo", keep=1, dry_run=False, concurrency=1
    )
    result = _run_worker(worker)

    assert gh.delete_calls == ["3"]
    assert result.finished == [0]


def test_concurrency_parallel_deletes_all(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    """A pool of workers still deletes every targeted run; failed IDs are
    reported by name in the log instead of just a count."""
    gh = _GhMock()
    gh.runs = [
        make_run(1, "old-a", "2024-05-01T00:00:00Z"),
        make_run(2, "old-b", "2024-05-02T00:00:00Z"),
        make_run(3, "old-c", "2024-05-03T00:00:00Z"),
        make_run(4, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.install(monkeypatch)

    worker = app.CleanupWorker(
        "owner/repo", keep=1, dry_run=False, concurrency=4
    )
    result = _run_worker(worker)

    assert set(gh.delete_calls) == {"1", "2", "3"}
    assert result.finished == [0]


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

    # Make run "1" fail so the log should name it explicitly.
    # Override the mock's delete after install so the worker uses flaky_delete.
    original_delete = app.delete_run

    def flaky_delete(repo: str, run_id: str) -> tuple[bool, str]:
        if run_id == "1":
            return False, "boom"
        return original_delete(repo, run_id)

    monkeypatch.setattr(app, "delete_run", flaky_delete)

    worker = app.CleanupWorker(
        "owner/repo", keep=1, dry_run=False, concurrency=3
    )
    result = _run_worker(worker)

    assert result.finished == [1]
    assert any("run 1" in line and "boom" in line for line in result.logs)
    assert any(
        line.startswith("Cleanup finished with 1 failure(s):")
        and "1" in line
        for line in result.logs
    )


def test_concurrency_clamped_to_bounds() -> None:
    worker = app.CleanupWorker(
        "owner/repo", keep=1, dry_run=True, concurrency=0
    )
    assert worker.concurrency == app.CONCURRENCY_MIN

    worker = app.CleanupWorker(
        "owner/repo", keep=1, dry_run=True, concurrency=99
    )
    assert worker.concurrency == app.CONCURRENCY_MAX


def test_pause_blocks_until_resumed(
    monkeypatch: pytest.MonkeyPatch, make_run: MakeRun
) -> None:
    gh = _GhMock()
    gh.runs = [
        make_run(10, "old", "2024-05-01T00:00:00Z"),
        make_run(11, "new", "2024-06-01T00:00:00Z"),
    ]
    gh.install(monkeypatch)

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.set_paused(True)
    assert worker.paused is True

    # Released on a timer: proves run() blocked on the gate, not ignored it.
    result = _run_with_release(worker, lambda: worker.set_paused(False))

    assert result.finished == [0]
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

    worker = app.CleanupWorker("owner/repo", keep=1, dry_run=False)
    worker.set_paused(True)

    # A pause + cancel must not deadlock; run() exits as cancelled.
    result = _run_with_release(worker, worker.cancel)

    assert result.finished == [2]
    assert gh.delete_calls == []
