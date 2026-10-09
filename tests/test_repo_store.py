"""Tests for repository/preference persistence and input validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import actions_cleaner_gui as app


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# --- is_valid_repo ---------------------------------------------------------


def test_is_valid_repo_accepts_slugs() -> None:
    assert app.is_valid_repo("owner/repo")
    assert app.is_valid_repo("some-org/my.repo_2")
    assert app.is_valid_repo("a.b-c/d_e.f")


def test_is_valid_repo_rejects_malformed() -> None:
    assert not app.is_valid_repo("")
    assert not app.is_valid_repo("noslash")
    assert not app.is_valid_repo("a/b/c")
    assert not app.is_valid_repo("owner/repo extra")
    assert not app.is_valid_repo("owner/repo; rm -rf /")


# --- normalize_repo --------------------------------------------------------


def test_normalize_repo_passes_slugs_through() -> None:
    assert app.normalize_repo("owner/repo") == "owner/repo"
    assert app.normalize_repo("  owner/repo  ") == "owner/repo"


def test_normalize_repo_extracts_https_url() -> None:
    url = "https://github.com/LionelColaso/llama_gui"
    assert app.normalize_repo(url) == "LionelColaso/llama_gui"
    assert app.is_valid_repo(app.normalize_repo(url))


def test_normalize_repo_handles_url_variants() -> None:
    assert app.normalize_repo("http://github.com/o/r") == "o/r"
    assert app.normalize_repo("www.github.com/o/r") == "o/r"
    assert app.normalize_repo("github.com/o/r") == "o/r"
    assert app.normalize_repo("GitHub.com/o/r") == "o/r"
    assert app.normalize_repo("https://github.com/o/r/") == "o/r"
    assert app.normalize_repo("https://github.com/o/r.git") == "o/r"
    assert app.normalize_repo("https://github.com/o/r/actions/runs/42") == "o/r"
    assert app.normalize_repo("https://github.com/o/r?tab=readme") == "o/r"
    assert app.normalize_repo("https://github.com/o/r/pull/7") == "o/r"


def test_normalize_repo_handles_ssh_remote() -> None:
    assert app.normalize_repo("git@github.com:o/r.git") == "o/r"
    assert app.normalize_repo("git@github.com:o/r") == "o/r"


def test_normalize_repo_leaves_foreign_input_alone() -> None:
    foreign = "https://gitlab.com/o/r"
    assert app.normalize_repo(foreign) == foreign
    assert not app.is_valid_repo(app.normalize_repo(foreign))
    only_owner = "https://github.com/onlyowner"
    assert app.normalize_repo(only_owner) == only_owner
    assert not app.is_valid_repo(app.normalize_repo(only_owner))
    assert app.normalize_repo("") == ""


# --- load_repos ------------------------------------------------------------


def test_load_repos_missing_file(settings_paths: tuple[Path, Path]) -> None:
    assert app.load_repos() == []


def test_load_repos_corrupt_json(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, "{not json")
    assert app.load_repos() == []


def test_load_repos_bare_list(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '["a/one", "b/two"]')
    assert app.load_repos() == ["a/one", "b/two"]


def test_load_repos_legacy_dict_format(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '{"repos": ["legacy/repo"]}')
    assert app.load_repos() == ["legacy/repo"]


def test_load_repos_filters_junk_and_duplicates(
    settings_paths: tuple[Path, Path],
) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, json.dumps([1, "", "a/b", "a/b", "c/d", None]))
    assert app.load_repos() == ["a/b", "c/d"]


def test_load_repos_non_list_root(settings_paths: tuple[Path, Path]) -> None:
    repos_path, _ = settings_paths
    _write(repos_path, '"just a string"')
    assert app.load_repos() == []


# --- save_repos ------------------------------------------------------------


def test_save_repos_writes_bare_json_array(
    settings_paths: tuple[Path, Path],
) -> None:
    repos_path, _ = settings_paths
    assert app.save_repos(["x/y", "z/w"])
    data = json.loads(repos_path.read_text(encoding="utf-8"))
    assert data == ["x/y", "z/w"]


def test_save_repos_failure_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "as-directory"
    target.mkdir()
    monkeypatch.setattr(app, "SETTINGS_PATH", str(target))
    assert not app.save_repos(["x/y"])


# --- preferences -----------------------------------------------------------


def test_load_preferences_defaults(
    settings_paths: tuple[Path, Path],
) -> None:
    assert app.load_preferences() == (
        2, False, app.FETCH_LIMIT_DEFAULT, 0, "", app.CONCURRENCY_DEFAULT
    )


def test_preferences_roundtrip(settings_paths: tuple[Path, Path]) -> None:
    assert app.save_preferences(7, True, 500, 25)
    assert app.load_preferences() == (
        7, True, 500, 25, "", app.CONCURRENCY_DEFAULT
    )


def test_preferences_roundtrip_with_last_repo(
    settings_paths: tuple[Path, Path],
) -> None:
    assert app.save_preferences(7, True, 500, 25, "owner/repo")
    assert app.load_preferences() == (
        7, True, 500, 25, "owner/repo", app.CONCURRENCY_DEFAULT
    )


def test_preferences_roundtrip_with_concurrency(
    settings_paths: tuple[Path, Path],
) -> None:
    assert app.save_preferences(7, True, 500, 25, "owner/repo", 8)
    assert app.load_preferences() == (7, True, 500, 25, "owner/repo", 8)


def test_preferences_roundtrip_uses_defaults_for_new_fields(
    settings_paths: tuple[Path, Path],
) -> None:
    # Old 2-arg callers still work; new fields fall back to defaults.
    assert app.save_preferences(7, True)
    assert app.load_preferences() == (
        7,
        True,
        app.FETCH_LIMIT_DEFAULT,
        0,
        "",
        app.CONCURRENCY_DEFAULT,
    )


def test_load_preferences_corrupt_json(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, "nope")
    assert app.load_preferences() == (
        2, False, app.FETCH_LIMIT_DEFAULT, 0, "", app.CONCURRENCY_DEFAULT
    )


def test_load_preferences_rejects_bad_values(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    default = (2, False, app.FETCH_LIMIT_DEFAULT, 0, "", app.CONCURRENCY_DEFAULT)
    _write(
        prefs_path,
        json.dumps({"keep": 0, "failed_only": "yes"}),
    )
    assert app.load_preferences() == default
    _write(
        prefs_path,
        json.dumps({"keep": 1000, "failed_only": True}),
    )
    assert app.load_preferences() == (2, True, *default[2:])
    _write(prefs_path, json.dumps({"keep": True}))
    assert app.load_preferences() == default
    # Out-of-range or wrongly-typed new fields fall back to defaults.
    _write(
        prefs_path,
        json.dumps(
            {
                "fetch_limit": app.FETCH_LIMIT_MAX + 1,
                "max_deletions": -5,
            }
        ),
    )
    assert app.load_preferences() == default
    _write(
        prefs_path,
        json.dumps({"fetch_limit": "lots", "max_deletions": 3.5}),
    )
    assert app.load_preferences() == default


def test_load_preferences_rejects_invalid_concurrency(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, json.dumps({"concurrency": 0}))
    assert app.load_preferences()[5] == app.CONCURRENCY_DEFAULT
    _write(prefs_path, json.dumps({"concurrency": 100}))
    assert app.load_preferences()[5] == app.CONCURRENCY_DEFAULT
    _write(prefs_path, json.dumps({"concurrency": "lots"}))
    assert app.load_preferences()[5] == app.CONCURRENCY_DEFAULT
    # Boundary values are accepted.
    _write(prefs_path, json.dumps({"concurrency": app.CONCURRENCY_MIN}))
    assert app.load_preferences()[5] == app.CONCURRENCY_MIN
    _write(prefs_path, json.dumps({"concurrency": app.CONCURRENCY_MAX}))
    assert app.load_preferences()[5] == app.CONCURRENCY_MAX


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
                "fetch_limit": app.FETCH_LIMIT_MAX,
                "max_deletions": app.MAX_DELETIONS_MAX,
            }
        ),
    )
    assert app.load_preferences() == (
        3,
        False,
        app.FETCH_LIMIT_MAX,
        app.MAX_DELETIONS_MAX,
        "",
        app.CONCURRENCY_DEFAULT,
    )
    # 0 deletions and the minimum fetch limit are both accepted.
    _write(prefs_path, json.dumps({"fetch_limit": app.FETCH_LIMIT_MIN}))
    assert app.load_preferences()[2] == app.FETCH_LIMIT_MIN


def test_load_preferences_rejects_invalid_last_repo(
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    _write(prefs_path, json.dumps({"last_repo": "not-a-valid-repo"}))
    assert app.load_preferences()[4] == ""
    _write(prefs_path, json.dumps({"last_repo": 12345}))
    assert app.load_preferences()[4] == ""
    _write(prefs_path, json.dumps({"last_repo": "owner/repo"}))
    assert app.load_preferences()[4] == "owner/repo"


def test_save_preferences_failure_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "prefs-as-dir"
    target.mkdir()
    monkeypatch.setattr(app, "PREFERENCES_PATH", str(target))
    assert not app.save_preferences(2, False)
