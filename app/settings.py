"""Settings persistence for repositories and preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from .repo import is_valid_repo
from .selection import (
    FETCH_LIMIT_DEFAULT,
    FETCH_LIMIT_MAX,
    FETCH_LIMIT_MIN,
)

# Re-export fetch limit constants for other modules
__all__ = [
    "FETCH_LIMIT_DEFAULT",
    "FETCH_LIMIT_MAX",
    "FETCH_LIMIT_MIN",
]

#: Saved repositories — a bare, human-readable JSON array.
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".actions-cleaner-repos.json")
#: Saved preferences (`keep`, `failed_only`). Dry-run is deliberately *not*
#: persisted so the app always starts in safe dry-run mode.
PREFERENCES_PATH = os.path.join(
    os.path.expanduser("~"), ".actions-cleaner-settings.json"
)
#: Progress checkpoint for resume-after-cancel/pause.
CHECKPOINT_PATH = os.path.join(
    os.path.expanduser("~"), ".actions-cleaner-checkpoint.json"
)

#: Bounds for the user-configurable per-run deletion cap (0 = unlimited).
MAX_DELETIONS_MIN = 0
MAX_DELETIONS_MAX = 5000
#: Bounds for the user-configurable deletion concurrency (1 = sequential).
CONCURRENCY_MIN = 1
CONCURRENCY_MAX = 10
CONCURRENCY_DEFAULT = 1
#: Bounds for the user-configurable timeouts (seconds).
TIMEOUT_MIN = 10
TIMEOUT_MAX = 180
TIMEOUT_LIST_DEFAULT = 60
TIMEOUT_DELETE_DEFAULT = 30
TIMEOUT_AUTH_DEFAULT = 15


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow a decoded-JSON value to an object (``{}`` when it isn't one)."""
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return dict[str, Any]()


def load_repos() -> list[str]:
    """Load saved repositories; tolerates bare-list and legacy dict formats."""
    if not os.path.exists(SETTINGS_PATH):
        return []
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return []
    raw: Any
    if isinstance(data, dict):
        raw = _as_dict(data).get("repos", [])
    else:
        raw = data
    if not isinstance(raw, list):
        return []
    items = cast(list[object], raw)
    repos: list[str] = []
    for entry in items:
        if isinstance(entry, str) and entry and entry not in repos:
            repos.append(entry)
    return repos


