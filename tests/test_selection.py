"""Tests for the pure run-selection / failed-filter logic."""

from __future__ import annotations

from collections.abc import Callable

import actions_cleaner_gui as app

MakeRun = Callable[..., app.RunInfo]


def test_keeps_newest_commits_regardless_of_input_order(
    make_run: MakeRun,
) -> None:
    runs = [
        make_run(1, "old", "2024-01-01T00:00:00Z"),
        make_run(2, "new", "2024-06-01T00:00:00Z"),
        make_run(3, "mid", "2024-03-01T00:00:00Z"),
    ]
    delete, kept = app.select_runs_to_delete(runs, keep=1)
    # Deletion candidates are listed newest-first.
    assert delete == ["3", "1"]
    assert kept == ["2"]


def test_keeps_all_runs_of_kept_commits(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "a", "2024-06-01T01:00:00Z"),
        make_run(2, "a", "2024-06-01T00:00:00Z"),  # 2nd workflow, same commit
        make_run(3, "b", "2024-05-01T00:00:00Z"),
    ]
    delete, kept = app.select_runs_to_delete(runs, keep=1)
    assert sorted(kept) == ["1", "2"]
    assert delete == ["3"]


def test_keep_larger_than_commit_count(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "a", "2024-06-01T00:00:00Z"),
        make_run(2, "b", "2024-05-01T00:00:00Z"),
    ]
    delete, kept = app.select_runs_to_delete(runs, keep=5)
    assert delete == []
    assert sorted(kept) == ["1", "2"]


def test_empty_input() -> None:
    assert app.select_runs_to_delete([], keep=2) == ([], [])


def test_delete_ids_are_strings(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "new", "2024-06-01T00:00:00Z"),
        make_run(2, "old", "2024-05-01T00:00:00Z"),
    ]
    delete, _kept = app.select_runs_to_delete(runs, keep=1)
    assert delete == ["2"]


def test_filter_failed_runs_includes_expected_conclusions(
    make_run: MakeRun,
) -> None:
    runs = [
        make_run(1, "a", "2024-06-01T00:00:00Z", "failure"),
        make_run(2, "b", "2024-05-01T00:00:00Z", "cancelled"),
        make_run(3, "c", "2024-04-01T00:00:00Z", "timed_out"),
        make_run(4, "d", "2024-03-01T00:00:00Z", "startup_failure"),
        make_run(5, "e", "2024-02-01T00:00:00Z", "success"),
        make_run(6, "f", "2024-01-01T00:00:00Z", ""),
    ]
    failed = app.filter_failed_runs(runs)
    assert {run["databaseId"] for run in failed} == {1, 2, 3, 4}
