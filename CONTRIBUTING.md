# Contributing to Actions-Cleaner

Thank you for your interest in contributing! This guide covers development setup, testing, and pull request guidelines.

## Development Setup

### Prerequisites

- **Python 3.14** (see `.python-version`)
- **GitHub CLI (`gh`)** v2.0+ — required for all GitHub API interactions
- **uv** — fast Python package manager (`pip install uv`)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/<owner>/Actions-Cleaner.git
cd Actions-Cleaner

# Install dependencies
uv sync --dev

# Run the GUI in development mode
just run
# or: uv run python -m app
```

### Useful Commands

```bash
just run          # Launch GUI (dev mode)
just build        # Nuitka standalone build
just check        # Full check: ruff, mypy, pyright, jscpd
just test         # pytest with coverage
just ci           # Full local CI simulation
just clean        # Remove build artifacts
```

## Project Structure

```
Actions-Cleaner/
├── app/                    # Main package
│   ├── __main__.py         # Entry point
│   ├── worker.py           # CleanupWorker (QThread)
│   ├── settings.py         # Persistence (repos, preferences)
│   ├── repo.py             # Repo normalization/validation
│   ├── selection.py        # Run filtering logic
│   ├── ui.py               # MainWindow (PySide6)
│   ├── icon.py             # Window/app icon
│   └── gh_retry.py         # gh CLI retry logic
├── scripts/
│   ├── build.py            # Nuitka build script
│   └── clean.py            # Clean build artifacts
├── tests/                  # Pytest suite
│   ├── conftest.py         # Fixtures (QApplication, temp paths)
│   ├── test_gui.py         # MainWindow flows (offscreen)
│   ├── test_worker.py      # CleanupWorker with mocked gh
│   ├── test_selection.py   # Run selection logic
│   ├── test_repo_store.py  # Persistence tests
│   ├── test_gh_retry.py    # Retry/rate-limit tests
│   └── test_properties.py  # Hypothesis property-based tests
├── .github/workflows/      # CI/CD pipelines
├── pyproject.toml          # Project config (uv, ruff, mypy, pyright, pytest)
└── justfile                # Task runner
```

## Code Style & Quality

### Linting & Formatting

```bash
just check  # Runs all checks
```

- **ruff** — fast linter/formatter (line-length 88, double quotes)
- **mypy** — strict mode (`disallow_untyped_defs = True`)
- **pyright** — strict type checking
- **jscpd** — copy-paste detection (threshold 5%)

All checks must pass before merging.

### Type Hints

- All new code must have full type annotations
- Use `from __future__ import annotations` in new modules
- Avoid `Any` — prefer `object` or proper generics

## Testing

### Running Tests

```bash
just test              # All tests with coverage
uv run pytest          # Same, raw pytest
uv run pytest -k "not slow"  # Skip slow tests
```

### Test Categories

| File | Purpose |
|------|---------|
| `test_gui.py` | Offscreen Qt tests for MainWindow flows |
| `test_worker.py` | CleanupWorker with mocked `gh` CLI |
| `test_selection.py` | Pure run-selection logic |
| `test_repo_store.py` | Repo/preference persistence |
| `test_gh_retry.py` | Retry logic, rate-limit detection |
| `test_properties.py` | Hypothesis property-based tests |

### Writing New Tests

- **Unit tests**: Test single functions in isolation with mocks
- **Integration tests**: Mock `subprocess.run` to simulate `gh` CLI behavior
- **Property tests**: Use `@given` + `@settings(suppress_health_check=[HealthCheck.too_slow])` for hypothesis tests
- **GUI tests**: Use `qtbot` fixture; keep tests headless (offscreen)

### Coverage Target

- **Target**: ≥90% line coverage on `app/` package
- Run `just test` to see current coverage report
- Focus on uncovered branches in `ui.py`, `worker.py`, `settings.py`, `selection.py`

## Pull Request Guidelines

### Before Submitting

1. **Run `just ci`** — Full local CI simulation (lint + typecheck + test + build)
2. **Update tests** — Add tests for new functionality
3. **Update docs** — If behavior changes, update relevant docs
4. **Keep commits clean** — Atomic, descriptive commits

### PR Requirements

- [ ] All checks pass (`just ci` green)
- [ ] Coverage ≥90% on changed files
- [ ] Type hints on all new public functions
- [ ] No `jscpd` duplication >5%
- [ ] Conventional commit messages (e.g., `feat:`, `fix:`, `refactor:`)

### Commit Message Format

```
<type>(<scope>): <description>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `build`, `ci`, `perf`, `style`

Example:
```
feat(selection): add date-range filter for runs

Add "Older than N days" spinbox to UI and filter_runs_older_than()
to selection.py. Runs older than cutoff are excluded from deletion.

Closes #42
```

## Architecture Notes

### Threading Model

- **Main thread**: PySide6 GUI (`MainWindow`)
- **Worker thread**: `CleanupWorker` (extends `QThread`) — all `gh` subprocess calls
- **Signals**: Progress, logs, summary emitted via Qt signals (auto-queued to GUI thread)

### `gh` CLI Contract

Three commands only (list-form args, no `shell=True`):

1. `gh auth status` — preflight, `GH_AUTH_TIMEOUT`
2. `gh run list --repo <repo> --limit <N> --json databaseId,headSha,createdAt,conclusion` — `GH_LIST_TIMEOUT`
3. `gh run delete <run_id> --repo <repo>` — `GH_DELETE_TIMEOUT` (batched)

### Safety Invariants

- Dry-run defaults to **checked** (never persisted)
- Repo input validated before every run (`normalize_repo()`)
- No tokens stored — `gh` handles auth via keychain
- All `gh` calls off GUI thread

## Releasing

Releases are automated via `.github/workflows/release.yml` (manual dispatch). The workflow:

1. Builds on Windows/macOS/Linux
2. Creates GitHub Release with artifacts
3. Publishes to package registries (if configured)

Version is set in `pyproject.toml` and `scripts/build.py` (`--product-version`).

## Questions?

Open an issue or discussion on GitHub. For security issues, see `SECURITY.md` (if present).