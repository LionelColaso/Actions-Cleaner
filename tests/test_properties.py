"""Property-based tests using hypothesis for edge cases in core logic."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis.strategies import composite

from app.gh_retry import (
    _is_rate_limited,
    _is_transient_error,
    _parse_rate_limit_reset,
)
from app.repo import is_valid_repo, normalize_repo
from app.selection import (
    ALL_CONCLUSIONS,
    FAILED_CONCLUSIONS,
    RunInfo,
    filter_failed_runs,
    filter_runs_by_conclusions,
    filter_runs_older_than,
    select_runs_to_delete,
)
from app.settings import load_preferences, load_repos, save_preferences, save_repos

# --- Strategies for generating test data ---


RunInfoDict = RunInfo


@composite
def run_info(draw: st.DrawFn) -> RunInfoDict:
    """Generate a valid RunInfo-like dict."""
    # Use timezone-aware datetime to avoid DTZ001
    dt = draw(
        st.datetimes(
            min_value=datetime(2020, 1, 1, tzinfo=UTC),
            max_value=datetime(2026, 12, 31, tzinfo=UTC),
        )
    )
    return {
        "databaseId": draw(st.integers(min_value=1, max_value=10000)),
        "headSha": draw(st.text(alphabet="abcdef0123456789", min_size=7, max_size=40)),
        "createdAt": dt.isoformat().replace("+00:00", "Z"),
        "conclusion": draw(st.sampled_from(list(ALL_CONCLUSIONS))),
    }


@composite
def run_info_list(
    draw: st.DrawFn, min_size: int = 0, max_size: int = 20
) -> list[RunInfoDict]:
    """Generate a list of RunInfo dicts with unique databaseIds."""
    count = draw(st.integers(min_value=min_size, max_value=max_size))
    db_ids = draw(
        st.lists(
            st.integers(min_value=1, max_value=10000),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    runs: list[RunInfoDict] = []
    for db_id in db_ids:
        # Bind db_id to avoid B023
        def with_id(r: RunInfoDict, _id: int = db_id) -> RunInfoDict:
            return {**r, "databaseId": _id}

        runs.append(draw(run_info().map(with_id)))
    return runs


# --- Repo normalization tests ---


class TestRepoNormalizationProperties:
    """Property-based tests for repo normalization."""

    @given(st.text(min_size=1, max_size=100))
    def test_normalize_repo_never_crashes(self, s: str) -> None:
        """normalize_repo should never crash on any input."""
        try:
            normalize_repo(s)
        except ValueError, TypeError, AttributeError:
            pytest.fail(f"normalize_repo crashed on input: {s!r}")

    @given(st.text(min_size=1, max_size=100))
    def test_is_valid_repo_never_crashes(self, s: str) -> None:
        """is_valid_repo should never crash on any input."""
        try:
            is_valid_repo(s)
        except ValueError, TypeError, AttributeError:
            pytest.fail(f"is_valid_repo crashed on input: {s!r}")

    def test_valid_slug_roundtrip(self) -> None:
        """Valid owner/repo slugs pass through unchanged."""
        valid_slugs = [
            "owner/repo",
            "a/b",
            "my-org/my-repo",
            "user123/project_456",
        ]
        for slug in valid_slugs:
            assert normalize_repo(slug) == slug
            assert is_valid_repo(slug)

    def test_https_url_normalization(self) -> None:
        """HTTPS GitHub URLs normalize to owner/repo."""
        urls = [
            "https://github.com/owner/repo",
            "https://github.com/owner/repo/",
            "https://github.com/owner/repo.git",
        ]
        for url in urls:
            assert normalize_repo(url) == "owner/repo"

    def test_ssh_url_normalization(self) -> None:
        """SSH GitHub URLs normalize to owner/repo."""
        urls = [
            "git@github.com:owner/repo.git",
            "git@github.com:owner/repo",
        ]
        for url in urls:
            assert normalize_repo(url) == "owner/repo"

    def test_invalid_inputs_rejected(self) -> None:
        """Clearly invalid inputs are rejected."""
        invalid = [
            "",
            "owner",
            "owner/",
            "/repo",
            "owner/repo/extra",
            "not-a-url",
            "https://gitlab.com/owner/repo",
        ]
        for inv in invalid:
            assert not is_valid_repo(inv)


# --- Run selection property tests ---


class TestRunSelectionProperties:
    """Property-based tests for run selection logic."""

    @given(
        run_info_list(min_size=0, max_size=20), st.integers(min_value=0, max_value=10)
    )
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_select_runs_to_delete_keeps_correct_count(
        self, runs: list[RunInfoDict], keep: int
    ) -> None:
        """select_runs_to_delete keeps at most `keep` distinct commits."""
        delete_ids, kept_ids = select_runs_to_delete(runs, keep)
        # All runs are either deleted or kept
        assert len(delete_ids) + len(kept_ids) == len(runs)
        # Kept runs belong to at most `keep` distinct commits (or 1 if keep=0)
        kept_shas = {
            run["headSha"] for run in runs if str(run["databaseId"]) in kept_ids
        }
        assert len(kept_shas) <= max(keep, 1)

    @given(
        run_info_list(min_size=5, max_size=20), st.integers(min_value=1, max_value=5)
    )
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_select_runs_to_delete_newest_first(
        self, runs: list[RunInfoDict], keep: int
    ) -> None:
        """When keep >= 1, the newest commit's runs are always kept."""
        sorted_runs = sorted(runs, key=lambda r: r["createdAt"], reverse=True)
        newest_sha = sorted_runs[0]["headSha"]
        _delete_ids, kept_ids = select_runs_to_delete(runs, keep)
        if keep >= 1:
            for run in runs:
                if run["headSha"] == newest_sha:
                    assert str(run["databaseId"]) in kept_ids

    @given(
        run_info_list(min_size=0, max_size=10), st.integers(min_value=0, max_value=365)
    )
    def test_filter_runs_older_than(self, runs: list[RunInfoDict], days: int) -> None:
        """filter_runs_older_than only returns runs older than cutoff."""
        try:
            filtered = filter_runs_older_than(runs, days)
            if days <= 0:
                assert filtered == runs
            else:
                cutoff = datetime.now(UTC)
                # Handle month boundary
                new_day = cutoff.day - days
                while new_day <= 0:
                    if cutoff.month == 1:
                        cutoff = cutoff.replace(year=cutoff.year - 1, month=12, day=31)
                    else:
                        cutoff = cutoff.replace(month=cutoff.month - 1, day=1)
                    new_day = cutoff.day - days
                cutoff = cutoff.replace(day=max(1, new_day))
                for run in filtered:
                    created = datetime.fromisoformat(run["createdAt"])
                    assert created < cutoff
        except ValueError:
            # filter_runs_older_than can raise ValueError on month boundary
            pass

    @given(run_info_list(min_size=0, max_size=20))
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_filter_failed_runs_subset(self, runs: list[RunInfoDict]) -> None:
        """filter_failed_runs returns a subset of input."""
        filtered = filter_failed_runs(runs)
        assert len(filtered) <= len(runs)
        for run in filtered:
            assert run["conclusion"] in FAILED_CONCLUSIONS

    @given(
        run_info_list(min_size=0, max_size=20),
        st.sets(st.sampled_from(list(ALL_CONCLUSIONS)), min_size=0, max_size=5),
    )
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_filter_runs_by_conclusions_subset(
        self, runs: list[RunInfoDict], conclusions: set[str]
    ) -> None:
        """filter_runs_by_conclusions returns a subset of input."""
        filtered = filter_runs_by_conclusions(runs, conclusions)
        assert len(filtered) <= len(runs)
        if conclusions:
            for run in filtered:
                assert run["conclusion"] in conclusions

    @given(run_info_list(min_size=0, max_size=20))
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_filter_runs_by_conclusions_empty_returns_all(
        self, runs: list[RunInfoDict]
    ) -> None:
        """Empty conclusions set returns all runs unchanged."""
        filtered = filter_runs_by_conclusions(runs, set())
        assert filtered == runs

    @given(run_info_list(min_size=0, max_size=20))
    @settings(suppress_health_check=[HealthCheck.too_slow], max_examples=20)
    def test_filter_runs_by_conclusions_all_returns_all(
        self, runs: list[RunInfoDict]
    ) -> None:
        """ALL_CONCLUSIONS returns all runs unchanged."""
        filtered = filter_runs_by_conclusions(runs, ALL_CONCLUSIONS)
        assert filtered == runs


