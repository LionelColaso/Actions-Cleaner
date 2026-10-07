#!/usr/bin/env python3
"""Clean up GitHub Actions workflow runs with a PySide6 GUI."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def list_runs(repo: str, status: str | None = None) -> list[dict[str, str]]:
    cmd = [
        "gh",
        "run",
        "list",
        "--repo",
        repo,
        "--limit",
        "1000",
        "--json",
        "databaseId,headSha",
    ]
    if status:
        cmd.extend(["--status", status])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return list[dict[str, str]](json.loads(result.stdout))


SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".actions-cleaner-repos.json")


def load_repos() -> list[str]:
    if not os.path.exists(SETTINGS_PATH):
        return []
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [r for r in data.get("repos", []) if isinstance(r, str)]
    except json.JSONDecodeError, OSError:
        return []


def save_repos(repos: list[str]) -> None:
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"repos": repos}, f, indent=2)


class CleanupWorker(QThread):
    log_signal = Signal(str)
    max_signal = Signal(int)
    progress_signal = Signal(int)
    finished_signal = Signal(int)

    def __init__(
        self,
        repo: str,
        keep: int,
        dry_run: bool,
        failed_only: bool = False,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.keep = keep
        self.dry_run = dry_run
        self.failed_only = failed_only

    def run(self) -> None:
        try:
            self.log_signal.emit(f"Fetching workflow runs for {self.repo}...")
            status = "failure" if self.failed_only else None
            runs = list_runs(self.repo, status=status)
        except subprocess.CalledProcessError as e:
            self.log_signal.emit(f"Error fetching runs: {e.stderr or e}")
            self.finished_signal.emit(1)
            return

        if not runs:
            self.log_signal.emit("No workflow runs found.")
            self.finished_signal.emit(0)
            return

        commits: list[str] = []
        seen: set[str] = set()
        for run in runs:
            sha = run["headSha"]
            if sha not in seen:
                commits.append(sha)
                seen.add(sha)

        keep_commits = set(commits[: self.keep])
        delete_runs: list[str] = []

        for run in runs:
            if run["headSha"] not in keep_commits:
                delete_runs.append(str(run["databaseId"]))

        keep_runs = len(runs) - len(delete_runs)
        action = "Would delete" if self.dry_run else "Deleting"
        self.log_signal.emit(
            f"{action} {len(delete_runs)} runs, keeping {keep_runs} from latest {self.keep} commits."
        )

        total = len(delete_runs)
        self.max_signal.emit(total)
        if self.dry_run:
            for i, run_id in enumerate(delete_runs, 1):
                self.progress_signal.emit(i)
                self.log_signal.emit(f"  DRY-RUN: would delete run {run_id}")
        else:
            status_file = os.path.join(
                os.environ.get("TMPDIR", ""), f"actions-cleaner-status-{id(self)}.txt"
            )
            if not status_file:
                status_file = os.path.join(
                    os.environ.get("TEMP", ""), f"actions-cleaner-status-{id(self)}.txt"
                )
            lines: list[str] = []
            for run_id in delete_runs:
                lines.append(f'echo "{run_id}" > "{status_file}"')
                lines.append(f'echo "DELETING:{run_id}"')
                lines.append(
                    f"gh run delete {run_id} --repo {self.repo} || "
                    f'echo "FAILED:{run_id}:$?" >&2'
                )
            script = "\n".join(lines)
            try:
                result = subprocess.run(
                    script,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                failures: list[str] = []
                for i, line in enumerate(result.stdout.splitlines(), 1):
                    self.progress_signal.emit(i)
                    if line.startswith("DELETING:"):
                        run_id = line.split(":", 1)[1]
                        self.log_signal.emit(f"  Deleted run {run_id}")
                for line in result.stderr.splitlines():
                    if line.startswith("FAILED:"):
                        run_id = line.split(":", 2)[1]
                        failures.append(run_id)
                        self.log_signal.emit(f"  Failed to delete run {line}")
                    else:
                        self.log_signal.emit(f"  {line}")
                if failures:
                    self.log_signal.emit(
                        f"Batch complete with {len(failures)} failure(s)."
                    )
            except (OSError, subprocess.SubprocessError) as e:
                self.log_signal.emit(f"  Batch delete error: {e}")
            finally:
                try:
                    os.remove(status_file)
                except OSError:
                    pass

        self.log_signal.emit("Cleanup complete.")
        self.finished_signal.emit(0)


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("GitHub Actions Cleaner")
        self.setup_ui()
        self.worker: CleanupWorker | None = None

    def setup_ui(self) -> None:
        form = QGroupBox("Settings")
        form_layout = QFormLayout()

        repo_row = QHBoxLayout()
        self.repo_input = QLineEdit()
        self.repo_input.setPlaceholderText("e.g. LionelColaso/llama_gui")
        self.add_repo_btn = QPushButton("Add")
        self.add_repo_btn.clicked.connect(self.add_repo)
        self.remove_repo_btn = QPushButton("Remove")
        self.remove_repo_btn.clicked.connect(self.remove_repo)
        repo_row.addWidget(self.repo_input)
        repo_row.addWidget(self.add_repo_btn)
        repo_row.addWidget(self.remove_repo_btn)
        form_layout.addRow("Repository:", repo_row)

        self.repo_combo = QComboBox()
        self.repo_combo.setEditable(False)
        self.load_repo_list()
        form_layout.addRow("Saved repositories:", self.repo_combo)
        self.repo_combo.currentTextChanged.connect(self.on_repo_selected)

        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(1, 1000)
        self.keep_spin.setValue(2)
        form_layout.addRow("Runs to keep:", self.keep_spin)

        self.dry_run_check = QCheckBox("Dry run (don't actually delete)")
        self.dry_run_check.setChecked(True)
        form_layout.addRow("", self.dry_run_check)

        self.failed_only_check = QCheckBox("Failed workflows only")
        form_layout.addRow("", self.failed_only_check)

        form.setLayout(form_layout)

        self.cleanup_btn = QPushButton("Clean Up Actions")
        self.cleanup_btn.clicked.connect(self.start_cleanup)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        layout = QVBoxLayout()
        layout.addWidget(form)
        layout.addWidget(self.cleanup_btn)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)
        self.setLayout(layout)

    def load_repo_list(self) -> None:
        repos = load_repos()
        self.repo_combo.clear()
        self.repo_combo.addItems(repos)
        if repos:
            self.repo_input.setText(repos[0])

    def on_repo_selected(self, text: str) -> None:
        self.repo_input.setText(text)

    def add_repo(self) -> None:
        repo = self.repo_input.text().strip()
        if not repo:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter a repository name (e.g. owner/repo).",
            )
            return
        repos = load_repos()
        if repo in repos:
            QMessageBox.information(self, "Info", "Repository already exists.")
            return
        repos.append(repo)
        save_repos(repos)
        self.load_repo_list()
        self.repo_combo.setCurrentText(repo)

    def remove_repo(self) -> None:
        repo = self.repo_input.text().strip()
        if not repo:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Please enter or select a repository to remove.",
            )
            return
        repos = load_repos()
        if repo not in repos:
            QMessageBox.information(self, "Info", "Repository not found in saved list.")
            return
        repos.remove(repo)
        save_repos(repos)
        self.load_repo_list()
        if repos:
            self.repo_input.setText(repos[0])

    def start_cleanup(self) -> None:
        repo = self.repo_input.text().strip()
        if not repo:
            QMessageBox.warning(
                self, "Validation Error", "Please enter a repository (e.g. owner/repo)."
            )
            return

        keep = self.keep_spin.value()
        dry_run = self.dry_run_check.isChecked()
        failed_only = self.failed_only_check.isChecked()

        self.cleanup_btn.setEnabled(False)
        self.log_output.clear()
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = CleanupWorker(repo, keep, dry_run, failed_only)
        self.worker.log_signal.connect(self.log_output.append)
        self.worker.max_signal.connect(self.progress_bar.setMaximum)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_cleanup_finished)
        self.worker.start()

    def on_cleanup_finished(self, result: int) -> None:
        self.cleanup_btn.setEnabled(True)
        if result != 0:
            QMessageBox.critical(
                self, "Error", "Cleanup failed. Check the log for details."
            )
        else:
            QMessageBox.information(self, "Success", "Cleanup completed successfully.")


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: actions-cleaner [OPTIONS]")
        print("Clean up GitHub Actions workflow runs.")
        print()
        print("Options:")
        print("  -h, --help     Show this message and exit")
        print("  --version      Show the version and exit")
        return 0

    if "--version" in sys.argv:
        print("actions-cleaner 0.1.0")
        return 0

    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(600, 400)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
