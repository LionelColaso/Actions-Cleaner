"""Shared fixtures for the actions-cleaner test suite."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

import actions_cleaner_gui as app


@pytest.fixture(scope="session", autouse=True)
def _qapp() -> Iterator[QApplication]:
    """Provide a single QApplication for the whole session."""
    existing = QApplication.instance()
    if existing is None:
        yield QApplication([])
    else:
        yield cast(QApplication, existing)


@pytest.fixture(autouse=True)
def settings_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    """Point the app at per-test settings files."""
    repos = tmp_path / "repos.json"
    prefs = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_PATH", str(repos))
    monkeypatch.setattr(app, "PREFERENCES_PATH", str(prefs))
    return repos, prefs


@pytest.fixture
def make_run() -> Callable[..., app.RunInfo]:
    """Factory for `RunInfo` records used across tests."""

    def _make(
        run_id: int,
        sha: str,
        created: str,
        conclusion: str = "success",
    ) -> app.RunInfo:
        return app.RunInfo(
            databaseId=run_id,
            headSha=sha,
            createdAt=created,
            conclusion=conclusion,
        )

    return _make