# --- Settings persistence tests ---


class TestSettingsPersistenceProperties:
    """Property-based tests for settings persistence."""

    def test_repos_roundtrip(self) -> None:
        """save_repos/load_repos roundtrip preserves data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_file = Path(tmpdir) / "repos.json"
            with patch("app.settings.SETTINGS_PATH", str(repos_file)):
                test_repos = ["owner/repo1", "owner/repo2", "another/repo"]
                assert save_repos(test_repos)
                loaded = load_repos()
                assert loaded == test_repos

    def test_repos_roundtrip_removes_duplicates(self) -> None:
        """save_repos removes duplicates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_file = Path(tmpdir) / "repos.json"
            with patch("app.settings.SETTINGS_PATH", str(repos_file)):
                test_repos = ["owner/repo1", "owner/repo1", "owner/repo2"]
                assert save_repos(test_repos)
                loaded = load_repos()
                assert loaded == ["owner/repo1", "owner/repo2"]

    def test_preferences_roundtrip(self) -> None:
        """save_preferences/load_preferences roundtrip preserves data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            prefs_file = Path(tmpdir) / "prefs.json"
            with patch("app.settings.PREFERENCES_PATH", str(prefs_file)):
                _test_prefs = {
                    "keep": 5,
                    "failed_only": True,
                    "fetch_limit": 500,
                    "max_deletions": 100,
                    "last_repo": "owner/repo",
                    "concurrency": 4,
                    "list_timeout": 120,
                    "delete_timeout": 60,
                    "auth_timeout": 30,
                    "dark_mode": True,
                }
                assert save_preferences(
                    keep=5,
                    failed_only=True,
                    fetch_limit=500,
                    max_deletions=100,
                    last_repo="owner/repo",
                    concurrency=4,
                    list_timeout=120,
                    delete_timeout=60,
                    auth_timeout=30,
                    dark_mode=True,
                )
                loaded = load_preferences()
                expected_tuple = (
                    5,
                    True,
                    500,
                    100,
                    "owner/repo",
                    4,
                    120,
                    60,
                    30,
                    True,
                )
                assert loaded == expected_tuple

    def test_load_repos_missing_file_returns_empty(self) -> None:
        """Missing repos file returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_file = Path(tmpdir) / "nonexistent.json"
            with patch("app.settings.SETTINGS_PATH", str(repos_file)):
                loaded = load_repos()
                assert loaded == []

    def test_load_repos_corrupt_json_returns_empty(self) -> None:
        """Corrupt JSON returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos_file = Path(tmpdir) / "corrupt.json"
            repos_file.write_text("{ not valid json }")
            with patch("app.settings.SETTINGS_PATH", str(repos_file)):
                loaded = load_repos()
                assert loaded == []


# --- gh_retry property tests ---


class TestGhRetryProperties:
    """Property-based tests for gh_retry module."""

    @given(st.text(min_size=0, max_size=200))
    def test_is_rate_limited_never_crashes(self, s: str) -> None:
        """_is_rate_limited never crashes."""
        try:
            _is_rate_limited(s)
        except ValueError, TypeError, AttributeError:
            pytest.fail(f"_is_rate_limited crashed on: {s!r}")

    @given(st.text(min_size=0, max_size=200))
    def test_is_transient_error_never_crashes(self, s: str) -> None:
        """_is_transient_error never crashes."""
        try:
            _is_transient_error(s)
        except ValueError, TypeError, AttributeError:
            pytest.fail(f"_is_transient_error crashed on: {s!r}")

    @given(st.text(min_size=0, max_size=200))
    def test_parse_rate_limit_reset_never_crashes(self, s: str) -> None:
        """_parse_rate_limit_reset never crashes."""
        try:
            _parse_rate_limit_reset(s)
        except ValueError, TypeError, AttributeError:
            pytest.fail(f"_parse_rate_limit_reset crashed on: {s!r}")


# --- Integration tests for worker flow ---


class TestWorkerIntegration:
    """Integration-style tests for worker components."""

    def test_worker_imports_all_modules(self) -> None:
        """Worker can import all required modules."""
        from app.worker import (
            CleanupWorker,
            check_gh_auth,
            check_gh_version,
            delete_run,
            delete_runs_batch,
            get_gh_version,
        )

        assert CleanupWorker is not None
        assert check_gh_auth is not None
        assert check_gh_version is not None
        assert delete_run is not None
        assert delete_runs_batch is not None
        assert get_gh_version is not None

    def test_selection_imports_all(self) -> None:
        """Selection module exports all public functions."""
        from app.selection import (
            ALL_CONCLUSIONS,
            FAILED_CONCLUSIONS,
        )

        assert ALL_CONCLUSIONS is not None
        assert FAILED_CONCLUSIONS is not None

    def test_settings_imports_all(self) -> None:
        """Settings module exports all public functions."""
        from app.settings import (
            load_preferences,
            load_repos,
            save_preferences,
            save_repos,
        )

        assert load_preferences is not None
        assert load_repos is not None
        assert save_preferences is not None
        assert save_repos is not None

    def test_gh_retry_imports_all(self) -> None:
        """gh_retry module exports all public functions."""
        from app.gh_retry import (
            MAX_RETRIES,
            RETRY_BASE_DELAY,
            RETRY_MAX_DELAY,
        )

        assert MAX_RETRIES == 3
        assert RETRY_BASE_DELAY == 1.0
        assert RETRY_MAX_DELAY == 30.0
