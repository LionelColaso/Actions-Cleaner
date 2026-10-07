# AGENTS.md — `actions-cleaner` build spec & architecture

> **Who this is for:** an AI coding agent (or human) working in this repo.
> **What this app is:** a PySide6 desktop app that cleans up GitHub Actions
> workflow runs by keeping only the latest N commits' worth of runs and deleting
> the rest. It shells out to the `gh` CLI for all GitHub API interactions.
> **Read order:** §1 (architecture) → §2 (repo layout) → §3 (build) → §4 (invariants).
> **This file is the *design* spec.** Implementation status and work items live in
> [`TODO.md`](TODO.md).

---

## 1. Architecture

The app is a single-window PySide6 GUI with a background worker thread for all
`gh` CLI operations. It never touches the GitHub API directly — `gh` handles
auth, rate limiting, and pagination.

```
┌─────────────────────────────────────────────────────────────┐
│  MainWindow (QWidget)                                        │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ Settings group                                           ││
│  │  - Repository input + Add/Remove buttons                 ││
│  │  - Saved repositories dropdown (persisted to JSON)       ││
│  │  - Runs to keep (spin box, default 2)                    ││
│  │  - Dry run checkbox                                      ││
│  │  - Failed workflows only checkbox                        ││
│  └─────────────────────────────────────────────────────────┘│
│  [ Clean Up Actions ]                                        │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ Progress bar                                             ││
│  │ Log output (QTextEdit, read-only)                        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  CleanupWorker (QThread)                                     │
│  1. gh run list --repo <owner/repo> --limit 1000             │
│     --json databaseId,headSha                                │
│     [--status failure] (when failed-only is enabled)          │
│  2. Group runs by headSha, keep latest N commits             │
│  3. Delete all other runs via gh run delete                  │
│  Progress + log emitted via Qt signals to MainWindow         │
└─────────────────────────────────────────────────────────────┘
```

**Worker thread:** `CleanupWorker` extends `QThread`. All `subprocess.run` calls
happen in `run()`, never on the GUI thread. Progress and log lines are emitted
through `Signal(str)`, `Signal(int)` (progress), and `Signal(int)` (max) connections,
which Qt auto-queues to the GUI thread.

**Repository persistence:** Saved repos are stored as a JSON array in
`~/.actions-cleaner-repos.json`. `load_repos()` / `save_repos()` handle the
file. The dropdown (`QComboBox`) and line edit stay in sync via
`currentTextChanged` and Add/Remove button handlers.

**Subprocess contract:** the app invokes exactly two `gh` commands:
- `gh run list --repo <repo> --limit 1000 --json databaseId,headSha`
  `[--status failure]` (when failed-only is enabled)
- `gh run delete <run_id> --repo <repo>`

`subprocess.CalledProcessError` is caught and surfaced in the log; the app
never crashes on `gh` failures.

---

## 2. Repo layout

```
Actions-Cleaner/
├── pyproject.toml           # uv project; deps, tool configs
├── justfile                 # canonical tasks
├── .python-version          # 3.14
├── .gitignore
├── AGENTS.md                # this file
├── README.md
├── TODO.md
├── actions_cleaner_gui.py   # the app (single file)
├── scripts/
│   ├── build.py             # Nuitka standalone build entrypoint
│   └── clean.py             # remove build artifacts / caches
├── tests/                   # test directory (empty for now)
└── .github/workflows/
    ├── build.yml            # reusable per-OS Nuitka build
    ├── release.yml          # manual Stable/Edge GitHub release
    ├── auto_build.yml       # push to main → Edge release
    ├── ci.yml               # PR gate: lint + pyright + pytest + build
    ├── lint.yml             # ruff + mypy
    ├── pyright.yml          # pyright
    └── pytest.yml           # pytest headless
```

---

## 3. Build

- `just build` → Nuitka `--standalone` into `build/` via `scripts/build.py`.
- `just build-version 0.1.0.0` sets a product version.
- `just run` → `uv run python actions_cleaner_gui.py` (dev mode).
- `just check` → ruff format, ruff lint, mypy, pyright.
- `just test` → pytest (skips if `tests/` is empty).
- `just ci` → full local CI simulation.
- Python: **3.14** (matches `llama_gui`).
- Framework: **PySide6 6.7+**.
- Build tool: **Nuitka** (standalone, static libpython, pyside6 plugin).

---

## 4. Invariants

1. **All `gh` calls happen off the GUI thread.** `CleanupWorker` is a `QThread`;
   direct widget access from `run()` is undefined behaviour.
2. **No plaintext secrets.** The `gh` CLI handles auth via its own keychain;
   the app never reads or stores tokens.
3. **Dry run is the default.** The checkbox starts checked; the app must not
   delete anything until the user unchecks it.
4. **Repository input is validated before every run.** Empty or malformed
   `owner/repo` is rejected with a `QMessageBox` warning; no subprocess is
   spawned.
5. **Saved repos are a simple JSON list.** No duplicates, no external
   dependencies, human-readable file at `~/.actions-cleaner-repos.json`.

---

## 5. Hard "do not" list

- Do **not** block the GUI thread with `subprocess.run`.
- Do **not** store GitHub tokens or credentials.
- Do **not** delete runs in dry-run mode.
- Do **not** call `gh` without first validating the repo string.
- Do **not** add heavy external dependencies — this is a thin wrapper around `gh`.
- Do **not** change the single-file app structure without updating `scripts/build.py`.

---

## 6. Run / verify

```bash
just dev-setup               # install deps
just run                     # launch the GUI
just check                   # full check suite
just test                    # pytest
just build                   # Nuitka standalone
```
