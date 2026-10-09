# ─── Shell Configuration ─────────────────────────────────────────────────
# Unix: just's default (sh -c). Windows: powershell.exe (ships with all
# modern Windows). Multi-line recipes use [unix]/[windows] guards.
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# ─── Global Variables ────────────────────────────────────────────────────
config_and_path := "--config pyproject.toml ."
pytest_opts := "--no-qt-log -s -ra"
cov_opts := "--cov=actions_cleaner_gui --cov-report=xml --cov-report=html --cov-report=term-missing --junitxml=junit/test-results.xml"

# ─── Default Target ──────────────────────────────────────────────────────
@default:
    just --list

# ═══════════════════════════════════════════════════════════════════════════
# Core Development
# ═══════════════════════════════════════════════════════════════════════════

# Run the actions-cleaner application
run: dev-setup
    uv run python actions_cleaner_gui.py

# Run all tests (skips gracefully if tests/ does not exist or is empty)
[unix]
test: dev-setup
    uv run pytest {{pytest_opts}} -q tests/; rc=$?; if [ $rc -eq 5 ]; then echo "No tests ran"; exit 0; else exit $rc; fi

[windows]
test: dev-setup
    uv run pytest {{pytest_opts}} -q tests/; $rc=$LASTEXITCODE; if ($rc -eq 5) { echo "No tests ran"; exit 0 }; exit $rc

# Run tests with verbose output and short tracebacks
[unix]
test-verbose: dev-setup
    uv run pytest {{pytest_opts}} -v --tb=short tests/; rc=$?; if [ $rc -eq 5 ]; then echo "No tests ran"; exit 0; else exit $rc; fi

[windows]
test-verbose: dev-setup
    uv run pytest {{pytest_opts}} -v --tb=short tests/; $rc=$LASTEXITCODE; if ($rc -eq 5) { echo "No tests ran"; exit 0 }; exit $rc

# Run tests and emit an HTML coverage report + XML for CI uploads
[unix]
coverage: dev-setup
    uv run pytest {{pytest_opts}} {{cov_opts}} tests/; rc=$?; if [ $rc -eq 5 ]; then echo "No tests ran"; exit 0; else exit $rc; fi

[windows]
coverage: dev-setup
    uv run pytest {{pytest_opts}} {{cov_opts}} tests/; $rc=$LASTEXITCODE; if ($rc -eq 5) { echo "No tests ran"; exit 0 }; exit $rc

# ═══════════════════════════════════════════════════════════════════════════
# Code Quality
# ═══════════════════════════════════════════════════════════════════════════

# Run copy/paste detection (jscpd); skip gracefully if npx is unavailable
jscpd:
    npx --yes jscpd@5.4.0 . --config .jscpd.json

# Run the full check suite: ruff format --check, ruff check, mypy, pyright, jscpd
check: ruff-format-check ruffcheck typecheck jscpd
    @echo "Use 'just fix' to automatically fix linting and formatting issues!"

# Verify formatting without modifying files (ruff format --check)
ruff-format-check:
    uv run ruff format --check {{config_and_path}}

# Check linting issues (ruff check)
ruffcheck:
    uv run ruff check {{config_and_path}}

# Type-check with mypy and pyright
typecheck: mypy pyright
    @echo "Typecheck complete!"

# check with mypy
mypy:
    uv run mypy --config-file pyproject.toml .

# check with pyright
pyright:
    uv run pyright -p pyproject.toml .

# Auto-fix formatting and lint issues
fix: format ruff-fix
    @echo "Auto-fixes applied!"

# Fix with ruff
ruff-fix:
    uv run ruff check --fix {{config_and_path}}

# Format code with ruff
format:
    uv run ruff format {{config_and_path}}

# Run full CI pipeline locally: quality checks + tests with coverage
ci: check coverage
    @echo "CI simulation complete!"

# ═══════════════════════════════════════════════════════════════════════════
# Dependency Management
# ═══════════════════════════════════════════════════════════════════════════

# Install all dependencies (including dev and build groups)
dev-setup:
    uv sync --locked --dev --group build

# Update all dependencies to their latest compatible versions
update:
    uv lock --upgrade

# Remove all build artifacts, caches, and generated files
clean:
    uv run python scripts/clean.py

# ═══════════════════════════════════════════════════════════════════════════
# Build / Distribution
# ═══════════════════════════════════════════════════════════════════════════

# Build actions-cleaner executable with Nuitka (runs checks first)
build *ARGS='': check
    uv run python scripts/build.py {{ARGS}}

# Build actions-cleaner executable with a specific version string
build-version VERSION: check
    uv run python scripts/build.py --product-version="{{VERSION}}"

# ═══════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════

# Show help for build script
build-help:
    uv run python scripts/build.py --help
