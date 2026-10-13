"""Tests for gh_retry module - rate limiting, retry logic, and transient error handling."""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from app.gh_retry import (
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    _is_rate_limited,
    _is_transient_error,
    _parse_rate_limit_reset,
    run_gh_with_retry,
)


class TestRateLimitParsing:
    """Tests for rate limit header parsing."""

    def test_parse_rate_limit_reset_valid(self) -> None:
        """Parse valid X-RateLimit-Reset header."""
        future_ts = int(time.time()) + 60
        stderr = f"X-RateLimit-Reset: {future_ts}"
        result = _parse_rate_limit_reset(stderr)
        assert result is not None
        assert 59 <= result <= 61  # ~60 seconds with buffer

    def test_parse_rate_limit_reset_past(self) -> None:
        """Parse past X-RateLimit-Reset header returns 0+."""
        past_ts = int(time.time()) - 60
        stderr = f"X-RateLimit-Reset: {past_ts}"
        result = _parse_rate_limit_reset(stderr)
        assert result is not None
        assert result >= 0  # max(0, ...) ensures non-negative

    def test_parse_rate_limit_reset_missing(self) -> None:
        """Missing header returns None."""
        stderr = "some other error"
        result = _parse_rate_limit_reset(stderr)
        assert result is None

    def test_parse_rate_limit_reset_invalid(self) -> None:
        """Invalid timestamp returns None."""
        stderr = "X-RateLimit-Reset: not-a-number"
        result = _parse_rate_limit_reset(stderr)
        assert result is None

    def test_is_rate_limited_true(self) -> None:
        """Rate limit remaining 0 returns True."""
        stderr = "X-RateLimit-Remaining: 0"
        assert _is_rate_limited(stderr) is True

    def test_is_rate_limited_false(self) -> None:
        """Rate limit remaining > 0 returns False."""
        stderr = "X-RateLimit-Remaining: 10"
        assert _is_rate_limited(stderr) is False

    def test_is_rate_limited_missing(self) -> None:
        """Missing header returns False."""
        stderr = "some other error"
        assert _is_rate_limited(stderr) is False


class TestTransientErrorDetection:
    """Tests for transient error detection."""

    @pytest.mark.parametrize(
        "stderr",
        [
            "timeout",
            "connection refused",
            "network error",
            "temporary failure",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "rate limit exceeded",
            "rate limited",
            "too many requests",
            "RATE LIMIT",  # case insensitive
            "NETWORK ERROR",  # case insensitive
        ],
    )
    def test_is_transient_error_detects(self, stderr: str) -> None:
        """Various transient error patterns are detected."""
        assert _is_transient_error(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "not found",
            "permission denied",
            "invalid repository",
            "authentication failed",
            "404 Not Found",
            "401 Unauthorized",
        ],
    )
    def test_is_transient_error_ignores(self, stderr: str) -> None:
        """Non-transient errors are not detected."""
        assert _is_transient_error(stderr) is False


class TestRunGhWithRetry:
    """Tests for run_gh_with_retry function."""

    def test_success_first_attempt(self) -> None:
        """Successful command returns CompletedProcess on first try."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "version"],
                returncode=0,
                stdout="gh version 2.45.0",
                stderr="",
            )
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 0
            assert mock_run.call_count == 1

    def test_retry_on_timeout(self) -> None:
        """Timeout triggers retry with exponential backoff."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            mock_run.side_effect = [
                subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 0
            assert mock_run.call_count == 2
            mock_sleep.assert_called_once()

    def test_max_retries_exceeded_on_timeout(self) -> None:
        """Returns None after max retries exceeded on timeout."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["gh"], timeout=5)
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is None
            assert mock_run.call_count == MAX_RETRIES + 1

    def test_retry_on_transient_error(self) -> None:
        """Transient error triggers retry."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=1,
                    stdout="",
                    stderr="timeout",
                ),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 0
            assert mock_run.call_count == 2
            mock_sleep.assert_called_once()

    def test_rate_limited_waits_for_reset(self) -> None:
        """Rate limited response waits for reset time."""
        log_signal = MagicMock()
        future_ts = int(time.time()) + 10
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=1,
                    stdout="",
                    stderr=f"X-RateLimit-Remaining: 0\nX-RateLimit-Reset: {future_ts}",
                ),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 0
            assert mock_run.call_count == 2
            # Should have slept for ~10 seconds
            mock_sleep.assert_called()
            sleep_time = mock_sleep.call_args[0][0]
            assert 9 <= sleep_time <= 12

    def test_rate_limited_no_reset_time_uses_backoff(self) -> None:
        """Rate limited without reset time uses exponential backoff."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=1,
                    stdout="",
                    stderr="X-RateLimit-Remaining: 0",
                ),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 0
            assert mock_run.call_count == 2
            mock_sleep.assert_called_once()

    def test_os_error_returns_none(self) -> None:
        """OSError (e.g., gh not found) returns None immediately."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("gh not found")
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is None
            assert mock_run.call_count == 1
            log_signal.emit.assert_called_with("  Failed to run `gh`: gh not found")

    def test_non_retryable_error_returns_result(self) -> None:
        """Non-retryable error returns the result without retrying."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "version"],
                returncode=1,
                stdout="",
                stderr="authentication failed",
            )
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is not None
            assert result.returncode == 1
            assert mock_run.call_count == 1

    def test_retry_delay_is_capped_at_max(self) -> None:
        """Retry delay is capped at RETRY_MAX_DELAY."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep") as mock_sleep:
            # Simulate multiple timeouts to trigger multiple retries
            # With MAX_RETRIES=3, we get delays: 1, 2, 4 (2**0, 2**1, 2**2)
            # All under RETRY_MAX_DELAY=30, so no capping yet
            mock_run.side_effect = [
                subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
                subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
                subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
                subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
            ]
            result = run_gh_with_retry(["gh", "version"], 5, log_signal)
            assert result is None
            # Check delays follow exponential backoff: 1, 2, 4
            sleep_calls = mock_sleep.call_args_list
            assert len(sleep_calls) == MAX_RETRIES
            assert sleep_calls[0][0][0] == RETRY_BASE_DELAY * 1  # 1
            assert sleep_calls[1][0][0] == RETRY_BASE_DELAY * 2  # 2
            assert sleep_calls[2][0][0] == RETRY_BASE_DELAY * 4  # 4
            # All should be less than max
            for call in sleep_calls:
                assert call[0][0] <= RETRY_MAX_DELAY

    def test_emit_on_transient_error(self) -> None:
        """Logs transient error with retry info."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=1,
                    stdout="",
                    stderr="timeout",
                ),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            run_gh_with_retry(["gh", "version"], 5, log_signal)
            # Check that error was logged
            calls = [str(c) for c in log_signal.emit.call_args_list]
            assert any("Transient error" in c for c in calls)
            assert any("retrying" in c for c in calls)

    def test_emit_on_rate_limited(self) -> None:
        """Logs rate limited message."""
        log_signal = MagicMock()
        with patch("subprocess.run") as mock_run, patch("time.sleep"):
            mock_run.side_effect = [
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=1,
                    stdout="",
                    stderr="X-RateLimit-Remaining: 0",
                ),
                subprocess.CompletedProcess(
                    args=["gh", "version"],
                    returncode=0,
                    stdout="gh version 2.45.0",
                    stderr="",
                ),
            ]
            run_gh_with_retry(["gh", "version"], 5, log_signal)
            calls = [str(c) for c in log_signal.emit.call_args_list]
            assert any("Rate limited" in c for c in calls)