def save_repos(repos: list[str]) -> bool:
    """Persist repositories as a bare JSON array; False on I/O failure."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(repos, f, indent=2)
        return True
    except OSError:
        return False


def load_preferences() -> tuple[int, bool, int, int, str, int, int, int, int, bool]:
    """Return ``(keep, failed_only, fetch_limit, max_deletions, last_repo,
    concurrency, list_timeout, delete_timeout, auth_timeout, dark_mode)``."""
    keep = 2
    failed_only = False
    fetch_limit = FETCH_LIMIT_DEFAULT
    max_deletions = MAX_DELETIONS_MIN
    last_repo = ""
    concurrency = CONCURRENCY_DEFAULT
    list_timeout = TIMEOUT_LIST_DEFAULT
    delete_timeout = TIMEOUT_DELETE_DEFAULT
    auth_timeout = TIMEOUT_AUTH_DEFAULT
    dark_mode = False
    try:
        with open(PREFERENCES_PATH, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return (
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
        )
    obj = _as_dict(data)
    raw_keep = obj.get("keep")
    if (
        isinstance(raw_keep, int)
        and not isinstance(raw_keep, bool)
        and 1 <= raw_keep <= 100
    ):
        keep = raw_keep
    failed_candidate = obj.get("failed_only")
    if isinstance(failed_candidate, bool):
        failed_only = failed_candidate
    raw_limit = obj.get("fetch_limit")
    if (
        isinstance(raw_limit, int)
        and not isinstance(raw_limit, bool)
        and FETCH_LIMIT_MIN <= raw_limit <= FETCH_LIMIT_MAX
    ):
        fetch_limit = raw_limit
    raw_max_del = obj.get("max_deletions")
    if (
        isinstance(raw_max_del, int)
        and not isinstance(raw_max_del, bool)
        and MAX_DELETIONS_MIN <= raw_max_del <= MAX_DELETIONS_MAX
    ):
        max_deletions = raw_max_del
    raw_last_repo = obj.get("last_repo")
    if isinstance(raw_last_repo, str) and is_valid_repo(raw_last_repo):
        last_repo = raw_last_repo
    raw_concurrency = obj.get("concurrency")
    if (
        isinstance(raw_concurrency, int)
        and not isinstance(raw_concurrency, bool)
        and CONCURRENCY_MIN <= raw_concurrency <= CONCURRENCY_MAX
    ):
        concurrency = raw_concurrency
    raw_list_timeout = obj.get("list_timeout")
    if (
        isinstance(raw_list_timeout, int)
        and not isinstance(raw_list_timeout, bool)
        and TIMEOUT_MIN <= raw_list_timeout <= TIMEOUT_MAX
    ):
        list_timeout = raw_list_timeout
    raw_delete_timeout = obj.get("delete_timeout")
    if (
        isinstance(raw_delete_timeout, int)
        and not isinstance(raw_delete_timeout, bool)
        and TIMEOUT_MIN <= raw_delete_timeout <= TIMEOUT_MAX
    ):
        delete_timeout = raw_delete_timeout
    raw_auth_timeout = obj.get("auth_timeout")
    if (
        isinstance(raw_auth_timeout, int)
        and not isinstance(raw_auth_timeout, bool)
        and TIMEOUT_MIN <= raw_auth_timeout <= TIMEOUT_MAX
    ):
        auth_timeout = raw_auth_timeout
    raw_dark_mode = obj.get("dark_mode")
    if isinstance(raw_dark_mode, bool):
        dark_mode = raw_dark_mode
    return (
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
    )


def save_preferences(
    keep: int,
    failed_only: bool,
    fetch_limit: int = FETCH_LIMIT_DEFAULT,
    max_deletions: int = MAX_DELETIONS_MIN,
    last_repo: str = "",
    concurrency: int = CONCURRENCY_DEFAULT,
    list_timeout: int = TIMEOUT_LIST_DEFAULT,
    delete_timeout: int = TIMEOUT_DELETE_DEFAULT,
    auth_timeout: int = TIMEOUT_AUTH_DEFAULT,
    dark_mode: bool = False,
) -> bool:
    """Persist preferences (never dry-run)."""
    try:
        with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "keep": keep,
                    "failed_only": failed_only,
                    "fetch_limit": fetch_limit,
                    "max_deletions": max_deletions,
                    "last_repo": last_repo,
                    "concurrency": concurrency,
                    "list_timeout": list_timeout,
                    "delete_timeout": delete_timeout,
                    "auth_timeout": auth_timeout,
                    "dark_mode": dark_mode,
                },
                f,
                indent=2,
            )
        return True
    except OSError:
        return False


def export_settings(path: str) -> bool:
    """Export repositories and preferences to a JSON file."""
    repos = load_repos()
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
    ) = load_preferences()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "repos": repos,
                    "preferences": {
                        "keep": keep,
                        "failed_only": failed_only,
                        "fetch_limit": fetch_limit,
                        "max_deletions": max_deletions,
                        "last_repo": last_repo,
                        "concurrency": concurrency,
                        "list_timeout": list_timeout,
                        "delete_timeout": delete_timeout,
                        "auth_timeout": auth_timeout,
                        "dark_mode": dark_mode,
                    },
                },
                f,
                indent=2,
            )
        return True
    except OSError:
        return False


def import_settings(
    path: str,
) -> tuple[list[str], tuple[int, bool, int, int, str, int, int, int, int, bool]] | None:
    """Import repositories and preferences from a JSON file.

    Returns tuple of (repos, preferences) or None on failure.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return None
    obj = _as_dict(data)
    if obj.get("version") != 1:
        return None
    raw_repos = obj.get("repos", [])
    if not isinstance(raw_repos, list):
        return None
    repos: list[str] = []
    for entry in raw_repos:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(entry, str) and entry and entry not in repos:
            repos.append(entry)
    raw_prefs = obj.get("preferences", {})
    if not isinstance(raw_prefs, dict):
        return None
    prefs_obj = _as_dict(raw_prefs)
    keep = 2
    failed_only = False
    fetch_limit = FETCH_LIMIT_DEFAULT
    max_deletions = MAX_DELETIONS_MIN
    last_repo = ""
    concurrency = CONCURRENCY_DEFAULT
    list_timeout = TIMEOUT_LIST_DEFAULT
    delete_timeout = TIMEOUT_DELETE_DEFAULT
    auth_timeout = TIMEOUT_AUTH_DEFAULT
    dark_mode = False
    raw_keep = prefs_obj.get("keep")
    if (
        isinstance(raw_keep, int)
        and not isinstance(raw_keep, bool)
        and 1 <= raw_keep <= 100
    ):
        keep = raw_keep
    failed_candidate = prefs_obj.get("failed_only")
    if isinstance(failed_candidate, bool):
        failed_only = failed_candidate
    raw_limit = prefs_obj.get("fetch_limit")
    if (
        isinstance(raw_limit, int)
        and not isinstance(raw_limit, bool)
        and FETCH_LIMIT_MIN <= raw_limit <= FETCH_LIMIT_MAX
    ):
        fetch_limit = raw_limit
    raw_max_del = prefs_obj.get("max_deletions")
    if (
        isinstance(raw_max_del, int)
        and not isinstance(raw_max_del, bool)
        and MAX_DELETIONS_MIN <= raw_max_del <= MAX_DELETIONS_MAX
    ):
        max_deletions = raw_max_del
    raw_last_repo = prefs_obj.get("last_repo")
    if isinstance(raw_last_repo, str) and is_valid_repo(raw_last_repo):
        last_repo = raw_last_repo
    raw_concurrency = prefs_obj.get("concurrency")
    if (
        isinstance(raw_concurrency, int)
        and not isinstance(raw_concurrency, bool)
        and CONCURRENCY_MIN <= raw_concurrency <= CONCURRENCY_MAX
    ):
        concurrency = raw_concurrency
    raw_list_timeout = prefs_obj.get("list_timeout")
    if (
        isinstance(raw_list_timeout, int)
        and not isinstance(raw_list_timeout, bool)
        and TIMEOUT_MIN <= raw_list_timeout <= TIMEOUT_MAX
    ):
        list_timeout = raw_list_timeout
    raw_delete_timeout = prefs_obj.get("delete_timeout")
    if (
        isinstance(raw_delete_timeout, int)
        and not isinstance(raw_delete_timeout, bool)
        and TIMEOUT_MIN <= raw_delete_timeout <= TIMEOUT_MAX
    ):
        delete_timeout = raw_delete_timeout
    raw_auth_timeout = prefs_obj.get("auth_timeout")
    if (
        isinstance(raw_auth_timeout, int)
        and not isinstance(raw_auth_timeout, bool)
        and TIMEOUT_MIN <= raw_auth_timeout <= TIMEOUT_MAX
    ):
        auth_timeout = raw_auth_timeout
    raw_dark_mode = prefs_obj.get("dark_mode")
    if isinstance(raw_dark_mode, bool):
        dark_mode = raw_dark_mode
    return (
        repos,
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
        ),
    )


