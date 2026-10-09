"""Tests for CleanupWorker.run() with the gh CLI mocked out."""

from __future__ import annotations

import subprocess
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

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_auth() -> str:
            return self.auth_error

        def fake_list(repo: str) -> list[app.RunInfo]:
            if self.list_error is not None:
                raise self.list_error
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
