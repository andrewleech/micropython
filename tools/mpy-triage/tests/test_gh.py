"""Tests for the GitHub API wrapper."""

import json
import subprocess
import time
from unittest.mock import patch

from mpy_triage import gh as gh_mod
from mpy_triage.gh import (
    _parse_concatenated_json,
    _parse_rate_limit_reset,
    gh_api,
    gh_diff,
    gh_search,
)


class TestParseConcatenatedJson:
    """Unit tests for the JSON multiparse helper."""

    def test_single_array(self) -> None:
        assert _parse_concatenated_json("[1, 2, 3]") == [1, 2, 3]

    def test_two_arrays_concatenated(self) -> None:
        text = '[{"a":1},{"a":2}][{"a":3}]'
        result = _parse_concatenated_json(text)
        assert result == [{"a": 1}, {"a": 2}, {"a": 3}]

    def test_arrays_with_whitespace(self) -> None:
        text = "[1, 2]\n\n[3, 4]\n"
        assert _parse_concatenated_json(text) == [1, 2, 3, 4]

    def test_single_object(self) -> None:
        text = '{"key": "val"}'
        assert _parse_concatenated_json(text) == [{"key": "val"}]

    def test_empty_string(self) -> None:
        assert _parse_concatenated_json("") == []


class TestParseRateLimitReset:
    """Unit tests for rate-limit header parsing."""

    def test_extracts_timestamp(self) -> None:
        stderr = "HTTP 403\nX-RateLimit-Reset: 1700000000\nsome other text"
        assert _parse_rate_limit_reset(stderr) == 1700000000.0

    def test_returns_none_when_missing(self) -> None:
        assert _parse_rate_limit_reset("HTTP 500 Internal Server Error") is None


def _make_completed_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["gh", "api"], stdout=stdout, stderr=stderr, returncode=returncode
    )


class TestGhApi:
    """Tests for gh_api using mocked subprocess.run."""

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_simple_get(self, mock_run) -> None:
        payload = {"id": 1, "title": "test"}
        mock_run.return_value = _make_completed_process(stdout=json.dumps(payload))

        result = gh_api("repos/owner/repo/issues/1")

        assert result == payload
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "repos/owner/repo/issues/1" in cmd

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_paginate_merges_arrays(self, mock_run) -> None:
        page1 = [{"n": 1}]
        page2 = [{"n": 2}]
        stdout = json.dumps(page1) + json.dumps(page2)
        mock_run.return_value = _make_completed_process(stdout=stdout)

        result = gh_api("repos/owner/repo/issues", paginate=True)

        assert result == [{"n": 1}, {"n": 2}]
        cmd = mock_run.call_args[0][0]
        assert "--paginate" in cmd

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_error_returns_none(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(returncode=1, stderr="Not Found")
        assert gh_api("repos/owner/repo/nonexistent") is None

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_empty_response_paginate(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(stdout="  ")
        assert gh_api("repos/owner/repo/issues", paginate=True) == []

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_empty_response_no_paginate(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(stdout="  ")
        assert gh_api("repos/owner/repo/issues") is None

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("time.sleep")
    @patch("subprocess.run")
    def test_rate_limit_retry(self, mock_run, mock_sleep) -> None:
        """On 403 with rate-limit header, gh_api should sleep and retry."""
        reset_time = int(time.time()) + 5
        rate_stderr = f"HTTP 403\nX-RateLimit-Reset: {reset_time}\n"
        mock_run.side_effect = [
            _make_completed_process(returncode=1, stderr=rate_stderr),
            _make_completed_process(stdout='{"ok":true}'),
        ]

        result = gh_api("repos/owner/repo/issues")

        assert result == {"ok": True}
        assert mock_run.call_count == 2

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_custom_accept_header(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(stdout="diff --git a/f b/f\n")

        result = gh_api("repos/owner/repo/pulls/1", accept="application/vnd.github.diff")

        assert result == "diff --git a/f b/f\n"
        cmd = mock_run.call_args[0][0]
        assert any("application/vnd.github.diff" in arg for arg in cmd)

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_method_override(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(stdout="{}")
        gh_api("repos/owner/repo/issues", method="POST")
        cmd = mock_run.call_args[0][0]
        assert "--method" in cmd
        assert "POST" in cmd


class TestGhSearch:
    """Tests for gh_search."""

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_returns_items(self, mock_run) -> None:
        payload = {"items": [{"number": 1}, {"number": 2}], "total_count": 2}
        mock_run.return_value = _make_completed_process(stdout=json.dumps(payload))

        result = gh_search("repo:micropython/micropython is:issue")
        assert len(result) == 2

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_date_range_added(self, mock_run) -> None:
        payload = {"items": [], "total_count": 0}
        mock_run.return_value = _make_completed_process(stdout=json.dumps(payload))

        gh_search("repo:micropython/micropython", date_range=("2024-01-01", "2024-06-30"))
        cmd_str = " ".join(mock_run.call_args[0][0])
        assert (
            "created%3A2024-01-01..2024-06-30" in cmd_str
            or "created:2024-01-01..2024-06-30" in cmd_str
        )

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_error_returns_empty(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(returncode=1, stderr="err")
        assert gh_search("bad query") == []


class TestGhDiff:
    """Tests for gh_diff."""

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_returns_diff_string(self, mock_run) -> None:
        diff_text = "diff --git a/file.c b/file.c\n+hello\n"
        mock_run.return_value = _make_completed_process(stdout=diff_text)

        result = gh_diff("micropython/micropython", 42)
        assert result == diff_text

    @patch.object(gh_mod, "REQUEST_DELAY", 0)
    @patch("subprocess.run")
    def test_error_returns_none(self, mock_run) -> None:
        mock_run.return_value = _make_completed_process(returncode=1, stderr="err")
        assert gh_diff("micropython/micropython", 999) is None