# =============================================================================
# Progress checkpoint persistence (for resume after cancel/pause)
# =============================================================================


def save_checkpoint(
    repo: str,
    keep: int,
    dry_run: bool,
    failed_only: bool,
    workflow: str,
    conclusions: set[str],
    older_than_days: int,
    delete_ids: list[str],
    done: int,
    failed_ids: list[str],
    max_deletions: int,
    fetch_limit: int,
    concurrency: int,
    list_timeout: int,
    delete_timeout: int,
    auth_timeout: int,
) -> bool:
    """Save cleanup progress checkpoint for resume capability."""
    try:
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "repo": repo,
                    "keep": keep,
                    "dry_run": dry_run,
                    "failed_only": failed_only,
                    "workflow": workflow,
                    "conclusions": sorted(conclusions),
                    "older_than_days": older_than_days,
                    "delete_ids": delete_ids,
                    "done": done,
                    "failed_ids": failed_ids,
                    "max_deletions": max_deletions,
                    "fetch_limit": fetch_limit,
                    "concurrency": concurrency,
                    "list_timeout": list_timeout,
                    "delete_timeout": delete_timeout,
                    "auth_timeout": auth_timeout,
                },
                f,
                indent=2,
            )
        return True
    except OSError:
        return False


def load_checkpoint() -> dict[str, Any] | None:
    """Load cleanup progress checkpoint if valid; return None otherwise."""
    if not os.path.exists(CHECKPOINT_PATH):
        return None
    try:
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            data: Any = json.load(f)
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    # Validate required fields
    required = [
        "repo",
        "keep",
        "dry_run",
        "failed_only",
        "workflow",
        "conclusions",
        "older_than_days",
        "delete_ids",
        "done",
        "failed_ids",
        "max_deletions",
        "fetch_limit",
        "concurrency",
        "list_timeout",
        "delete_timeout",
        "auth_timeout",
    ]
    for key in required:
        if key not in data:
            return None
    return data


def clear_checkpoint() -> bool:
    """Remove the checkpoint file after successful completion or explicit clear."""
    try:
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
        return True
    except OSError:
        return False
