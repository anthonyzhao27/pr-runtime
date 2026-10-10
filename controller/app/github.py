from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager, nullcontext

import httpx

from . import github_app
from .config import settings

log = logging.getLogger("github")
API = "https://api.github.com"
MAX_FILE_CHARS = 200_000  # same cap as the runner's touched-file read
MAX_DIFF_CHARS = 120_000  # runner sends at most MAX_OUTPUT*4 of diff; the prompt clips to 60k anyway

# ---- write serialization ------------------------------------------------------------------------
# GitHub's secondary rate limit is enforced per identity (per installation token for an App, per user for a PAT)
# and it punishes two things: concurrent content creation, and more than ~80 POST/PATCH/PUT/DELETE a minute
# (500 an hour). The Oct 7 burst tripped it with 12 workers posting in parallel, which is why writes are
# serialized at all. What changed on Oct 10:
#   * one lock + last-write timestamp per identity bucket (installation id, or "pat" when no installation /
#     no App). Writes to different installations never wait on each other. Honest limit: every test PR here is
#     under one installation (anthonyzhao27), so this cannot help a single-tenant burst; it only stops one
#     tenant's burst from delaying another's.
#   * MIN_POST_GAP applies between *slots*, not between requests. A task's review POST and its check-run PATCH
#     are two endpoints but one post_slot(): the task pays one gap, not two. Two back-to-back writes every
#     ~1.5s + 2 RTT (~2.3s) is ~52 writes/min, under the 80/min ceiling with margin for check-run opens.
#   * 403/429 with Retry-After or "rate limit"/"abuse" in the body: honour Retry-After (else 60s*attempt),
#     retry up to 5 times, and log it loudly so a burst that hits the limit is reported, not hidden.
MIN_POST_GAP = 1.5


class _Bucket:
    __slots__ = ("lock", "last_post")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_post = 0.0


_buckets: dict[str, _Bucket] = {}
_buckets_lock = threading.Lock()  # guards the dict only; each bucket has its own lock
_tls = threading.local()  # .slot = the bucket this thread currently holds via post_slot(), if any


def _bucket(installation_id: int | None) -> _Bucket:
    key = str(installation_id) if (installation_id and github_app.configured()) else "pat"
    with _buckets_lock:
        b = _buckets.get(key)
        if b is None:
            b = _buckets[key] = _Bucket()
        return b


def _wait_gap(b: _Bucket) -> None:
    gap = MIN_POST_GAP - (time.monotonic() - b.last_post)
    if gap > 0:
        time.sleep(gap)


@contextmanager
def post_slot(installation_id: int | None):
    """Hold one serialized write slot for several requests to the same identity: one gap for the slot, then the
    requests inside go back to back. Re-entrant for the holding thread."""
    b = _bucket(installation_id)
    if getattr(_tls, "slot", None) is b:
        yield
        return
    with b.lock:
        _wait_gap(b)
        _tls.slot = b
        try:
            yield
        finally:
            _tls.slot = None
            b.last_post = time.monotonic()


def _headers(installation_id: int | None = None) -> dict:
    """Auth for a given task: App installation token when the App is configured, else the PAT."""
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "pr-runtime"}
    token = github_app.token_for(installation_id) or settings.github_token
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(method: str, url: str, installation_id: int | None, **kw) -> httpx.Response:
    """Serialized write (per identity bucket) with backoff on GitHub's 403/429 secondary rate limit."""
    b = _bucket(installation_id)
    held = getattr(_tls, "slot", None) is b
    with (nullcontext() if held else b.lock):
        for attempt in range(5):
            if not held:
                _wait_gap(b)
            r = httpx.request(method, url, headers=_headers(installation_id), timeout=30, **kw)
            b.last_post = time.monotonic()
            if r.status_code in (403, 429) and ("rate limit" in r.text.lower() or "abuse" in r.text.lower() or r.headers.get("Retry-After")):
                wait = int(r.headers.get("Retry-After", "0")) or min(60 * (attempt + 1), 180)
                log.warning("GitHub secondary rate limit on %s %s (attempt %d); sleeping %ss", method, url, attempt + 1, wait)
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


# ---- API-diff fallback (runner could not run: private repo, bad ref, crash) -------------------------------

def pr_diff(repo: str, pr_number: int, installation_id: int | None = None) -> str:
    r = httpx.get(f"{API}/repos/{repo}/pulls/{pr_number}",
                  headers={**_headers(installation_id), "Accept": "application/vnd.github.diff"}, timeout=30)
    r.raise_for_status()
    return r.text[:MAX_DIFF_CHARS]


def pr_files(repo: str, pr_number: int, installation_id: int | None = None) -> list[dict]:
    """Changed files of a PR (filename, status, additions, deletions), paginated; GitHub stops at 3000."""
    out: list[dict] = []
    for page in range(1, 31):
        r = httpx.get(f"{API}/repos/{repo}/pulls/{pr_number}/files", params={"per_page": 100, "page": page},
                      headers=_headers(installation_id), timeout=30)
        r.raise_for_status()
        batch = r.json()
        out.extend(batch)
        if len(batch) < 100:
            break
    return out


def file_at(repo: str, path: str, ref: str, installation_id: int | None = None) -> str | None:
    """Raw contents at a ref with the runner's caps: None when missing, binary (NUL in the first 8k) or oversized."""
    r = httpx.get(f"{API}/repos/{repo}/contents/{path}", params={"ref": ref},
                  headers={**_headers(installation_id), "Accept": "application/vnd.github.raw+json"}, timeout=30)
    if r.status_code != 200:
        return None
    data = r.content
    if len(data) > MAX_FILE_CHARS * 2 or b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")[:MAX_FILE_CHARS]


def pr_context(repo: str, pr_number: int, head_sha: str, installation_id: int | None = None) -> dict:
    """What the runner would have produced minus the tests: the PR diff, the touched paths and the head contents of
    the touched files, all through the installation token (so private repos work). Raises on failure; the caller
    decides what a failed fallback means."""
    diff = pr_diff(repo, pr_number, installation_id)
    meta = pr_files(repo, pr_number, installation_id)
    touched = [f["filename"] for f in meta]
    files: dict[str, str] = {}
    for f in meta:
        if f.get("status") == "removed":
            continue
        content = file_at(repo, f["filename"], head_sha, installation_id)
        if content is not None:
            files[f["filename"]] = content
    return {"diff": diff, "touched_files": touched, "files": files}


# ---- Checks API (needs a GitHub App; PATs cannot create check runs) ----------------------------

def check_start(repo: str, head_sha: str, installation_id: int | None, details_url: str | None = None) -> int | None:
    if not (settings.checks_enabled and installation_id and github_app.configured()):
        return None
    try:
        payload = {"name": "pr-runtime", "head_sha": head_sha, "status": "in_progress",
                   "output": {"title": "Reviewing", "summary": "Running tests and reviewing the diff."}}
        if details_url:
            payload["details_url"] = details_url
        r = _request("POST", f"{API}/repos/{repo}/check-runs", installation_id, json=payload)
        if r.status_code >= 400:
            log.warning("check_start %s: %s", r.status_code, r.text[:300])
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
