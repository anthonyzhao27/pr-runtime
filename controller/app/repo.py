"""Read-only repo access for the reviewer's tools (S2), served from a bare mirror inside the trusted controller.

Nothing from the PR is executed here: `git show`, `git grep`, `git ls-tree` on a given sha only.
The mirror lives in /tmp (writable emptyDir); it is cloned on first use and fetched per task.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading

log = logging.getLogger("repo")
_lock = threading.Lock()
MIRROR_ROOT = os.environ.get("MIRROR_ROOT", "/tmp/mirrors")


def _mirror(repo: str) -> str:
    path = os.path.join(MIRROR_ROOT, repo.replace("/", "__") + ".git")
    with _lock:
        if not os.path.isdir(path):
            os.makedirs(MIRROR_ROOT, exist_ok=True)
            subprocess.run(["git", "clone", "--quiet", "--mirror", f"https://github.com/{repo}.git", path], check=True, timeout=300)
    return path


def ensure_sha(repo: str, sha: str) -> str:
    path = _mirror(repo)
    if subprocess.run(["git", "-C", path, "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode != 0:
        with _lock:
            subprocess.run(["git", "-C", path, "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*", "+refs/pull/*:refs/pull/*"],
                           capture_output=True, timeout=120)
    return path


def _git(path: str, *args: str, timeout: int = 20) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout if p.returncode == 0 else p.stderr)


def read_file(repo: str, sha: str, rel: str, max_chars: int = 40_000) -> str:
    rc, out = _git(ensure_sha(repo, sha), "show", f"{sha}:{rel.lstrip('/')}")
    if rc != 0:
        return f"error: {rel} not found at {sha[:10]}"
    return out if len(out) <= max_chars else out[:max_chars] + f"\n... [{len(out) - max_chars} chars truncated]"


def grep(repo: str, sha: str, pattern: str, path_glob: str = "", max_lines: int = 80) -> str:
    args = ["grep", "-n", "-I", "--no-color", "-e", pattern, sha]
    if path_glob:
        args += ["--", path_glob]
    rc, out = _git(ensure_sha(repo, sha), *args)
    if rc == 1 or not out.strip():
        return "no matches"
    if rc != 0:
        return f"error: {out.strip()[:200]}"
    lines = [l.split(":", 1)[1] if l.startswith(sha) else l for l in out.splitlines()]  # strip the sha prefix
    more = f"\n... [{len(lines) - max_lines} more]" if len(lines) > max_lines else ""
    return "\n".join(lines[:max_lines]) + more


def list_dir(repo: str, sha: str, rel: str = "") -> str:
    rel = rel.strip("/")
    rc, out = _git(ensure_sha(repo, sha), "ls-tree", "--name-only", f"{sha}:{rel}" if rel else sha)
    return out if rc == 0 else f"error: {rel or '/'} not found at {sha[:10]}"
