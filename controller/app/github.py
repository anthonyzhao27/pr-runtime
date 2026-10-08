from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("github")
API = "https://api.github.com"


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
    r = httpx.post(f"{API}/repos/{repo}/pulls/{pr_number}/reviews", headers=_headers(), json=payload, timeout=30)
    if r.status_code == 422 and comments:
        # Usually a comment on a line outside the diff. Retry without inline comments so the verdict still lands.
        log.warning("review 422 for #%s: %s; retrying without inline comments", pr_number, r.text[:300])
        payload["comments"] = []
        payload["body"] = body + "\n\n" + "\n".join(f"- `{c['path']}:{c.get('line')}` {c['body']}" for c in comments)
        r = httpx.post(f"{API}/repos/{repo}/pulls/{pr_number}/reviews", headers=_headers(), json=payload, timeout=30)
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
