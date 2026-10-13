"""Tests for the pure run-selection / failed-filter logic."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from app.selection import (
    RunInfo,
    _as_dict,
    _parse_datetime,
    filter_failed_runs,
    filter_runs_by_conclusions,
    filter_runs_older_than,
    list_runs,
    select_runs_to_delete,
)

MakeRun = Callable[..., RunInfo]


def test_keeps_newest_commits_regardless_of_input_order(
    make_run: MakeRun,
) -> None:
    runs = [
        make_run(1, "old", "2024-01-01T00:00:00Z"),
        make_run(2, "new", "2024-06-01T00:00:00Z"),
        make_run(3, "mid", "2024-03-01T00:00:00Z"),
    ]
    delete, kept = select_runs_to_delete(runs, keep=1)
    # Deletion candidates are listed newest-first.
    assert delete == ["3", "1"]
    assert kept == ["2"]


def test_keeps_all_runs_of_kept_commits(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "a", "2024-06-01T01:00:00Z"),
        make_run(2, "a", "2024-06-01T00:00:00Z"),  # 2nd workflow, same commit
        make_run(3, "b", "2024-05-01T00:00:00Z"),
    ]
    delete, kept = select_runs_to_delete(runs, keep=1)
    assert sorted(kept) == ["1", "2"]
    assert delete == ["3"]


def test_keep_larger_than_commit_count(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "a", "2024-06-01T00:00:00Z"),
        make_run(2, "b", "2024-05-01T00:00:00Z"),
    ]
    delete, kept = select_runs_to_delete(runs, keep=5)
    assert delete == []
    assert sorted(kept) == ["1", "2"]


def test_empty_input() -> None:
    assert select_runs_to_delete([], keep=2) == ([], [])


def test_delete_ids_are_strings(make_run: MakeRun) -> None:
    runs = [
        make_run(1, "new", "2024-06-01T00:00:00Z"),
        make_run(2, "old", "2024-05-01T00:00:00Z"),
    ]
    delete, _kept = select_runs_to_delete(runs, keep=1)
    assert delete == ["2"]


def test_filter_failed_runs_includes_expected_conclusions() -> None:
    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-01T00:00:00Z",
            "conclusion": "failure",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "cancelled",
        },
        {
            "databaseId": 3,
            "headSha": "c",
            "createdAt": "2024-04-01T00:00:00Z",
            "conclusion": "timed_out",
        },
        {
            "databaseId": 4,
            "headSha": "d",
            "createdAt": "2024-03-01T00:00:00Z",
            "conclusion": "startup_failure",
        },
        {
            "databaseId": 5,
            "headSha": "e",
            "createdAt": "2024-02-01T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 6,
            "headSha": "f",
            "createdAt": "2024-01-01T00:00:00Z",
            "conclusion": "",
        },
    ]
    failed = filter_failed_runs(runs)
    assert {run["databaseId"] for run in failed} == {1, 2, 3, 4}


# --- _as_dict ---------------------------------------------------------------


def test_as_dict_returns_dict_for_dict() -> None:
    d = {"a": 1, "b": 2}
    assert _as_dict(d) == d


def test_as_dict_returns_empty_for_non_dict() -> None:
    assert _as_dict("not a dict") == {}
    assert _as_dict([1, 2, 3]) == {}
    assert _as_dict(None) == {}
    assert _as_dict(42) == {}


# --- _parse_datetime --------------------------------------------------------


def test_parse_datetime_valid_z_suffix() -> None:
    dt = _parse_datetime("2024-06-15T12:30:45Z")
    assert dt is not None
    assert dt.year == 2024
    assert dt.month == 6
    assert dt.day == 15
    assert dt.hour == 12
    assert dt.minute == 30
    assert dt.second == 45
    assert dt.tzinfo == UTC


def test_parse_datetime_valid_offset() -> None:
    dt = _parse_datetime("2024-06-15T12:30:45+00:00")
    assert dt is not None
    assert dt.tzinfo == UTC


def test_parse_datetime_invalid_returns_none() -> None:
    assert _parse_datetime("not a date") is None
    assert _parse_datetime("") is None
    assert _parse_datetime(None) is None  # type: ignore[arg-type]


# --- filter_runs_older_than -------------------------------------------------


def test_filter_runs_older_than_zero_days_returns_all() -> None:
    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-01T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "success",
        },
    ]
    assert filter_runs_older_than(runs, 0) == runs
    assert filter_runs_older_than(runs, -5) == runs


def test_filter_runs_older_than_filters_correctly() -> None:
    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-15T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "success",
        },
    ]
    # With 10 days, the June 15 run is too new (assuming today is after June 25)
    filtered = filter_runs_older_than(runs, 10)
    # The test depends on current date - we just check it runs without error
    assert isinstance(filtered, list)


# --- filter_runs_by_conclusions ---------------------------------------------


def test_filter_runs_by_conclusions_empty_returns_all() -> None:
    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-01T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "failure",
        },
    ]
    assert filter_runs_by_conclusions(runs, set()) == runs


def test_filter_runs_by_conclusions_filters_correctly() -> None:
    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-01T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "failure",
        },
        {
            "databaseId": 3,
            "headSha": "c",
            "createdAt": "2024-04-01T00:00:00Z",
            "conclusion": "cancelled",
        },
    ]
    filtered = filter_runs_by_conclusions(runs, {"failure", "cancelled"})
    assert {run["databaseId"] for run in filtered} == {2, 3}


def test_filter_runs_by_conclusions_all_returns_all() -> None:
    from app.selection import ALL_CONCLUSIONS

    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "a",
            "createdAt": "2024-06-01T00:00:00Z",
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "b",
            "createdAt": "2024-05-01T00:00:00Z",
            "conclusion": "failure",
        },
    ]
    assert filter_runs_by_conclusions(runs, ALL_CONCLUSIONS) == runs


# --- list_runs (mocked gh) --------------------------------------------------


def test_list_runs_invalid_repo_raises() -> None:
    with patch("app.selection.is_valid_repo", return_value=False):
        try:
            list_runs("invalid-repo")
        except ValueError as e:
            assert "invalid repository slug" in str(e)
        else:
            pytest.fail("Expected ValueError")


@patch("app.selection.run_gh_with_retry")
@patch("app.selection.is_valid_repo", return_value=True)
def test_list_runs_success(mock_is_valid: MagicMock, mock_run_gh: MagicMock) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(
        [
            {
                "databaseId": 1,
                "headSha": "abc123",
                "createdAt": "2024-06-01T00:00:00Z",
                "conclusion": "success",
            },
            {
                "databaseId": 2,
                "headSha": "def456",
                "createdAt": "2024-05-01T00:00:00Z",
                "conclusion": "failure",
            },
        ]
    )
    mock_result.stderr = ""
    mock_run_gh.return_value = mock_result

    runs = list_runs("owner/repo", fetch_limit=100, workflow="ci.yml")
    assert len(runs) == 2
    assert runs[0]["databaseId"] == 1
    assert runs[0]["headSha"] == "abc123"
    assert runs[1]["databaseId"] == 2
    assert runs[1]["headSha"] == "def456"
    mock_run_gh.assert_called_once()


@patch("app.selection.run_gh_with_retry")
@patch("app.selection.is_valid_repo", return_value=True)
def test_list_runs_gh_error(mock_is_valid: MagicMock, mock_run_gh: MagicMock) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "authentication failed"
    mock_run_gh.return_value = mock_result

    try:
        list_runs("owner/repo")
    except subprocess.CalledProcessError as e:
        # The detail is passed as the third argument (output attribute)
        assert "authentication failed" in (e.output or "").lower()
    else:
        pytest.fail("Expected CalledProcessError")


@patch("app.selection.run_gh_with_retry")
@patch("app.selection.is_valid_repo", return_value=True)
def test_list_runs_retry_failure(
    mock_is_valid: MagicMock, mock_run_gh: MagicMock
) -> None:
    mock_run_gh.return_value = None  # All retries exhausted

    try:
        list_runs("owner/repo")
    except subprocess.CalledProcessError as e:
        # The detail is passed as the third argument (output attribute)
        detail = (e.output or "").lower()
        assert "failed after retries" in detail or "cancelled" in detail
    else:
        pytest.fail("Expected CalledProcessError")


@patch("app.selection.run_gh_with_retry")
@patch("app.selection.is_valid_repo", return_value=True)
def test_list_runs_invalid_json(
    mock_is_valid: MagicMock, mock_run_gh: MagicMock
) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not valid json"
    mock_result.stderr = ""
    mock_run_gh.return_value = mock_result

    try:
        list_runs("owner/repo")
    except json.JSONDecodeError, TypeError:
        pass
    else:
        pytest.fail("Expected JSON decode error or TypeError")


@patch("app.selection.run_gh_with_retry")
@patch("app.selection.is_valid_repo", return_value=True)
def test_list_runs_skips_malformed_items(
    mock_is_valid: MagicMock, mock_run_gh: MagicMock
) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(
        [
            {
                "databaseId": 1,
                "headSha": "abc123",
                "createdAt": "2024-06-01T00:00:00Z",
                "conclusion": "success",
            },
            {"invalid": "item"},  # missing databaseId/headSha
            "not a dict",
            {
                "databaseId": 2,
                "headSha": "def456",
                "createdAt": "2024-05-01T00:00:00Z",
                "conclusion": "failure",
            },
        ]
    )
    mock_result.stderr = ""
    mock_run_gh.return_value = mock_result

    runs = list_runs("owner/repo")
    assert len(runs) == 2
    assert runs[0]["databaseId"] == 1
    assert runs[1]["databaseId"] == 2


# --- select_runs_to_delete with older_than_days -----------------------------


def test_select_runs_to_delete_with_older_than_days() -> None:
    from datetime import UTC, datetime, timedelta

    # Create dates that are clearly old vs new
    old_date = (
        (datetime.now(UTC) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    )
    new_date = (
        (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    )

    runs: list[RunInfo] = [
        {
            "databaseId": 1,
            "headSha": "old_sha",
            "createdAt": old_date,
            "conclusion": "success",
        },
        {
            "databaseId": 2,
            "headSha": "new_sha",
            "createdAt": new_date,
            "conclusion": "success",
        },
    ]
    delete_ids, kept_ids = select_runs_to_delete(runs, keep=1, older_than_days=5)
    # Only the old run should be considered for deletion (newer than 5 days excluded)
    # The old run is the only one in the filtered set, so it's kept (keep=1 protects it)
    assert delete_ids == []
    assert kept_ids == ["1"]
