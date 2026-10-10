"""GitHub App auth (S4): sign a JWT with the App's private key, exchange it for a short-lived
installation token scoped to one installation, cache it. Falls back to GITHUB_BOT_TOKEN when the
App is not configured, so the PAT path keeps working.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx
import jwt

log = logging.getLogger("github_app")
API = "https://api.github.com"
_lock = threading.Lock()
_cache: dict[int, tuple[str, float]] = {}  # installation_id -> (token, expires_at)


def configured() -> bool:
    return bool(os.environ.get("GITHUB_APP_ID") and os.environ.get("GITHUB_APP_PRIVATE_KEY"))


def app_jwt() -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": os.environ["GITHUB_APP_ID"]}
    return jwt.encode(payload, os.environ["GITHUB_APP_PRIVATE_KEY"], algorithm="RS256")


def installation_token(installation_id: int) -> str:
    """Token valid ~1h, limited to the installation's repos and the App's declared permissions."""
    with _lock:
        tok = _cache.get(installation_id)
        if tok and tok[1] - time.time() > 120:
            return tok[0]
        r = httpx.post(f"{API}/app/installations/{installation_id}/access_tokens",
                       headers={"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"}, timeout=20)
        r.raise_for_status()
        j = r.json()
        expires = time.time() + 55 * 60
        _cache[installation_id] = (j["token"], expires)
        log.info("minted installation token for %s (perms: %s)", installation_id, ",".join(sorted(j.get("permissions", {}))))
        return j["token"]


def token_for(installation_id: int | None) -> str:
    """The credential to use for a given task: App installation token if possible, else the PAT."""
    if installation_id and configured():
        try:
            return installation_token(installation_id)
        except Exception as e:  # noqa: BLE001
            log.error("installation token failed for %s: %s; falling back to PAT", installation_id, e)
    return os.environ.get("GITHUB_BOT_TOKEN", "")


def app_slug() -> str | None:
    """The App's login, e.g. 'pr-runtime[bot]', for ignoring our own comments."""
    if not configured():
        return None
    try:
        r = httpx.get(f"{API}/app", headers={"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json"}, timeout=10)
        r.raise_for_status()
        return r.json().get("slug")
    except Exception:  # noqa: BLE001
        return None
