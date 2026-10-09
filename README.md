# Actions-Cleaner

A PySide6 desktop app that cleans up [GitHub Actions](https://github.com/features/actions) workflow runs by keeping only the latest N commits' worth of runs and deleting the rest. Uses the `gh` CLI for all GitHub API interactions.

## Features

- **Repository management:** Save and manage multiple repositories with a dropdown; add/remove repos locally (validated as `owner/repo`).
- **Flexible retention:** Configure how many commits' worth of runs to keep (default: 2); the choice is remembered.
- **Failed-only filter:** Optionally target only failed, cancelled or timed-out workflow runs for cleanup.
- **Dry-run mode:** Preview what would be deleted before committing — always on by default (and never remembered, so every launch starts safe).
- **Background processing:** All GitHub API calls happen off the GUI thread; progress and logs stream in real time, with a Cancel button and a final summary (kept / deleted / failed / elapsed).
- **Persistent settings:** Saved repositories live in `~/.actions-cleaner-repos.json` (a plain JSON array); `keep`/`failed_only` preferences live in `~/.actions-cleaner-settings.json`.

## Requirements

- Python **3.14** (via [uv](https://docs.astral.sh/uv/))
- [`gh` CLI](https://cli.github.com/) authenticated (`gh auth login`)
- Node.js (`npx`, only for the `jscpd` step of `just check`)

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

1. Add a repository — `owner/repo` **or** a full GitHub URL (e.g. `https://github.com/owner/repo`; URLs are reduced to `owner/repo`) — and click **Add**.
2. Set **Commits to keep** (how many recent commits to preserve).
3. Optionally set **Runs to fetch** (how many recent runs to scan; default 1000 — `gh` paginates automatically) and **Max deletions** (cap the number deleted in one pass; `No limit` by default).
4. Optionally check **Failed / cancelled only** to target unsuccessful runs exclusively.
5. Ensure **Dry run** is checked for a preview, then click **Clean Up Actions**.
6. Uncheck **Dry run** when ready to delete.

Only the **most recent runs** per repository are scanned (`gh run list --limit N`, `N` = *Runs to fetch*, default 1000), ordered by creation date. While a cleanup runs you can **Pause**/resume it or **Cancel**; closing the window mid-run waits for the current `gh` call to finish before quitting.

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
