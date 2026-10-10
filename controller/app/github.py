from __future__ import annotations

import logging
import threading
import time

import httpx

from . import github_app
from .config import settings

log = logging.getLogger("github")
API = "https://api.github.com"
_post_lock = threading.Lock()  # GitHub's secondary rate limit punishes concurrent content creation from one identity
_last_post = 0.0
MIN_POST_GAP = 1.5


def _headers(installation_id: int | None = None) -> dict:
    """Auth for a given task: App installation token when the App is configured, else the PAT."""
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pr-runtime"}
    token = github_app.token_for(installation_id) or settings.github_token
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(method: str, url: str, installation_id: int | None, **kw) -> httpx.Response:
    """Serialized write with backoff on GitHub's 403/429 secondary rate limit (honours Retry-After)."""
    global _last_post
    with _post_lock:
        for attempt in range(5):
            gap = MIN_POST_GAP - (time.monotonic() - _last_post)
            if gap > 0:
                time.sleep(gap)
            r = httpx.request(method, url, headers=_headers(installation_id), timeout=30, **kw)
            _last_post = time.monotonic()
            if r.status_code in (403, 429) and ("rate limit" in r.text.lower() or "abuse" in r.text.lower() or r.headers.get("Retry-After")):
                wait = int(r.headers.get("Retry-After", "0")) or min(60 * (attempt + 1), 180)
                log.warning("GitHub secondary rate limit (attempt %d); sleeping %ss", attempt + 1, wait)
                time.sleep(wait)
                continue
            return r
        return r


def pr_size(repo: str, pr_number: int, installation_id: int | None = None) -> int:
    """Changed lines (additions + deletions). Falls back to 0 on any error; ranking is best-effort."""
    try:
        r = httpx.get(f"{API}/repos/{repo}/pulls/{pr_number}", headers=_headers(installation_id), timeout=10)
        r.raise_for_status()
        j = r.json()
        return int(j.get("additions", 0)) + int(j.get("deletions", 0))
    except Exception as e:  # noqa: BLE001
        log.warning("pr_size failed for #%s: %s", pr_number, e)
        return 0


def pr_head(repo: str, pr_number: int, installation_id: int | None = None) -> tuple[str, str] | None:
    """(head_sha, base_sha) for a PR; used when a mention arrives without PR shas in the payload."""
    try:
        r = httpx.get(f"{API}/repos/{repo}/pulls/{pr_number}", headers=_headers(installation_id), timeout=10)
        r.raise_for_status()
        j = r.json()
        return j["head"]["sha"], j["base"]["sha"]
    except Exception as e:  # noqa: BLE001
        log.warning("pr_head failed for #%s: %s", pr_number, e)
        return None


def post_review(repo: str, pr_number: int, head_sha: str, verdict: str, body: str, comments: list[dict],
                installation_id: int | None = None) -> str | None:
    """Create a PR review with inline comments. Returns the review html_url or None."""
    if not (github_app.token_for(installation_id) or settings.github_token):
        log.warning("no GitHub credential; skipping review post for #%s", pr_number)
        return None
    event = {"APPROVE": "APPROVE", "REQUEST_CHANGES": "REQUEST_CHANGES"}.get(verdict, "COMMENT")
    payload = {"commit_id": head_sha, "body": body, "event": event, "comments": comments}
    url = f"{API}/repos/{repo}/pulls/{pr_number}/reviews"
    r = _request("POST", url, installation_id, json=payload)
    if r.status_code == 422 and "own pull request" in r.text and event != "COMMENT":
        # GitHub refuses APPROVE/REQUEST_CHANGES from the PR author. Same identity = comment with the verdict in the body.
        log.info("self-review on #%s; downgrading %s to COMMENT", pr_number, event)
        payload["event"] = "COMMENT"
        payload["body"] = f"**Verdict: {verdict}**\n\n{body}"
        r = _request("POST", url, installation_id, json=payload)
    if r.status_code == 422 and comments:
        # Usually a comment on a line outside the diff. Retry without inline comments so the verdict still lands.
        log.warning("review 422 for #%s: %s; retrying without inline comments", pr_number, r.text[:300])
        payload["comments"] = []
        payload["body"] = body + "\n\n" + "\n".join(f"- `{c['path']}:{c.get('line')}` {c['body']}" for c in comments)
        r = _request("POST", url, installation_id, json=payload)
    r.raise_for_status()
    return r.json().get("html_url")


def get_file(repo: str, path: str, ref: str, installation_id: int | None = None) -> str | None:
    """Fetch a file at a ref (used by the reviewer's read_file tool for files outside the diff)."""
    try:
        r = httpx.get(f"{API}/repos/{repo}/contents/{path}", params={"ref": ref},
                      headers={**_headers(installation_id), "Accept": "application/vnd.github.raw+json"}, timeout=15)
        if r.status_code == 200:
            return r.text[:200_000]
    except Exception as e:  # noqa: BLE001
        log.warning("get_file %s failed: %s", path, e)
    return None


# ---- Checks API (needs a GitHub App; PATs cannot create check runs) ----------------------------

def check_start(repo: str, head_sha: str, installation_id: int | None, details_url: str | None = None) -> int | None:
    if not (settings.checks_enabled and installation_id and github_app.configured()):
        return None
    try:
        r = _request("POST", f"{API}/repos/{repo}/check-runs", installation_id, json={
            "name": "pr-runtime", "head_sha": head_sha, "status": "in_progress",
            "details_url": details_url, "output": {"title": "Reviewing", "summary": "Running tests and reviewing the diff."},
        })
        r.raise_for_status()
        return r.json()["id"]
    except Exception as e:  # noqa: BLE001
        log.warning("check_start failed: %s", e)
        return None


def check_finish(repo: str, check_run_id: int | None, installation_id: int | None, conclusion: str,
                 title: str, summary: str, text: str = "") -> None:
    """conclusion: success | failure | neutral | action_required. The bot blocks via 'failure'; it never approves."""
    if not check_run_id:
        return
    try:
        r = _request("PATCH", f"{API}/repos/{repo}/check-runs/{check_run_id}", installation_id, json={
            "status": "completed", "conclusion": conclusion,
            "output": {"title": title[:255], "summary": summary[:65_000], "text": text[:65_000]},
        })
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("check_finish failed: %s", e)


def react(repo: str, comment_id: int, installation_id: int | None, content: str = "eyes") -> None:
    """Acknowledge a mention with a reaction on the comment that triggered it."""
    try:
        _request("POST", f"{API}/repos/{repo}/issues/comments/{comment_id}/reactions", installation_id, json={"content": content})
    except Exception as e:  # noqa: BLE001
        log.warning("react failed: %s", e)


def comment(repo: str, issue_number: int, installation_id: int | None, body: str) -> None:
    try:
        _request("POST", f"{API}/repos/{repo}/issues/{issue_number}/comments", installation_id, json={"body": body})
    except Exception as e:  # noqa: BLE001
        log.warning("comment failed: %s", e)
