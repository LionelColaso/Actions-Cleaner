"""GUI tests for MainWindow (runs offscreen, no real gh calls)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

import actions_cleaner_gui as app

Calls = dict[str, list[tuple[object, ...]]]


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Capture QMessageBox calls instead of blocking on modal dialogs."""
    calls: Calls = {"warning": [], "information": [], "critical": []}

    def _make(kind: str) -> Callable[..., None]:
        def _record(*args: object, **kwargs: object) -> None:
            calls[kind].append(args)

        return _record

    monkeypatch.setattr(QMessageBox, "warning", _make("warning"))
    monkeypatch.setattr(QMessageBox, "information", _make("information"))
    monkeypatch.setattr(QMessageBox, "critical", _make("critical"))
    return calls


def _which_ok(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _which_missing(name: str) -> str | None:
    return None


class _InstantWorker(app.CleanupWorker):
    """Worker whose run() does no I/O — safe for lifecycle tests."""

    def run(self) -> None:
        self.log_signal.emit("fake run")
        self.summary_signal.emit(1, 0, 0)
        self.finished_signal.emit(0)


class _RunningWorker(app.CleanupWorker):
    """Worker that always claims to be running (for closeEvent tests)."""

    def isRunning(self) -> bool:
        return True


# --- startup safety --------------------------------------------------------


def test_window_starts_in_safe_dry_run_mode(dialogs: Calls) -> None:
    window = app.MainWindow()
    assert window.dry_run_check.isChecked()
    assert window.keep_spin.value() == 2
    assert not window.cleanup_btn.isEnabled()  # no repo entered yet
    assert window.progress_bar.isHidden()
    window.close()


def test_start_cleanup_rejects_empty_repo(dialogs: Calls) -> None:
    window = app.MainWindow()
    window.start_cleanup()
    assert len(dialogs["warning"]) == 1
    assert window.worker is None
    window.close()


def test_start_cleanup_rejects_malformed_repo(dialogs: Calls) -> None:
    window = app.MainWindow()
    window.repo_input.setText("not-a-repo")
    window.start_cleanup()
    assert len(dialogs["warning"]) == 1
    assert window.worker is None
    window.close()


def test_start_cleanup_requires_gh_on_path(
    monkeypatch: pytest.MonkeyPatch, dialogs: Calls
) -> None:
    monkeypatch.setattr(shutil, "which", _which_missing)
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.start_cleanup()
    assert dialogs["warning"]
    assert "gh" in str(dialogs["warning"][0])
    assert window.worker is None
    window.close()


def test_cleanup_button_tracks_repo_input(dialogs: Calls) -> None:
    window = app.MainWindow()
    assert not window.cleanup_btn.isEnabled()
    window.repo_input.setText("owner/repo")
    assert window.cleanup_btn.isEnabled()
    window.repo_input.setText("   ")
    assert not window.cleanup_btn.isEnabled()
    window.close()


# --- repository management -------------------------------------------------


def test_add_and_remove_repo_roundtrip(
    dialogs: Calls, settings_paths: tuple[Path, Path]
) -> None:
    repos_path, _ = settings_paths
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.add_repo()
    assert window.repo_combo.count() == 1
    assert json.loads(repos_path.read_text(encoding="utf-8")) == ["owner/repo"]

    window.remove_repo()
    assert window.repo_combo.count() == 0
    assert window.repo_input.text() == ""
    assert json.loads(repos_path.read_text(encoding="utf-8")) == []
    window.close()


def test_add_repo_rejects_invalid(dialogs: Calls) -> None:
    window = app.MainWindow()
    window.repo_input.setText("garbage")
    window.add_repo()
    assert dialogs["warning"]
    assert window.repo_combo.count() == 0
    window.close()


def test_add_repo_rejects_duplicates(dialogs: Calls) -> None:
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.add_repo()
    window.add_repo()
    assert dialogs["information"]
    assert window.repo_combo.count() == 1
    window.close()


def test_remove_unknown_repo_reports_info(dialogs: Calls) -> None:
    window = app.MainWindow()
    window.repo_input.setText("unknown/repo")
    window.remove_repo()
    assert dialogs["information"]
    window.close()


# --- GitHub URL input ------------------------------------------------------


def test_add_repo_accepts_github_url(
    dialogs: Calls, settings_paths: tuple[Path, Path]
) -> None:
    repos_path, _ = settings_paths
    window = app.MainWindow()
    window.repo_input.setText("https://github.com/owner/repo/actions")
    window.add_repo()
    assert window.repo_combo.count() == 1
    assert window.repo_input.text() == "owner/repo"  # canonical form shown
    assert json.loads(repos_path.read_text(encoding="utf-8")) == ["owner/repo"]

    # the plain slug afterwards is recognized as the same repository
    window.repo_input.setText("owner/repo")
    window.add_repo()
    assert window.repo_combo.count() == 1
    assert dialogs["information"]
    window.close()


def test_remove_repo_accepts_github_url(
    dialogs: Calls, settings_paths: tuple[Path, Path]
) -> None:
    repos_path, _ = settings_paths
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.add_repo()
    window.repo_input.setText("https://github.com/owner/repo")
    window.remove_repo()
    assert window.repo_combo.count() == 0
    assert json.loads(repos_path.read_text(encoding="utf-8")) == []
    window.close()


def test_start_cleanup_accepts_github_url(
    monkeypatch: pytest.MonkeyPatch, dialogs: Calls
) -> None:
    monkeypatch.setattr(shutil, "which", _which_missing)
    window = app.MainWindow()
    window.repo_input.setText("https://github.com/owner/repo")
    window.start_cleanup()
    # The URL passed validation; the run stops only at the missing gh CLI.
    assert dialogs["warning"]
    assert "gh" in str(dialogs["warning"][0])
    assert window.repo_input.text() == "owner/repo"  # normalized in place
    assert window.worker is None
    window.close()


def test_repo_combo_reload_preserves_typed_text(
    settings_paths: tuple[Path, Path],
) -> None:
    repos_path, _ = settings_paths
    repos_path.write_text(json.dumps(["a/one", "b/two"]), encoding="utf-8")
    window = app.MainWindow()
    window.repo_input.setText("typed/three")
    window.load_repo_list()
    assert window.repo_input.text() == "typed/three"
    assert window.repo_combo.count() == 2
    window.close()


def test_on_repo_selected_ignores_empty_text() -> None:
    window = app.MainWindow()
    window.repo_input.setText("keep/me")
    window.on_repo_selected("")
    assert window.repo_input.text() == "keep/me"
    window.on_repo_selected("other/repo")
    assert window.repo_input.text() == "other/repo"
    window.close()


# --- completion states -----------------------------------------------------


def test_finished_resets_ui_and_reports_dry_run_success(
    dialogs: Calls,
) -> None:
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.cleanup_btn.setEnabled(False)
    window.progress_bar.setVisible(True)
    window.worker = app.CleanupWorker("owner/repo", 2, True)
    window._last_dry_run = True
    window._last_summary = (5, 3, 0)

    window.on_cleanup_finished(0)

    assert window.progress_bar.isHidden()
    assert not window.summary_label.isHidden()
    assert "kept 5" in window.summary_label.text()
    assert "Dry run" in window.summary_label.text()
    assert len(dialogs["information"]) == 1
    assert not dialogs["critical"]
    window.worker = None
    window.close()


def test_finished_failure_reports_critical(dialogs: Calls) -> None:
    window = app.MainWindow()
    window._last_dry_run = False
    window._last_summary = (2, 1, 1)

    window.on_cleanup_finished(1)

    assert dialogs["critical"]
    assert "Failed" in window.summary_label.text()
    window.close()


def test_finished_cancel_reports_information(dialogs: Calls) -> None:
    window = app.MainWindow()
    window._last_dry_run = False
    window._last_summary = (4, 2, 0)

    window.on_cleanup_finished(2)

    assert dialogs["information"]
    assert "Cancelled" in window.summary_label.text()
    window.close()


def test_finished_suppresses_dialogs_while_closing(
    dialogs: Calls,
) -> None:
    window = app.MainWindow()
    window._close_pending = True

    window.on_cleanup_finished(0)

    assert not dialogs["information"]
    assert not dialogs["critical"]
    window.close()


def test_thread_finished_clears_worker_and_reenables(
    dialogs: Calls,
) -> None:
    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.worker = app.CleanupWorker("owner/repo", 2, True)
    window.cleanup_btn.setEnabled(False)

    window.on_thread_finished()

    assert window.worker is None
    assert window.cleanup_btn.isEnabled()
    window.close()


def test_close_while_worker_running_defers_and_cancels() -> None:
    window = app.MainWindow()
    worker = _RunningWorker("owner/repo", 2, True)
    window.worker = worker

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()
    assert worker._cancelled
    assert window._close_pending
    # Allow the (never started) worker to be torn down so the window can close.
    window.worker = None
    window.close()


# --- full lifecycle with a fake worker -------------------------------------


def test_start_cleanup_full_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    dialogs: Calls,
    settings_paths: tuple[Path, Path],
) -> None:
    _, prefs_path = settings_paths
    monkeypatch.setattr(shutil, "which", _which_ok)
    monkeypatch.setattr(app, "CleanupWorker", _InstantWorker)

    window = app.MainWindow()
    window.repo_input.setText("owner/repo")
    window.keep_spin.setValue(7)
    window.start_cleanup()

    worker = window.worker
    assert worker is not None
    assert not window.cleanup_btn.isEnabled()  # busy while running
    assert worker.wait(2000)
    for _ in range(3):
        QApplication.processEvents()

    assert window.worker is None
    assert window.cleanup_btn.isEnabled()
    assert window.progress_bar.isHidden()
    assert not window.summary_label.isHidden()
    assert len(dialogs["information"]) == 1
    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert prefs == {"keep": 7, "failed_only": False}
    window.close()
