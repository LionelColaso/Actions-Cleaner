"""Main GUI window for Actions Cleaner."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .icon import load_app_icon
from .repo import is_valid_repo, normalize_repo
from .selection import ALL_CONCLUSIONS
from .settings import (
    CONCURRENCY_DEFAULT,
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    FETCH_LIMIT_DEFAULT,
    FETCH_LIMIT_MAX,
    FETCH_LIMIT_MIN,
    MAX_DELETIONS_MAX,
    MAX_DELETIONS_MIN,
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
from .worker import CleanupWorker

VERSION = "0.1.0"
LOG_MAX_BLOCKS = 5000


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"GitHub Actions Cleaner {VERSION}")
        self.setWindowIcon(load_app_icon())
        self.worker: CleanupWorker | None = None
        self._close_pending = False
        self._cancelled = False
        self._repos: list[str] = load_repos()
        self._run_started = 0.0
        self._last_dry_run = True
        self._last_summary = (0, 0, 0)
        self.setup_ui()
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
        self._apply_preferences(
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
        self._refresh_cleanup_enabled()

    def _apply_preferences(
        self,
        keep: int,
        failed_only: bool,
        fetch_limit: int,
        max_deletions: int,
        last_repo: str,
        concurrency: int,
        list_timeout: int,
        delete_timeout: int,
        auth_timeout: int,
        dark_mode: bool,
    ) -> None:
        """Apply loaded preferences to UI controls."""
        self.keep_spin.setValue(keep)
        self.failed_only_check.setChecked(failed_only)
        self.fetch_limit_spin.setValue(fetch_limit)
        self.max_deletions_spin.setValue(max_deletions)
        self.concurrency_spin.setValue(concurrency)
        self.list_timeout_spin.setValue(list_timeout)
        self.delete_timeout_spin.setValue(delete_timeout)
        self.auth_timeout_spin.setValue(auth_timeout)
        self.dark_mode_check.setChecked(dark_mode)
        if dark_mode:
            self.toggle_dark_mode(True)
        if last_repo:
            self.repo_input.setText(last_repo)

    def setup_ui(self) -> None:
        form = QGroupBox("Settings")
        form_layout = QFormLayout()

        repo_row = QHBoxLayout()
        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText(
            "owner/repo or https://github.com/owner/repo"
        )
        self.repo_input.setToolTip(
            "GitHub repository — owner/repo slug or a GitHub URL"
        )
        self.repo_input.textChanged.connect(self._refresh_cleanup_enabled)
        self.add_repo_btn = QPushButton("Add")
        self.add_repo_btn.setToolTip("Save the repository above to the list")
        self.add_repo_btn.clicked.connect(self.add_repo)
        self.remove_repo_btn = QPushButton("Remove")
        self.remove_repo_btn.setToolTip("Remove the repository above from the list")
        self.remove_repo_btn.clicked.connect(self.remove_repo)
        repo_row.addWidget(self.repo_input)
        repo_row.addWidget(self.add_repo_btn)
        repo_row.addWidget(self.remove_repo_btn)
        form_layout.addRow("Repository:", repo_row)

        self.repo_combo = QComboBox()
        self.repo_combo.setEditable(False)
        self.repo_combo.setToolTip(
            "Saved repositories — selecting one loads it above for edit/remove"
        )
        self.load_repo_list()
        form_layout.addRow("Saved repositories:", self.repo_combo)
        self.repo_combo.currentTextChanged.connect(self.on_repo_selected)

        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(1, 100)
        self.keep_spin.setValue(2)
        self.keep_spin.setToolTip("How many newest commits' runs to preserve")
        form_layout.addRow("Commits to keep:", self.keep_spin)

        self.fetch_limit_spin = QSpinBox()
        self.fetch_limit_spin.setRange(FETCH_LIMIT_MIN, FETCH_LIMIT_MAX)
        self.fetch_limit_spin.setSingleStep(100)
        self.fetch_limit_spin.setValue(FETCH_LIMIT_DEFAULT)
        self.fetch_limit_spin.setToolTip(
            "How many most-recent runs to fetch (gh paginates automatically)"
        )
        form_layout.addRow("Runs to fetch:", self.fetch_limit_spin)

        self.max_deletions_spin = QSpinBox()
        self.max_deletions_spin.setRange(MAX_DELETIONS_MIN, MAX_DELETIONS_MAX)
        self.max_deletions_spin.setSingleStep(10)
        self.max_deletions_spin.setValue(MAX_DELETIONS_MIN)
        self.max_deletions_spin.setSpecialValueText("No limit")
        self.max_deletions_spin.setToolTip(
            "Stop after deleting this many runs in one pass (0 = no limit)"
        )
        form_layout.addRow("Max deletions:", self.max_deletions_spin)

        self.older_than_spin = QSpinBox()
        self.older_than_spin.setRange(0, 3650)
        self.older_than_spin.setSingleStep(1)
        self.older_than_spin.setValue(0)
        self.older_than_spin.setSpecialValueText("No date filter")
        self.older_than_spin.setToolTip(
            "Only consider runs older than this many days (0 = disabled)"
        )
        form_layout.addRow("Older than (days):", self.older_than_spin)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(CONCURRENCY_MIN, CONCURRENCY_MAX)
        self.concurrency_spin.setValue(CONCURRENCY_DEFAULT)
        self.concurrency_spin.setToolTip(
            "Parallel `gh run delete` calls (1 = sequential; "
            "only used when there are >1 run to delete)"
        )
        form_layout.addRow("Deletion concurrency:", self.concurrency_spin)

        self.dry_run_check = QCheckBox("Dry run (don't actually delete)")
        self.dry_run_check.setChecked(True)
        form_layout.addRow("", self.dry_run_check)

        self.failed_only_check = QCheckBox("Failed / cancelled only")
        form_layout.addRow("", self.failed_only_check)

        self.conclusion_filter_widget = QListWidget()
        self.conclusion_filter_widget.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        self.conclusion_filter_widget.setMaximumHeight(120)
        self.conclusion_filter_widget.setToolTip(
            "Select which run conclusions to include (empty = all conclusions). "
            "Ctrl+click to multi-select."
        )
        for conclusion in sorted(ALL_CONCLUSIONS):
            item = QListWidgetItem(conclusion if conclusion else "(empty)")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.conclusion_filter_widget.addItem(item)
        form_layout.addRow("Conclusions filter:", self.conclusion_filter_widget)

        self.dark_mode_check = QCheckBox("Dark mode")
        self.dark_mode_check.setToolTip("Enable dark mode (requires app restart)")
        self.dark_mode_check.toggled.connect(self.toggle_dark_mode)
        form_layout.addRow("", self.dark_mode_check)

        self.workflow_input = QLineEdit()
        self.workflow_input.setPlaceholderText(
            "workflow name or ID (optional, leave blank for all)"
        )
        self.workflow_input.setToolTip(
            "Restrict cleanup to one workflow by name or ID "
            "(e.g. 'ci.yml' or '12345678-abcd-...')"
        )
        form_layout.addRow("Workflow filter:", self.workflow_input)

        form.setLayout(form_layout)

        # Timeouts live in a collapsible group so the default Settings
        # panel stays compact; power users can widen them if `gh` is slow.
        advanced = QGroupBox("Advanced (timeouts)")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QFormLayout()

        self.list_timeout_spin = QSpinBox()
        self.list_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.list_timeout_spin.setValue(TIMEOUT_LIST_DEFAULT)
        self.list_timeout_spin.setSingleStep(10)
        self.list_timeout_spin.setToolTip("Seconds before `gh run list` is cancelled")
        advanced_layout.addRow("List timeout (s):", self.list_timeout_spin)

        self.delete_timeout_spin = QSpinBox()
        self.delete_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.delete_timeout_spin.setValue(TIMEOUT_DELETE_DEFAULT)
        self.delete_timeout_spin.setSingleStep(10)
        self.delete_timeout_spin.setToolTip(
            "Seconds before each `gh run delete` is cancelled"
        )
        advanced_layout.addRow("Delete timeout (s):", self.delete_timeout_spin)

        self.auth_timeout_spin = QSpinBox()
        self.auth_timeout_spin.setRange(TIMEOUT_MIN, TIMEOUT_MAX)
        self.auth_timeout_spin.setValue(TIMEOUT_AUTH_DEFAULT)
        self.auth_timeout_spin.setSingleStep(5)
        self.auth_timeout_spin.setToolTip(
            "Seconds before `gh auth status` is cancelled"
        )
        advanced_layout.addRow("Auth timeout (s):", self.auth_timeout_spin)

        advanced.setLayout(advanced_layout)

        self.reset_btn = QPushButton("Reset to Default")
        self.reset_btn.clicked.connect(self.reset_defaults)
        self.reset_btn.setToolTip("Restore all settings to default values")

        self.export_btn = QPushButton("Export Settings")
        self.export_btn.clicked.connect(self.export_settings)
        self.export_btn.setToolTip("Export repositories and preferences to a JSON file")

        self.import_btn = QPushButton("Import Settings")
        self.import_btn.clicked.connect(self.import_settings)
        self.import_btn.setToolTip(
            "Import repositories and preferences from a JSON file"
        )

        self.cleanup_btn = QPushButton("Clean Up Actions")
        self.cleanup_btn.clicked.connect(self.start_cleanup)

        self.cleanup_all_btn = QPushButton("Clean All Saved Repos")
        self.cleanup_all_btn.clicked.connect(self.start_cleanup_all)
        self.cleanup_all_btn.setToolTip(
            "Run cleanup on all saved repositories sequentially"
        )

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_cleanup)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self.toggle_pause)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(LOG_MAX_BLOCKS)

        self.summary_label = QLabel()
        self.summary_label.setVisible(False)

        layout = QVBoxLayout()
        layout.addWidget(form)
        layout.addWidget(advanced)
        button_row = QHBoxLayout()
        button_row.addWidget(self.reset_btn)
        button_row.addWidget(self.export_btn)
        button_row.addWidget(self.import_btn)
        button_row.addWidget(self.cleanup_btn)
        button_row.addWidget(self.cleanup_all_btn)
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.pause_btn)
        layout.addLayout(button_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)
        layout.addWidget(self.summary_label)
        self.setLayout(layout)

    def _refresh_cleanup_enabled(self) -> None:
        busy = self.worker is not None
        has_repo = bool(self.repo_input.text().strip())
        has_saved_repos = bool(self._repos)
        self.cleanup_btn.setEnabled(not busy and has_repo)
        self.cleanup_all_btn.setEnabled(not busy and has_saved_repos)

    def load_repo_list(self) -> None:
        """Repopulate the combo without firing selection signals."""
        self.repo_combo.blockSignals(True)
        try:
            self.repo_combo.clear()
            self.repo_combo.addItems(self._repos)
        finally:
            self.repo_combo.blockSignals(False)

    def export_settings(self) -> None:
        """Export repositories and preferences to a JSON file."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Settings",
            "actions-cleaner-settings.json",
            "JSON Files (*.json)",
        )
        if not path:
            return
        if not export_settings(path):
            QMessageBox.critical(self, "Error", "Failed to export settings.")
            return
        QMessageBox.information(self, "Success", f"Settings exported to {path}")

    def import_settings(self) -> None:
        """Import repositories and preferences from a JSON file."""
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Settings",
            "",
            "JSON Files (*.json)",
        )
        if not path:
            return
        result = import_settings(path)
        if result is None:
            QMessageBox.critical(
                self, "Error", "Failed to import settings. Invalid file format."
            )
            return
        repos, prefs = result
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
        # Merge repos (avoid duplicates)
        for repo in repos:
            if repo not in self._repos:
                self._repos.append(repo)
        if not save_repos(self._repos):
            QMessageBox.critical(self, "Error", "Failed to save imported repositories.")
            return
        if not save_preferences(
            keep,
            failed_only,
            fetch_limit,
            max_deletions,
            last_repo,
            concurrency,
            list_timeout,
            delete_timeout,
            auth_timeout,
            self.dark_mode_check.isChecked(),
        ):
            QMessageBox.critical(self, "Error", "Failed to save imported preferences.")
            return
        self.load_repo_list()
        self._apply_preferences(
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
        QMessageBox.information(
            self,
            "Success",
            f"Settings imported from {path} ({len(repos)} repositories)",
        )

    def on_repo_selected(self, text: str) -> None:
        if text:
            self.repo_input.setText(text)

    def add_repo(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if not is_valid_repo(repo):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository as owner/repo or a GitHub URL.",
            )
            return
        if repo in self._repos:
            QMessageBox.information(self, "Info", "Repository already exists.")
            return
        self._repos.append(repo)
        if not save_repos(self._repos):
            self._repos.pop()
            QMessageBox.critical(self, "Error", "Could not write settings file")
            return
        self.repo_input.setText(repo)  # show the canonical owner/repo form
        self.load_repo_list()
        self.repo_combo.setCurrentText(repo)

    def remove_repo(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if not repo:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter or select a repository to remove.",
            )
            return
        if repo not in self._repos:
            QMessageBox.information(self, "Info", "Repository not found in saved list.")
            return
        index = self._repos.index(repo)
        self._repos.remove(repo)
        if not save_repos(self._repos):
            self._repos.insert(index, repo)
            QMessageBox.critical(self, "Error", "Could not write settings file")
            return
        self.load_repo_list()
        self.repo_input.setText(self._repos[0] if self._repos else "")

    def _validate_and_prepare_cleanup(self, repo: str | None = None) -> bool:
        """Validate preconditions for starting cleanup. Returns True if OK to proceed."""
        if repo is not None and not repo:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository as owner/repo or a GitHub URL.",
            )
            return False
        if repo is not None and not is_valid_repo(repo):
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository as owner/repo or a GitHub URL.",
            )
            return False
        if shutil.which("gh") is None:
            QMessageBox.warning(
                self,
                "Missing Dependency",
                "The `gh` CLI was not found on PATH.\n"
                "Install it from https://cli.github.com/ and run `gh auth login`.",
            )
            return False
        return self.worker is None

    def start_cleanup(self) -> None:
        repo = normalize_repo(self.repo_input.text())
        if self.repo_input.text().strip() != repo:
            self.repo_input.setText(repo)  # keep the canonical slug visible
        if not self._validate_and_prepare_cleanup(repo):
            return

        dry_run = self.dry_run_check.isChecked()
        self._init_cleanup(dry_run=dry_run, repo_for_prefs=repo)

        selected_conclusions = self._get_selected_conclusions()

        self.worker = self._create_worker(
            repo=repo,
            dry_run=dry_run,
            conclusions=selected_conclusions,
        )
        self._connect_worker_signals(
            on_summary=self.on_summary, on_completion=self.on_cleanup_finished
        )
        self.worker.start()
        self._refresh_cleanup_enabled()

    def _init_cleanup(self, dry_run: bool, repo_for_prefs: str) -> None:
        """Initialize common cleanup state and UI."""
        self._cancelled = False
        self._last_dry_run = dry_run
        self._run_started = time.monotonic()
        self._last_summary = (0, 0, 0)
        self.log_output.clear()
        self.summary_label.setVisible(False)
        if not save_preferences(
            self.keep_spin.value(),
            self.failed_only_check.isChecked(),
            self.fetch_limit_spin.value(),
            self.max_deletions_spin.value(),
            repo_for_prefs,
            self.concurrency_spin.value(),
            self.list_timeout_spin.value(),
            self.delete_timeout_spin.value(),
            self.auth_timeout_spin.value(),
            self.dark_mode_check.isChecked(),
        ):
            self.log_output.appendPlainText("Warning: could not write preferences file")

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.pause_btn.setVisible(True)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")

    def _get_selected_conclusions(self) -> set[str]:
        """Extract selected conclusions from the filter widget."""
        selected = {
            self.conclusion_filter_widget.item(i).text()
            for i in range(self.conclusion_filter_widget.count())
            if self.conclusion_filter_widget.item(i).checkState()
            == Qt.CheckState.Checked
        }
        # Convert "(empty)" back to empty string
        return {"" if c == "(empty)" else c for c in selected}

    def _create_worker(
        self,
        repo: str,
        dry_run: bool,
        conclusions: set[str],
        *,
        max_deletions: int | None = None,
        workflow: str = "",
    ) -> CleanupWorker:
        """Create a CleanupWorker with current UI settings."""
        return CleanupWorker(
            repo,
            self.keep_spin.value(),
            dry_run,
            self.failed_only_check.isChecked(),
            fetch_limit=self.fetch_limit_spin.value(),
            max_deletions=self.max_deletions_spin.value()
            if max_deletions is None
            else max_deletions,
            workflow=workflow or self.workflow_input.text(),
            concurrency=self.concurrency_spin.value(),
            list_timeout=self.list_timeout_spin.value(),
            delete_timeout=self.delete_timeout_spin.value(),
            auth_timeout=self.auth_timeout_spin.value(),
            older_than_days=self.older_than_spin.value(),
            conclusions=conclusions,
        )

    def _connect_worker_signals(
        self,
        on_summary: Callable[[int, int, int], None],
        on_completion: Callable[[int, int, int, int], None],
    ) -> None:
        """Connect worker signals to the given handlers."""
        assert self.worker is not None
        self.worker.log_signal.connect(self.log_output.appendPlainText)
        self.worker.max_signal.connect(self.progress_bar.setMaximum)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.summary_signal.connect(on_summary)
        self.worker.completion_signal.connect(on_completion)
        self.worker.finished.connect(self.on_thread_finished)

    def _reset_cleanup_ui(self) -> None:
        """Reset UI state after cleanup finishes (single or multi-repo)."""
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setEnabled(True)
        self.pause_btn.setVisible(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")
        self.progress_bar.setVisible(False)
        self._refresh_cleanup_enabled()

    def start_cleanup_all(self) -> None:
        """Start cleanup for all saved repositories sequentially."""
        if not self._repos:
            QMessageBox.information(self, "Info", "No saved repositories to clean.")
            return
        if not self._validate_and_prepare_cleanup():
            return

        dry_run = self.dry_run_check.isChecked()
        self._init_cleanup(
            dry_run=dry_run, repo_for_prefs=self._repos[0] if self._repos else ""
        )

        self._cleanup_all_repos = list(self._repos)
        self._cleanup_all_index = 0
        self._cleanup_all_dry_run = dry_run
        self._cleanup_all_totals = {"kept": 0, "deleted": 0, "failed": 0}
        self._run_next_repo_cleanup()
        self._refresh_cleanup_enabled()

    def _run_next_repo_cleanup(self) -> None:
        """Run cleanup for the next repository in the list."""
        if self._cleanup_all_index >= len(self._cleanup_all_repos) or self._cancelled:
            self._finish_cleanup_all()
            return

        repo = self._cleanup_all_repos[self._cleanup_all_index]
        self.log_output.appendPlainText(
            f"\n=== [{self._cleanup_all_index + 1}/{len(self._cleanup_all_repos)}] "
            f"Cleaning {repo} ==="
        )
        self.repo_input.setText(repo)

        selected_conclusions = self._get_selected_conclusions()

        self.worker = self._create_worker(
            repo=repo,
            dry_run=self._cleanup_all_dry_run,
            conclusions=selected_conclusions,
        )
        self._connect_worker_signals(
            on_summary=self._on_cleanup_all_summary,
            on_completion=self._on_cleanup_all_finished,
        )
        self.worker.start()

    def _on_cleanup_all_summary(self, kept: int, deleted: int, failed: int) -> None:
        self._cleanup_all_totals["kept"] += kept
        self._cleanup_all_totals["deleted"] += deleted
        self._cleanup_all_totals["failed"] += failed

    def _on_cleanup_all_finished(
        self, result: int, kept: int, deleted: int, failed: int
    ) -> None:
        """Handle completion of one repo's cleanup in the multi-repo sequence."""
        # The worker thread is finished, move to next repo
        self._cleanup_all_index += 1
        self.worker = None
        # Small delay to let UI update
        QApplication.processEvents()
        self._run_next_repo_cleanup()

    def _finish_cleanup_all(self) -> None:
        """Finalize multi-repo cleanup and show summary."""
        self._reset_cleanup_ui()

        elapsed = time.monotonic() - self._run_started if self._run_started else 0.0
        if self._cancelled:
            state = "Cancelled"
        elif self._cleanup_all_totals["failed"] > 0:
            state = "Failed"
        elif self._cleanup_all_dry_run:
            state = "Dry run"
        else:
            state = "Done"

        kept = self._cleanup_all_totals["kept"]
        deleted = self._cleanup_all_totals["deleted"]
        failed = self._cleanup_all_totals["failed"]

        self.summary_label.setText(
            f"{state} · kept {kept} · deleted {deleted} · "
            f"failed {failed} · {elapsed:.1f}s"
        )
        self.summary_label.setVisible(True)

        if self._close_pending:
            return
        if self._cancelled:
            QMessageBox.information(
                self,
                "Cancelled",
                f"Multi-repo cleanup cancelled after {elapsed:.1f}s "
                f"({deleted} deleted, {failed} failed, {kept} kept).",
            )
        elif failed > 0:
            QMessageBox.critical(
                self,
                "Error",
                f"Multi-repo cleanup finished with {failed} failure(s).\n"
                f"({deleted} deleted, {failed} failed, {kept} kept in {elapsed:.1f}s)",
            )
        else:
            if self._cleanup_all_dry_run:
                message = (
                    f"Dry run complete: would delete {deleted} run(s), "
                    f"keeping {kept} across {len(self._cleanup_all_repos)} repo(s) "
                    f"({elapsed:.1f}s)."
                )
            else:
                message = (
                    f"Multi-repo cleanup completed: deleted {deleted} run(s), "
                    f"keeping {kept} across {len(self._cleanup_all_repos)} repo(s) "
                    f"({elapsed:.1f}s)."
                )
            QMessageBox.information(self, "Success", message)

    def cancel_cleanup(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self._cancelled = True
            self.log_output.appendPlainText(
                "Cancelling — waiting for the current operation to finish..."
            )

    def toggle_pause(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            return
        pausing = not self.worker.paused
        self.worker.set_paused(pausing)
        self.pause_btn.setText("Resume" if pausing else "Pause")
        self.log_output.appendPlainText(
            "Paused — click Resume to continue." if pausing else "Resuming..."
        )

    def reset_defaults(self) -> None:
        """Reset all configurable settings to their default values."""
        # Reset spin boxes and checkboxes to defaults
        self.keep_spin.setValue(2)
        self.fetch_limit_spin.setValue(FETCH_LIMIT_DEFAULT)
        self.max_deletions_spin.setValue(MAX_DELETIONS_MIN)
        self.concurrency_spin.setValue(CONCURRENCY_DEFAULT)
        self.list_timeout_spin.setValue(TIMEOUT_LIST_DEFAULT)
        self.delete_timeout_spin.setValue(TIMEOUT_DELETE_DEFAULT)
        self.auth_timeout_spin.setValue(TIMEOUT_AUTH_DEFAULT)
        self.failed_only_check.setChecked(False)

    def toggle_dark_mode(self, enabled: bool) -> None:
        """Toggle dark mode by applying/removing a stylesheet."""
        from typing import cast

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        qapp = cast(QApplication, app)
        if enabled:
            qapp.setStyleSheet(self._dark_stylesheet())
        else:
            qapp.setStyleSheet("")

    def _dark_stylesheet(self) -> str:
        """Return a dark mode stylesheet."""
        return """
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #555555;
                margin-top: 1ex;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                selection-background-color: #4a90d9;
            }
            QPushButton {
                background-color: #3c3c3c;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton:disabled {
                color: #888888;
                background-color: #2a2a2a;
            }
            QProgressBar {
                border: 1px solid #555555;
                background-color: #3c3c3c;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4a90d9;
            }
            QCheckBox {
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 13px;
                height: 13px;
            }
            QLabel {
                color: #ffffff;
            }
        """

    def on_summary(self, kept: int, deleted: int, failed: int) -> None:
        self._last_summary = (kept, deleted, failed)

    def on_cleanup_finished(
        self, result: int, kept: int, deleted: int, failed: int
    ) -> None:
        """Reset UI state and report the outcome (0 ok, 1 fail, 2 cancel)."""
        self._reset_cleanup_ui()

        elapsed = time.monotonic() - self._run_started if self._run_started else 0.0
        if self._last_dry_run:
            state = "Dry run"
        elif result == 2:
            state = "Cancelled"
        elif result != 0:
            state = "Failed"
        else:
            state = "Done"
        self.summary_label.setText(
            f"{state} · kept {kept} · deleted {deleted} · "
            f"failed {failed} · {elapsed:.1f}s"
        )
        self.summary_label.setVisible(True)

        if self._close_pending:
            return  # window is closing — don't stack dialogs on the way out
        if result == 0:
            if self._last_dry_run:
                message = (
                    f"Dry run complete: would delete {deleted} run(s), "
                    f"keeping {kept} ({elapsed:.1f}s)."
                )
            else:
                message = (
                    f"Cleanup completed: deleted {deleted} run(s), "
                    f"keeping {kept} ({elapsed:.1f}s)."
                )
            QMessageBox.information(self, "Success", message)
        elif result == 2:
            QMessageBox.information(
                self,
                "Cancelled",
                f"Cleanup cancelled after {elapsed:.1f}s ({deleted} run(s) processed).",
            )
        else:
            QMessageBox.critical(
                self,
                "Error",
                "Cleanup failed. Check the log for details.\n"
                f"({deleted} deleted, {failed} failed, {kept} kept)",
            )

    def on_thread_finished(self) -> None:
        """Worker thread fully stopped — release it and honour pending close."""
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_cleanup_enabled()
        if self._close_pending:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None and self.worker.isRunning():
            # Never destroy a running QThread; cancel and close once it ends.
            self._close_pending = True
            self.worker.cancel()
            self.log_output.appendPlainText(
                "Closing — waiting for the running cleanup to stop..."
            )
            event.ignore()
            return
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts."""
        from PySide6.QtCore import Qt

        key = event.key()
        modifiers = event.modifiers()

        # Ctrl+Enter or Ctrl+Return: Start cleanup
        if (
            key in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
            and modifiers == Qt.KeyboardModifier.ControlModifier
        ):
            if self.cleanup_btn.isEnabled():
                self.start_cleanup()
            return

        # Escape: Cancel cleanup
        if key == Qt.Key.Key_Escape:
            if self.worker is not None and self.worker.isRunning():
                self.cancel_cleanup()
            return

        # Space: Pause/Resume cleanup
        if key == Qt.Key.Key_Space:
            if self.worker is not None and self.worker.isRunning():
                self.toggle_pause()
            return

        super().keyPressEvent(event)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="actions-cleaner",
        description=(
            "Clean up GitHub Actions workflow runs by keeping only the "
            "latest N commits' runs."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"actions-cleaner {VERSION}",
    )
    return parser


def main() -> int:
    # The standalone build may run with --windows-console-mode=disable,
    # where stdio handles don't exist; fall back to devnull so --help and
    # --version still exit cleanly instead of raising.
    if sys.stdout is None:
        sys.stdout = open(  # noqa: SIM115 — must outlive main()
            os.devnull, "w", encoding="utf-8"
        )
    if sys.stderr is None:
        sys.stderr = open(  # noqa: SIM115 — must outlive main()
            os.devnull, "w", encoding="utf-8"
        )
    try:
        make_parser().parse_args()
    except OSError:
        return 0

    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(640, 520)
    window.show()
    return app.exec()
