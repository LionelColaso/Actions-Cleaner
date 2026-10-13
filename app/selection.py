"""Run selection and filtering logic."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any, TypedDict, cast

from .repo import is_valid_repo


class RunInfo(TypedDict):
    """One workflow run as returned by `gh run list --json`."""

    databaseId: int
    headSha: str
    createdAt: str
    conclusion: str


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow a decoded-JSON value to an object (``{}`` when it isn't one)."""
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return {}


#: Run conclusions treated as "failed" by the failed-only filter.
FAILED_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "startup_failure"})

#: Granularity of one `gh run list` page; total fetch capped by `fetch_limit`.
GH_PAGE_SIZE = 100
#: Bounds for the user-configurable fetch limit (runs fetched per cleanup).
FETCH_LIMIT_MIN = 100
FETCH_LIMIT_MAX = 3000
FETCH_LIMIT_DEFAULT = 1000


def _parse_datetime(dt_str: str) -> datetime | None:
    """Parse ISO 8601 datetime string to UTC datetime."""
    if not dt_str:
        return None
    try:
        # Handle both 'Z' and '+00:00' formats
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.fromisoformat(dt_str).astimezone(UTC)
    except ValueError:
        return None


def filter_runs_older_than(runs: list[RunInfo], days: int) -> list[RunInfo]:
    """Keep only runs older than the specified number of days."""
    if days <= 0:
        return runs
    cutoff = datetime.now(UTC)
    cutoff = cutoff.replace(day=cutoff.day - days)
    filtered: list[RunInfo] = []
    for run in runs:
        created = _parse_datetime(run["createdAt"])
        if created is not None and created < cutoff:
            filtered.append(run)
    return filtered


def list_runs(
    repo: str,
    fetch_limit: int = FETCH_LIMIT_DEFAULT,
    workflow: str = "",
    timeout: int = 60,
) -> list[RunInfo]:
    """Fetch up to `fetch_limit` most-recent workflow runs for `repo`.

    ``gh run list --limit N`` means "maximum number of runs to fetch" and
    pages the REST API internally (100 runs/page), so raising the limit needs
    no manual pagination code on our side. When `workflow` is non-empty the
    ``--workflow`` flag narrows the fetch to a single workflow name or ID.
    """
    if not is_valid_repo(repo):
        raise ValueError(f"invalid repository slug: {repo}")

    limit = max(FETCH_LIMIT_MIN, min(fetch_limit, FETCH_LIMIT_MAX))
    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--limit",
        str(limit),
        "--json",
        "databaseId,headSha,createdAt,conclusion",
    ]
    if workflow:
        cmd.extend(["--workflow", workflow])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    data: Any = json.loads(result.stdout)
    if not isinstance(data, list):
        raise TypeError(
            f"unexpected gh output (expected a list, got {type(data).__name__})"
        )
    items = cast(list[dict[str, Any]], data)
    runs: list[RunInfo] = []
    for item in items:
        obj = _as_dict(item)
        if "databaseId" not in obj or "headSha" not in obj:
            continue
        runs.append(
            RunInfo(
                databaseId=int(obj["databaseId"]),
                headSha=str(obj["headSha"]),
                createdAt=str(obj.get("createdAt") or ""),
                conclusion=str(obj.get("conclusion") or ""),
            )
        )
    return runs


def filter_failed_runs(runs: list[RunInfo]) -> list[RunInfo]:
    """Keep only runs whose conclusion counts as failed."""
    return [run for run in runs if run["conclusion"] in FAILED_CONCLUSIONS]


def select_runs_to_delete(
    runs: list[RunInfo], keep: int, older_than_days: int = 0
) -> tuple[list[str], list[str]]:
    """Split runs into ``(delete, keep)`` id lists.

    Runs are ordered by ``createdAt`` (newest first). If `older_than_days` > 0,
    only runs older than that many days are considered for deletion. Then the
    newest ``keep`` distinct head SHAs — and every run belonging to them — are
    preserved.
    """
    # First, filter by date if specified
    if older_than_days > 0:
        runs = filter_runs_older_than(runs, older_than_days)

    ordered = sorted(runs, key=lambda run: run["createdAt"], reverse=True)
    newest_shas: list[str] = []
    seen: set[str] = set()
    for run in ordered:
        sha = run["headSha"]
        if sha not in seen:
            seen.add(sha)
            newest_shas.append(sha)
    keep_shas = set(newest_shas[:keep])
    delete_ids = [
        str(run["databaseId"]) for run in ordered if run["headSha"] not in keep_shas
    ]
    kept_ids = [
        str(run["databaseId"]) for run in ordered if run["headSha"] in keep_shas
    ]
    return delete_ids, kept_ids
