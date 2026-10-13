"""Tests for repository/preference persistence and input validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.repo import is_valid_repo, normalize_repo
from app.settings import (
    CONCURRENCY_DEFAULT,
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    FETCH_LIMIT_DEFAULT,
    FETCH_LIMIT_MAX,
    FETCH_LIMIT_MIN,
    MAX_DELETIONS_MAX,
    TIMEOUT_AUTH_DEFAULT,
    TIMEOUT_DELETE_DEFAULT,
    TIMEOUT_LIST_DEFAULT,
    TIMEOUT_MAX,
    TIMEOUT_MIN,
    export_settings,
    import_settings,
    load_preferences,
    load_repos,
    save_preferences,
    save_repos,
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# --- is_valid_repo ---------------------------------------------------------


def test_is_valid_repo_accepts_slugs() -> None:
    assert is_valid_repo("owner/repo")
    assert is_valid_repo("some-org/my.repo_2")
    assert is_valid_repo("a.b-c/d_e.f")


def test_is_valid_repo_rejects_malformed() -> None:
    assert not is_valid_repo("")
    assert not is_valid_repo("noslash")
    assert not is_valid_repo("a/b/c")
    assert not is_valid_repo("owner/repo extra")
    assert not is_valid_repo("owner/repo; rm -rf /")


# --- normalize_repo --------------------------------------------------------


def test_normalize_repo_passes_slugs_through() -> None:
    assert normalize_repo("owner/repo") == "owner/repo"
    assert normalize_repo("  owner/repo  ") == "owner/repo"


def test_normalize_repo_extracts_https_url() -> None:
    url = "https://github.com/LionelColaso/llama_gui"
    assert normalize_repo(url) == "LionelColaso/llama_gui"
    assert is_valid_repo(normalize_repo(url))


def test_normalize_repo_handles_url_variants() -> None:
    assert normalize_repo("http://github.com/o/r") == "o/r"
    assert normalize_repo("www.github.com/o/r") == "o/r"
    assert normalize_repo("github.com/o/r") == "o/r"
    assert normalize_repo("GitHub.com/o/r") == "o/r"
    assert normalize_repo("https://github.com/o/r/") == "o/r"
    assert normalize_repo("https://github.com/o/r.git") == "o/r"
    assert normalize_repo("https://github.com/o/r/actions/runs/42") == "o/r"
    assert normalize_repo("https://github.com/o/r?tab=readme") == "o/r"
    assert normalize_repo("https://github.com/o/r/pull/7") == "o/r"


def test_normalize_repo_handles_ssh_remote() -> None:
    assert normalize_repo("git@github.com:o/r.git") == "o/r"
    assert normalize_repo("git@github.com:o/r") == "o/r"


def test_normalize_repo_leaves_foreign_input_alone() -> None:
    foreign = "https://gitlab.com/o/r"
    assert normalize_repo(foreign) == foreign
    assert not is_valid_repo(normalize_repo(foreign))
    only_owner = "https://github.com/onlyowner"
    assert normalize_repo(only_owner) == only_owner
    assert not is_valid_repo(normalize_repo(only_owner))
    assert normalize_repo("") == ""


# --- load_repos ------------------------------------------------------------


def test_load_repos_missing_file(settings_paths: tuple[Path, Path]) -> None:
    assert load_repos() == []


def test_load_repos_corrupt_json(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, "{not json")
    assert load_repos() == []


def test_load_repos_bare_list(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '["a/one", "b/two"]')
    assert load_repos() == ["a/one", "b/two"]


def test_load_repos_legacy_dict_format(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '{"repos": ["legacy/repo"]}')
    assert load_repos() == ["legacy/repo"]


def test_load_repos_filters_junk_and_duplicates(
    settings_paths: tuple[Path, Path],
) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, json.dumps([1, "", "a/b", "a/b", "c/d", None]))
    assert load_repos() == ["a/b", "c/d"]


def test_load_repos_non_list_root(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '"just a string"')
    assert load_repos() == []


# --- save_repos ------------------------------------------------------------


def test_save_repos_writes_bare_json_array(
    settings_paths: tuple[Path, Path],
) -> None:
    repos_path, _ = settings_paths
    assert save_repos(["x/y", "z/w"])
    data = json.loads(repos_path.read_text(encoding="utf-8"))
    assert data == ["x/y", "z/w"]


def test_save_repos_failure_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "as-directory"
    target.mkdir()
    monkeypatch.setattr("app.settings.SETTINGS_PATH", str(target))
    assert not save_repos(["x/y"])


# --- preferences -----------------------------------------------------------


def test_load_preferences_defaults(
    settings_paths: tuple[Path, Path],
) -> None:
    assert load_preferences() == (
        2,
        False,
        FETCH_LIMIT_DEFAULT,
        0,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_preferences_roundtrip(settings_paths: tuple[Path, Path]) -> None:
    assert save_preferences(7, True, 500, 25)
    assert load_preferences() == (
        7,
        True,
        500,
        25,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_preferences_roundtrip_with_last_repo(
    settings_paths: tuple[Path, Path],
) -> None:
    assert save_preferences(7, True, 500, 25, "owner/repo")
    assert load_preferences() == (
        7,
        True,
        500,
        25,
        "owner/repo",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_preferences_roundtrip_with_concurrency(
    settings_paths: tuple[Path, Path],
) -> None:
    assert save_preferences(7, True, 500, 25, "owner/repo", 8)
    assert load_preferences() == (
        7,
        True,
        500,
        25,
        "owner/repo",
        8,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_preferences_roundtrip_with_timeouts(
    settings_paths: tuple[Path, Path],
) -> None:
    assert save_preferences(7, True, 500, 25, "owner/repo", 8, 120, 90, 45)
    assert load_preferences() == (7, True, 500, 25, "owner/repo", 8, 120, 90, 45, False)


def test_preferences_roundtrip_uses_defaults_for_new_fields(
    settings_paths: tuple[Path, Path],
) -> None:
    # Old 2-arg callers still work; new fields fall back to defaults.
    assert save_preferences(7, True)
    assert load_preferences() == (
        7,
        True,
        FETCH_LIMIT_DEFAULT,
        0,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_load_preferences_corrupt_json(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, "nope")
    assert load_preferences() == (
        2,
        False,
        FETCH_LIMIT_DEFAULT,
        0,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )


def test_load_preferences_rejects_bad_values(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    default = (
        2,
        False,
        FETCH_LIMIT_DEFAULT,
        0,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )
    _write(
        prefs_path,
        json.dumps({"keep": 0, "failed_only": "yes"}),
    )
    assert load_preferences() == default
    _write(
        prefs_path,
        json.dumps({"keep": 1000, "failed_only": True}),
    )
    assert load_preferences() == (2, True, *default[2:])
    _write(prefs_path, json.dumps({"keep": True}))
    assert load_preferences() == default
    # Out-of-range or wrongly-typed new fields fall back to defaults.
    _write(
        prefs_path,
        json.dumps(
            {
                "fetch_limit": FETCH_LIMIT_MAX + 1,
                "max_deletions": -5,
            }
        ),
    )
    assert load_preferences() == default
    _write(
        prefs_path,
        json.dumps({"fetch_limit": "lots", "max_deletions": 3.5}),
    )
    assert load_preferences() == default


def test_load_preferences_rejects_invalid_concurrency(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, json.dumps({"concurrency": 0}))
    assert load_preferences()[5] == CONCURRENCY_DEFAULT
    _write(prefs_path, json.dumps({"concurrency": 100}))
    assert load_preferences()[5] == CONCURRENCY_DEFAULT
    _write(prefs_path, json.dumps({"concurrency": "lots"}))
    assert load_preferences()[5] == CONCURRENCY_DEFAULT
    # Boundary values are accepted.
    _write(prefs_path, json.dumps({"concurrency": CONCURRENCY_MIN}))
    assert load_preferences()[5] == CONCURRENCY_MIN
    _write(prefs_path, json.dumps({"concurrency": CONCURRENCY_MAX}))
    assert load_preferences()[5] == CONCURRENCY_MAX


def test_load_preferences_rejects_invalid_timeouts(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    # Out-of-range timeouts fall back to defaults.
    _write(prefs_path, json.dumps({"list_timeout": 2}))
    assert load_preferences()[6] == TIMEOUT_LIST_DEFAULT
    _write(prefs_path, json.dumps({"list_timeout": 9999}))
    assert load_preferences()[6] == TIMEOUT_LIST_DEFAULT
    _write(prefs_path, json.dumps({"list_timeout": "lots"}))
    assert load_preferences()[6] == TIMEOUT_LIST_DEFAULT
    # Boundary values are accepted.
    _write(prefs_path, json.dumps({"list_timeout": TIMEOUT_MIN}))
    assert load_preferences()[6] == TIMEOUT_MIN
    _write(prefs_path, json.dumps({"list_timeout": TIMEOUT_MAX}))
    assert load_preferences()[6] == TIMEOUT_MAX


def test_load_preferences_accepts_valid_new_fields(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(
        prefs_path,
        json.dumps(
            {
                "keep": 3,
                "failed_only": False,
                "fetch_limit": FETCH_LIMIT_MAX,
                "max_deletions": MAX_DELETIONS_MAX,
            }
        ),
    )
    assert load_preferences() == (
        3,
        False,
        FETCH_LIMIT_MAX,
        MAX_DELETIONS_MAX,
        "",
        CONCURRENCY_DEFAULT,
        TIMEOUT_LIST_DEFAULT,
        TIMEOUT_DELETE_DEFAULT,
        TIMEOUT_AUTH_DEFAULT,
        False,
    )
    # 0 deletions and the minimum fetch limit are both accepted.
    _write(prefs_path, json.dumps({"fetch_limit": FETCH_LIMIT_MIN}))
    assert load_preferences()[2] == FETCH_LIMIT_MIN


def test_load_preferences_rejects_invalid_last_repo(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, json.dumps({"last_repo": "not-a-valid-repo"}))
    assert load_preferences()[4] == ""
    _write(prefs_path, json.dumps({"last_repo": 12345}))
    assert load_preferences()[4] == ""
    _write(prefs_path, json.dumps({"last_repo": "owner/repo"}))
    assert load_preferences()[4] == "owner/repo"


def test_save_preferences_failure_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "prefs-as-dir"
    target.mkdir()
    monkeypatch.setattr("app.settings.PREFERENCES_PATH", str(target))
    assert not save_preferences(2, False)


# --- export_settings / import_settings --------------------------------------


def test_export_settings_writes_json(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """export_settings writes a valid JSON file with repos and preferences."""
    _repos_path, _prefs_path = settings_paths
    # Set up some data
    save_repos(["owner/repo1", "owner/repo2"])
    save_preferences(5, True, 500, 100, "owner/repo1", 4, 120, 60, 30, True)

    export_path = tmp_path / "export.json"
    assert export_settings(str(export_path))

    import json

    data = json.loads(export_path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["repos"] == ["owner/repo1", "owner/repo2"]
    prefs = data["preferences"]
    assert prefs["keep"] == 5
    assert prefs["failed_only"] is True
    assert prefs["fetch_limit"] == 500
    assert prefs["max_deletions"] == 100
    assert prefs["last_repo"] == "owner/repo1"
    assert prefs["concurrency"] == 4
    assert prefs["list_timeout"] == 120
    assert prefs["delete_timeout"] == 60
    assert prefs["auth_timeout"] == 30
    assert prefs["dark_mode"] is True


def test_export_settings_failure_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """export_settings returns False on I/O failure."""
    target = tmp_path / "export-dir"
    target.mkdir()
    monkeypatch.setattr("app.settings.SETTINGS_PATH", str(tmp_path / "repos.json"))
    monkeypatch.setattr("app.settings.PREFERENCES_PATH", str(tmp_path / "prefs.json"))
    assert not export_settings(str(target))


def test_import_settings_valid_file(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings reads a valid export file and returns (repos, prefs)."""
    import json

    export_path = tmp_path / "import.json"
    export_data = {
        "version": 1,
        "repos": ["imported/repo1", "imported/repo2"],
        "preferences": {
            "keep": 3,
            "failed_only": True,
            "fetch_limit": 200,
            "max_deletions": 50,
            "last_repo": "imported/repo1",
            "concurrency": 2,
            "list_timeout": 90,
            "delete_timeout": 45,
            "auth_timeout": 20,
            "dark_mode": True,
        },
    }
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = import_settings(str(export_path))
    assert result is not None
    repos, prefs = result
    assert repos == ["imported/repo1", "imported/repo2"]
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
        dark_mode,
    ) = prefs
    assert keep == 3
    assert failed_only is True
    assert fetch_limit == 200
    assert max_deletions == 50
    assert last_repo == "imported/repo1"
    assert concurrency == 2
    assert list_timeout == 90
    assert delete_timeout == 45
    assert auth_timeout == 20
    assert dark_mode is True


