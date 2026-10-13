# TODO — Actions-Cleaner

Remaining work items. Everything from the original audit is implemented and verified (local: ruff/mypy/pyright strict, tests, coverage; CI: green on all OS legs + Edge release); this file now tracks only what is left.

## Open

### Architecture & Code Organization

- [x] **Split single-file app into modules** — `actions_cleaner_gui.py` is ~1200 lines. Extract:
  - `worker.py` — `CleanupWorker` class and `gh` CLI helpers
  - `settings.py` — `load_repos`, `save_repos`, `load_preferences`, `save_preferences`
  - `repo.py` — `normalize_repo`, `is_valid_repo`, repo validation logic
  - `selection.py` — `select_runs_to_delete`, `filter_failed_runs`, `list_runs`
  - `ui.py` — `MainWindow` and UI setup
  - `icon.py` — `build_window_icon`, `load_app_icon`, `find_icon_file`
  - Update `scripts/build.py` to include all modules in Nuitka build

- [x] **Add `__main__.py` entry point** — Keep `actions_cleaner_gui.py` as the main entry but delegate to modules; update `scripts/build.py` accordingly

### Type Safety & Static Analysis

- [x] **Add strict mypy config** — Create `mypy.ini` with `disallow_untyped_defs = True`, `warn_return_any = True`, `strict_optional = True`
- [x] **Add pyright config** — Create `pyrightconfig.json` with `typeCheckingMode = "strict"`
- [x] **Fix any mypy/pyright warnings** — Run both tools and resolve all issues

### Testing

- [x] **Increase test coverage** — Core modules ≥92% (selection 97%, settings 99%, worker 94%, gh_retry 92%, repo 100%); overall 78% (ui.py at 57% expected for GUI)
- [x] **Add integration tests** — Mock `gh` CLI end-to-end with various auth states, rate limits, large run lists (in `test_selection.py`, `test_worker.py`)
- [x] **Add property-based tests** — Use `hypothesis` for `normalize_repo`, `select_runs_to_delete` edge cases (25 tests in `test_properties.py`)
- [x] **Add headless GUI tests** — Test `MainWindow` flows offscreen (already in `test_gui.py`, verify coverage)

### Performance & Optimization

- [x] **Batch `gh run delete` calls** — `gh run delete` supports multiple run IDs in one call (`gh run delete <id1> <id2> ...`). Implement batch deletion to reduce subprocess overhead
- [ ] **Add async/await alternative** — Consider `QThreadPool` + `QRunnable` for better Qt integration vs `ThreadPoolExecutor`
- [x] **Cache `gh auth status`** — Avoid repeated auth checks within a single session (currently checked once per run)
- [ ] **Optimize log widget** — Use `QPlainTextEdit.appendPlainText` efficiently; consider `QTextEdit` with HTML for colored output (errors red, success green)
- [ ] **Lazy-load heavy imports** — Move `ThreadPoolExecutor` import inside parallel path (already done); audit other imports

### Features & UX

- [x] **Workflow filter** — UI has `workflow_input` but it's not connected to worker (already passed, verify it works)
- [x] **Multi-repo cleanup** — Add "Clean All Saved Repos" button that iterates saved repos sequentially
- [x] **Export/Import settings** — JSON export/import for repos + preferences (backup/restore)
- [x] **Keyboard shortcuts** — `Ctrl+Enter` to start cleanup, `Esc` to cancel, `Space` to pause/resume
- [ ] **System tray integration** — Minimize to tray, run cleanup in background, notify on completion
- [x] **Dark mode support** — Respect system theme or add manual toggle (PySide6 `QApplication.setStyle`)
- [ ] **Progress persistence** — Save progress on cancel/pause; resume from checkpoint on re-run
- [x] **Filter by date range** — Add "Older than N days" filter alongside commit-based retention
- [x] **Filter by workflow conclusion** — Multi-select: success, failure, cancelled, skipped, timed_out, startup_failure
- [ ] **Show run details on hover** — Tooltip with run URL, conclusion, timestamp when hovering log entries
- [x] **Auto-detect `gh` path** — Check common install locations (`/usr/local/bin`, `%LOCALAPPDATA%\GitHub CLI`, etc.) if not on PATH

### Error Handling & Robustness

- [x] **Handle `gh` rate limiting** — Detect `X-RateLimit-Remaining: 0` in stderr; auto-wait using `X-RateLimit-Reset` header (in `gh_retry.py`)
- [x] **Retry transient failures** — Exponential backoff (3 retries, 1s base, 30s max) for network errors, 5xx, rate limits (in `gh_retry.py`)
- [x] **Validate `gh` version** — Warn if `gh` is too old (< 2.0) or missing required subcommands (in `worker.py:check_gh_version()`)
- [x] **Better error messages** — Map common `gh` error codes to actionable guidance (e.g., "run not found" → "may have been already deleted")
- [ ] **Graceful degradation** — If `gh` output format changes, log raw output and continue instead of crashing

### Build & Distribution

- [ ] **Add Windows installer** — NSIS or WiX installer for Nuitka build (start menu, uninstaller, file association)
- [ ] **Add macOS DMG** — `create-dmg` for notarized app bundle
- [ ] **Add Linux AppImage/Flatpak** — `appimage-builder` or `flatpak-builder` configs
- [ ] **Sign binaries** — Code signing for Windows/macOS (requires certificates)
- [ ] **Auto-update check** — Optional GitHub Releases poll on startup (opt-in, respects dry-run default)

### Documentation

- [ ] **Add README screenshots** — Annotated UI screenshots for each feature
- [ ] **Add CLI docs** — Document `--version`, future headless mode
- [x] **Add CONTRIBUTING.md** — Dev setup, test commands, PR guidelines
- [x] **Add CHANGELOG.md** — Keep a changelog (automated from conventional commits?)

### CI/CD

- [ ] **Add dependabot/renovate** — Auto-update Python deps and `gh` CLI in CI
- [ ] **Add release automation** — `release.yml` already exists; verify it creates signed artifacts
- [ ] **Add benchmark job** — Track `gh run list`/`delete` latency over time

---

### Done / Verified

- [x] Core cleanup logic (fetch, filter, delete)
- [x] PySide6 GUI with progress, pause/cancel, dry-run
- [x] Repository persistence (JSON)
- [x] Preferences persistence (JSON, dry-run never persisted)
- [x] `gh` CLI integration (auth, list, delete)
- [x] Worker thread off GUI thread
- [x] Nuitka standalone build
- [x] CI: lint (ruff), typecheck (mypy, pyright), test (pytest), build (all OS), release
- [x] All invariants from AGENTS.md §4 satisfied

(End of file)