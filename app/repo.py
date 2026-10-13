"""Repository validation and normalization."""

from __future__ import annotations

import re

#: GitHub slugs: alphanumerics plus `-`, `_`, `.` on both sides of the slash.
REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+")
#: `github.com/owner/repo...` with optional scheme/`www.` — first two path
#: segments only, so issue/PR/action URLs resolve to their repository.
GITHUB_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([^/\s?#]+/[^/\s?#]+)",
    re.IGNORECASE,
)
#: SSH clone remotes: `git@github.com:owner/repo[.git]`.
GITHUB_SSH_PATTERN = re.compile(
    r"^git@github\.com:([^/\s]+/[^/\s]+)$",
    re.IGNORECASE,
)


def is_valid_repo(repo: str) -> bool:
    """Return True for a plausible GitHub `owner/repo` slug."""
    return REPO_PATTERN.fullmatch(repo) is not None


def normalize_repo(value: str) -> str:
    """Return `owner/repo` from a slug, GitHub URL, or SSH remote.

    Accepts plain `owner/repo` slugs unchanged, and extracts the slug from
    forms like ``https://github.com/owner/repo`` (any scheme, optional
    ``www.``, trailing paths such as ``/actions/runs/42``, query strings,
    ``.git`` suffixes) and ``git@github.com:owner/repo.git``. Input that is
    not a GitHub remote is returned unchanged (and must then pass
    :func:`is_valid_repo`).
    """
    text = value.strip()
    match = GITHUB_URL_PATTERN.match(text) or GITHUB_SSH_PATTERN.match(text)
    if match is None:
        return text
    slug = match.group(1)
    slug = slug.removesuffix(".git")
    return slug