def test_import_settings_missing_file_returns_none(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings returns None for non-existent file."""
    result = import_settings(str(tmp_path / "nonexistent.json"))
    assert result is None


def test_import_settings_corrupt_json_returns_none(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings returns None for corrupt JSON."""
    export_path = tmp_path / "corrupt.json"
    export_path.write_text("{not json", encoding="utf-8")
    result = import_settings(str(export_path))
    assert result is None


def test_import_settings_wrong_version_returns_none(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings returns None for wrong version."""
    import json

    export_path = tmp_path / "wrong_version.json"
    export_path.write_text(
        json.dumps({"version": 2, "repos": [], "preferences": {}}), encoding="utf-8"
    )
    result = import_settings(str(export_path))
    assert result is None


def test_import_settings_non_list_repos_returns_none(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings returns None when repos is not a list."""
    import json

    export_path = tmp_path / "bad_repos.json"
    export_path.write_text(
        json.dumps({"version": 1, "repos": "not-a-list", "preferences": {}}),
        encoding="utf-8",
    )
    result = import_settings(str(export_path))
    assert result is None


def test_import_settings_non_dict_preferences_returns_none(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings returns None when preferences is not a dict."""
    import json

    export_path = tmp_path / "bad_prefs.json"
    export_path.write_text(
        json.dumps({"version": 1, "repos": [], "preferences": "not-a-dict"}),
        encoding="utf-8",
    )
    result = import_settings(str(export_path))
    assert result is None


def test_import_settings_filters_invalid_repos(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings filters out invalid repo entries."""
    import json

    export_path = tmp_path / "mixed_repos.json"
    export_data = {
        "version": 1,
        "repos": ["valid/repo", "", "also-valid/repo", 123, None, "valid/repo"],
        "preferences": {},
    }
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = import_settings(str(export_path))
    assert result is not None
    repos, _ = result
    # Should only include valid, non-empty, unique repos
    assert repos == ["valid/repo", "also-valid/repo"]


def test_import_settings_uses_defaults_for_missing_prefs(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings uses defaults for missing preference fields."""
    import json

    export_path = tmp_path / "minimal_prefs.json"
    export_path.write_text(
        json.dumps({"version": 1, "repos": ["a/b"], "preferences": {}}),
        encoding="utf-8",
    )

    result = import_settings(str(export_path))
    assert result is not None
    _, prefs = result
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
        dark_mode,
    ) = prefs
    assert keep == 2
    assert failed_only is False
    assert fetch_limit == FETCH_LIMIT_DEFAULT
    assert max_deletions == 0
    assert last_repo == ""
    assert concurrency == CONCURRENCY_DEFAULT
    assert list_timeout == TIMEOUT_LIST_DEFAULT
    assert delete_timeout == TIMEOUT_DELETE_DEFAULT
    assert auth_timeout == TIMEOUT_AUTH_DEFAULT
    assert dark_mode is False


def test_import_settings_rejects_invalid_pref_values(
    tmp_path: Path, settings_paths: tuple[Path, Path]
) -> None:
    """import_settings rejects out-of-range or wrongly-typed preference values."""
    import json

    export_path = tmp_path / "bad_prefs.json"
    export_data = {
        "version": 1,
        "repos": ["a/b"],
        "preferences": {
            "keep": 0,  # out of range
            "failed_only": "yes",  # wrong type
            "fetch_limit": FETCH_LIMIT_MAX + 1,  # out of range
            "max_deletions": -5,  # out of range
            "last_repo": "not-a-valid-repo",  # invalid
            "concurrency": 0,  # out of range
            "list_timeout": 2,  # out of range
            "delete_timeout": "lots",  # wrong type
            "auth_timeout": 9999,  # out of range
            "dark_mode": "yes",  # wrong type
        },
    }
    export_path.write_text(json.dumps(export_data), encoding="utf-8")

    result = import_settings(str(export_path))
    assert result is not None
    _, prefs = result
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
        dark_mode,
    ) = prefs
    # All should fall back to defaults
    assert keep == 2
    assert failed_only is False
    assert fetch_limit == FETCH_LIMIT_DEFAULT
    assert max_deletions == 0
    assert last_repo == ""
    assert concurrency == CONCURRENCY_DEFAULT
    assert list_timeout == TIMEOUT_LIST_DEFAULT
    assert delete_timeout == TIMEOUT_DELETE_DEFAULT
    assert auth_timeout == TIMEOUT_AUTH_DEFAULT
    assert dark_mode is False
