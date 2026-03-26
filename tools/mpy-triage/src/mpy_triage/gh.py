"""GitHub API wrapper for mpy-triage.

Uses the ``gh`` CLI tool for authenticated access. Handles pagination,
rate-limit back-off, and search queries.
"""

import json
import logging
import re
import subprocess
import time
from urllib.parse import quote

log = logging.getLogger(__name__)

# Rate limiting: 5000 requests/hour for authenticated users.
REQUESTS_PER_HOUR = 5000
REQUEST_DELAY: float = 3600 / REQUESTS_PER_HOUR  # ~0.72s
_last_request_time: float = 0.0


def gh_api(
    endpoint: str,
    *,
    paginate: bool = False,
    accept: str = "application/vnd.github+json",
    method: str | None = None,
) -> list | dict | str | None:
    """Call the GitHub API via the ``gh`` CLI.

    Args:
        endpoint: API endpoint path (e.g. ``repos/owner/repo/issues``).
        paginate: If True, pass ``--paginate`` and merge concatenated JSON arrays.
        accept: Value for the Accept header.
        method: HTTP method override (e.g. ``GET``, ``POST``). Omitted when None.

    Returns:
        Parsed JSON (list or dict), raw text for non-JSON accept types, or
        None on error.
    """
    global _last_request_time

    cmd = ["gh", "api", "-H", f"Accept: {accept}"]
    if paginate:
        cmd.append("--paginate")
    if method is not None:
        cmd.extend(["--method", method])
    cmd.append(endpoint)

    while True:
        # Enforce minimum delay between requests to stay under rate limit.
        elapsed = time.time() - _last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        result = subprocess.run(cmd, capture_output=True, text=True)
        _last_request_time = time.time()

        if result.returncode != 0:
            # Check for rate limiting (HTTP 403 or 429).
            stderr = result.stderr
            if "403" in stderr or "429" in stderr:
                reset_ts = _parse_rate_limit_reset(stderr)
                if reset_ts is not None:
                    wait = max(0, reset_ts - time.time()) + 1
                    log.warning("Rate limited. Sleeping %.0f seconds until reset.", wait)
                    time.sleep(wait)
                    continue
            log.error("gh api error (rc=%d): %s", result.returncode, result.stderr)
            return None

        break

    if not result.stdout.strip():
        return [] if paginate else None

    # Non-JSON responses (e.g. diff).
    if "json" not in accept:
        return result.stdout

    # Paginated responses: gh --paginate concatenates multiple JSON arrays.
    if paginate:
        return _parse_concatenated_json(result.stdout)

    return json.loads(result.stdout)


def gh_search(query: str, *, date_range: tuple[str, str] | None = None) -> list[dict]:
    """Search GitHub issues/PRs via the search API.

    Args:
        query: Search query string (e.g. ``repo:micropython/micropython is:issue``).
        date_range: Optional (start, end) date strings in ``YYYY-MM-DD`` format.
            Adds a ``created:start..end`` qualifier to the query.

    Returns:
        List of item dicts from the search results.
    """
    q = query
    if date_range is not None:
        start, end = date_range
        q += f" created:{start}..{end}"

    endpoint = f"search/issues?q={quote(q)}&per_page=100&sort=updated&order=desc"
    result = gh_api(endpoint)
    if result is None or not isinstance(result, dict):
        return []
    return result.get("items", [])


def gh_diff(repo: str, pr_number: int) -> str | None:
    """Fetch the diff for a pull request.

    Args:
        repo: Repository in ``owner/name`` format.
        pr_number: PR number.

    Returns:
        The diff as a string, or None on error.
    """
    result = gh_api(
        f"repos/{repo}/pulls/{pr_number}",
        accept="application/vnd.github.diff",
    )
    if isinstance(result, str):
        return result
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_concatenated_json(text: str) -> list:
    """Parse potentially concatenated JSON arrays from ``gh --paginate``.

    When paginating, ``gh`` concatenates raw JSON responses without a
    separator.  Two consecutive arrays like ``[1,2][3,4]`` must be merged
    into ``[1,2,3,4]``.
    """
    data: list = []
    decoder = json.JSONDecoder()
    text = text.strip()
    pos = 0
    while pos < len(text):
        try:
            obj, end = decoder.raw_decode(text, pos)
            if isinstance(obj, list):
                data.extend(obj)
            else:
                data.append(obj)
            pos = end
            # Skip whitespace between concatenated values.
            while pos < len(text) and text[pos] in " \t\n\r":
                pos += 1
        except json.JSONDecodeError:
            break
    return data


def _parse_rate_limit_reset(stderr: str) -> float | None:
    """Extract ``X-RateLimit-Reset`` epoch timestamp from gh stderr output."""
    match = re.search(r"X-RateLimit-Reset:\s*(\d+)", stderr, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None
