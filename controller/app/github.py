from __future__ import annotations

import logging
import threading
import time

import httpx

from .config import settings

log = logging.getLogger("github")
API = "https://api.github.com"
_post_lock = threading.Lock()  # GitHub's secondary rate limit punishes concurrent content creation from one identity
_last_post = 0.0
MIN_POST_GAP = 1.5


def _post_review_request(url: str, payload: dict) -> httpx.Response:
    """Serialized POST with backoff on GitHub's 403/429 secondary rate limit (honours Retry-After)."""
    global _last_post
    with _post_lock:
        for attempt in range(5):
            gap = MIN_POST_GAP - (time.monotonic() - _last_post)
            if gap > 0:
                time.sleep(gap)
            r = httpx.post(url, headers=_headers(), json=payload, timeout=30)
            _last_post = time.monotonic()
            if r.status_code in (403, 429) and ("rate limit" in r.text.lower() or "abuse" in r.text.lower() or r.headers.get("Retry-After")):
                wait = int(r.headers.get("Retry-After", "0")) or min(60 * (attempt + 1), 180)
                log.warning("GitHub secondary rate limit (attempt %d); sleeping %ss", attempt + 1, wait)
                time.sleep(wait)
                continue
            return r
        return r


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pr-runtime"}
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


def pr_size(repo: str, pr_number: int) -> int:
    """Changed lines (additions + deletions). Falls back to 0 on any error; ranking is best-effort."""
    try:
        r = httpx.get(f"{API}/repos/{repo}/pulls/{pr_number}", headers=_headers(), timeout=10)
        r.raise_for_status()
        j = r.json()
        return int(j.get("additions", 0)) + int(j.get("deletions", 0))
    except Exception as e:  # noqa: BLE001
        log.warning("pr_size failed for #%s: %s", pr_number, e)
        return 0


def post_review(repo: str, pr_number: int, head_sha: str, verdict: str, body: str, comments: list[dict]) -> str | None:
    """Create a PR review with inline comments. Returns the review html_url or None."""
    if not settings.github_token:
        log.warning("no GITHUB_BOT_TOKEN; skipping review post for #%s", pr_number)
        return None
    event = {"APPROVE": "APPROVE", "REQUEST_CHANGES": "REQUEST_CHANGES"}.get(verdict, "COMMENT")
    payload = {"commit_id": head_sha, "body": body, "event": event, "comments": comments}
    url = f"{API}/repos/{repo}/pulls/{pr_number}/reviews"
    r = _post_review_request(url, payload)
    if r.status_code == 422 and "own pull request" in r.text and event != "COMMENT":
        # GitHub refuses APPROVE/REQUEST_CHANGES from the PR author. Same identity = comment with the verdict in the body.
        log.info("self-review on #%s; downgrading %s to COMMENT", pr_number, event)
        payload["event"] = "COMMENT"
        payload["body"] = f"**Verdict: {verdict}**\n\n{body}"
        r = _post_review_request(url, payload)
    if r.status_code == 422 and comments:
        # Usually a comment on a line outside the diff. Retry without inline comments so the verdict still lands.
        log.warning("review 422 for #%s: %s; retrying without inline comments", pr_number, r.text[:300])
        payload["comments"] = []
        payload["body"] = body + "\n\n" + "\n".join(f"- `{c['path']}:{c.get('line')}` {c['body']}" for c in comments)
        r = _post_review_request(url, payload)
    r.raise_for_status()
    return r.json().get("html_url")


def get_file(repo: str, path: str, ref: str) -> str | None:
    """Fetch a file at a ref (used by the reviewer's read_file tool for files outside the diff)."""
    try:
        r = httpx.get(f"{API}/repos/{repo}/contents/{path}", params={"ref": ref},
                      headers={**_headers(), "Accept": "application/vnd.github.raw+json"}, timeout=15)
        if r.status_code == 200:
            return r.text[:200_000]
    except Exception as e:  # noqa: BLE001
        log.warning("get_file %s failed: %s", path, e)
    return None
