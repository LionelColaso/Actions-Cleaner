# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Multi-select conclusion filter in UI (success, failure, cancelled, skipped, timed_out, startup_failure, empty)
- Date range filter ("Older than N days") in UI
- Dark mode toggle with custom stylesheet, persisted in preferences
- Keyboard shortcuts: Ctrl+Enter (start), Esc (cancel), Space (pause/resume)
- Export/import settings (JSON with repos + preferences including `dark_mode`)
- "Clean All Saved Repos" button for multi-repo cleanup
- Batch `gh run delete` (multiple run IDs per call) for performance
- gh CLI rate limiting detection: auto-waits on `X-RateLimit-Remaining: 0` using `X-RateLimit-Reset` header
- Retry logic with exponential backoff (3 retries, 1s base, 30s max) for transient failures
- gh version validation at startup (warns if gh < 2.0)
- Property-based tests with Hypothesis for repo normalization, run selection, settings persistence, gh_retry
- CONTRIBUTING.md and CHANGELOG.md

### Changed
- **BREAKING**: Refactored single-file `actions_cleaner_gui.py` (~1200 lines) into modular package `app/`:
  - `worker.py` — CleanupWorker and gh CLI helpers
  - `settings.py` — Persistence (repos, preferences)
  - `repo.py` — Repo normalization/validation
  - `selection.py` — Run filtering logic
  - `ui.py` — MainWindow and UI setup
  - `icon.py` — Window/app icon
  - `gh_retry.py` — Shared retry logic for rate limiting and transient errors
  - `__main__.py` — Entry point
- Package renamed from `actions_cleaner` to `app` (all imports updated)
- Nuitka build updated to use `--python-flag=-m app`
- Dry-run checkbox state never persisted (always defaults to checked for safety)

### Fixed
- Type safety: strict mypy + pyright pass with zero errors
- Linting: ruff clean with zero warnings
- jscpd duplication reduced to 3.30% (threshold 5%)

## [0.1.0] - 2026-XX-XX

### Added
- Initial release
- Core cleanup logic: fetch runs, filter by latest N commits, delete others
- PySide6 GUI with progress bar, pause/cancel, dry-run mode
- Repository persistence (JSON at `~/.actions-cleaner-repos.json`)
- Preferences persistence (JSON at `~/.actions-cleaner-settings.json`)
- gh CLI integration for auth, list, delete
- Worker thread off GUI thread for all subprocess calls
- Nuitka standalone build
- CI pipeline: lint (ruff), typecheck (mypy, pyright), test (pytest), build (all OS), release

---

## Release Process

Releases are created via GitHub Actions workflow `release.yml` (manual dispatch):

1. Set version in `pyproject.toml` and `scripts/build.py` (`--product-version`)
2. Run `just build` locally to verify
3. Push tag `vX.Y.Z` or use workflow dispatch
4. Workflow builds on Windows/macOS/Linux, creates release with artifacts