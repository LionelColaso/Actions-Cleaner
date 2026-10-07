# Actions-Cleaner

A PySide6 desktop app that cleans up [GitHub Actions](https://github.com/features/actions) workflow runs by keeping only the latest N commits' worth of runs and deleting the rest. Uses the `gh` CLI for all GitHub API interactions.

## Features

- **Repository management:** Save and manage multiple repositories with a dropdown; add/remove repos locally.
- **Flexible retention:** Configure how many commits' worth of runs to keep (default: 2).
- **Failed-only filter:** Optionally target only failed workflow runs for cleanup.
- **Dry-run mode:** Preview what would be deleted before committing — safe by default.
- **Background processing:** All GitHub API calls happen off the GUI thread; progress and logs stream in real time.
- **Persistent settings:** Saved repositories are stored locally in `~/.actions-cleaner-repos.json`.

## Requirements

- Python **3.14** (via [uv](https://docs.astral.sh/uv/))
- [`gh` CLI](https://cli.github.com/) authenticated (`gh auth login`)

## Quick start

```bash
# Install dependencies
uv sync --locked --dev

# Launch the GUI
just run
# or
uv run python actions_cleaner_gui.py
```

## Usage

1. Add a repository (e.g. `owner/repo`) and click **Add**.
2. Set **Runs to keep** (how many recent commits to preserve).
3. Optionally check **Failed workflows only** to clean up failed runs exclusively.
4. Ensure **Dry run** is checked for a preview, then click **Clean Up Actions**.
5. Uncheck **Dry run** when ready to delete.

## Build

A standalone executable is produced with [Nuitka](https://nuitka.net/):

```bash
just build                 # runs checks, then builds into build/
just build-version 0.1.0.0 # set a product version
```

## Checks

```bash
just check   # ruff format, ruff lint, mypy, pyright
just fix     # auto-fix formatting + lint
just test    # pytest
just ci      # full CI simulation
```

## Documentation

- [`AGENTS.md`](AGENTS.md) — design spec and invariants.
- [`TODO.md`](TODO.md) — known bugs, gaps and planned work.

## License

MIT
