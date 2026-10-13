"""Shared gh CLI retry logic for rate limiting and transient failures."""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

# Type alias for log signal
LogSignal = Any

#: Regex to extract rate limit remaining from gh stderr
RATE_LIMIT_REMAINING_RE = re.compile(r"X-RateLimit-Remaining:\s*(\d+)")
#: Regex to extract rate limit reset time from gh stderr
RATE_LIMIT_RESET_RE = re.compile(r"X-RateLimit-Reset:\s*(\d+)")

#: Max retries for transient gh failures
MAX_RETRIES = 3
#: Base delay for exponential backoff (seconds)
RETRY_BASE_DELAY = 1.0
#: Max delay for exponential backoff (seconds)
RETRY_MAX_DELAY = 30.0

#: Common gh error patterns mapped to user-friendly messages
GH_ERROR_MESSAGES: dict[str, str] = {
    "run not found": "Run not found — it may have already been deleted",
    "not found": "Resource not found — check the repository name and run ID",
    "permission denied": "Permission denied — ensure you have admin access to the repository",
    "unauthorized": "Authentication failed — run `gh auth login`",
    "bad credentials": "Invalid credentials — run `gh auth login` to re-authenticate",
    "rate limit": "Rate limited — wait for reset or increase delay between calls",
    "could not resolve host": "Network error — check your internet connection",
    "connection refused": "Connection refused — check network/firewall settings",
    "timeout": "Request timed out — try increasing the timeout setting",
    "graphql": "GraphQL error — may be a temporary GitHub API issue",
    "validation failed": "Invalid request — check the repository and workflow names",
}


def _parse_rate_limit_reset(stderr: str) -> float | None:
    """Parse rate limit reset timestamp from stderr; return wait time in seconds or None."""
    match = RATE_LIMIT_RESET_RE.search(stderr)
    if not match:
        return None
    try:
        reset_ts = int(match.group(1))
        now = time.time()
        wait = max(0, reset_ts - now + 1)  # +1 second buffer
        return wait
    except ValueError, OSError:
        return None


def _is_rate_limited(stderr: str) -> bool:
    """Check if gh stderr indicates rate limiting (remaining == 0)."""
    match = RATE_LIMIT_REMAINING_RE.search(stderr)
    if not match:
        return False
    return int(match.group(1)) == 0


def _is_transient_error(stderr: str) -> bool:
    """Check if error is transient (network, timeout, server error) and worth retrying."""
    stderr_lower = stderr.lower()
    transient_patterns = [
        "timeout",
        "connection",
        "network",
        "temporary",
        "502",
        "503",
        "504",
        "rate limit",
        "rate limited",
        "too many requests",
    ]
    return any(pattern in stderr_lower for pattern in transient_patterns)


def run_gh_with_retry(
    cmd: list[str],
    timeout: int,
    log_signal: LogSignal,
    max_retries: int = MAX_RETRIES,
) -> subprocess.CompletedProcess[str] | None:
    """
    Run gh command with retry logic for transient failures and rate limit handling.

    Returns CompletedProcess on success, None on fatal error or cancellation.
    The log_signal is used to emit status updates.
    """
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if attempt < max_retries:
                delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                log_signal.emit(
                    f"  Timeout on attempt {attempt + 1}/{max_retries + 1}; "
                    f"retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                continue
            return None
        except OSError as e:
            log_signal.emit(f"  Failed to run `gh`: {e}")
            return None

        # Success
        if result.returncode == 0:
            return result

        stderr = result.stderr or ""
        stdout = result.stdout or ""
        combined = (stderr + "\n" + stdout).strip()

        # Handle rate limiting
        if _is_rate_limited(combined):
            wait_time = _parse_rate_limit_reset(combined)
            if wait_time is not None and wait_time > 0:
                log_signal.emit(
                    f"  Rate limited; waiting {wait_time:.0f}s for reset..."
                )
                time.sleep(wait_time)
                continue
            # If we can't parse reset time, use exponential backoff
            if attempt < max_retries:
                delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
                log_signal.emit(f"  Rate limited; retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            log_signal.emit(f"  Rate limited and max retries exceeded: {combined}")
            return result

        # Handle transient errors with retry
        if _is_transient_error(combined) and attempt < max_retries:
            delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
            log_signal.emit(
                f"  Transient error on attempt {attempt + 1}/{max_retries + 1}; "
                f"retrying in {delay:.1f}s: {combined}"
            )
            time.sleep(delay)
            continue

        # Non-retryable error
        return result

    return None


def get_friendly_error_message(stderr: str, stdout: str = "") -> str:
    """
    Map common gh CLI error output to user-friendly guidance.

    Args:
        stderr: Standard error output from gh command
        stdout: Standard output from gh command

    Returns:
        User-friendly error message, or the original error if no mapping found
    """
    combined = (stderr + "\n" + stdout).lower()
    for pattern, message in GH_ERROR_MESSAGES.items():
        if pattern in combined:
            return message
    # Return original error (truncated) if no friendly mapping found
    original = (stderr or stdout or "").strip()
    return original if original else "Unknown error"
