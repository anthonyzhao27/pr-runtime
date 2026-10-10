"""Untrusted runner: fetch a PR head for ANY repo, run its tests and linter, ship raw results back.

Holds no secrets. Clones public repos anonymously (private repos: the controller would have to hand over a
scoped token; not implemented). Serves exactly one task; the controller deletes the pod afterwards.

Repo toolchain, in order of precedence:
  1. `.pr-runtime.yml` at the PR head:   install: ..., test: ..., lint: ..., timeout: 300
  2. detection: uv.lock / pyproject.toml -> `uv sync` + `uv run pytest`;  requirements.txt -> venv + pip;
                package.json -> `npm ci` + `npm test`;  go.mod -> `go test ./...`;  else: no tests (diff-only review)
A baked seed clone (SEED_DIR, e.g. the Flask fork) is reused when the task's repo matches it; that is the warm path.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEED = os.environ.get("SEED_DIR", "/opt/seed/flask")
SEED_REPO = os.environ.get("SEED_REPO", "anthonyzhao27/flask")
WORK_ROOT = os.environ.get("WORK_ROOT", "/work")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
MAX_OUTPUT = 30_000
MAX_FILE = 200_000
DEFAULT_TIMEOUT = int(os.environ.get("TASK_DEADLINE_SECONDS", "300"))

STATE = {"state": "booting", "task_id": None, "started_at": time.time()}


def sh(args, cwd: str, timeout: int = 240, shell: bool = False) -> tuple[int, str, float]:
    t0 = time.monotonic()
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell,
                           env={**os.environ, "CI": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        out = (p.stdout + ("\n" + p.stderr if p.stderr else ""))[-MAX_OUTPUT:]
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))[-MAX_OUTPUT:]
        out += f"\n[timed out after {timeout}s]"
        rc = 124
    return rc, out, time.monotonic() - t0


# ---- workdir ----------------------------------------------------------------------------------

def prepare_workdir(repo: str, clone_url: str) -> tuple[str, float, str]:
    """Return (path, seconds, how). Reuse the baked seed when it is the same repo; otherwise partial-clone."""
    t0 = time.monotonic()
    work = os.path.join(WORK_ROOT, repo.replace("/", "__"))
    marker = os.path.join(work, ".pr-runtime-seed")
    if os.path.isdir(os.path.join(work, ".git")):
        # Staged at boot from the baked seed (deps already installed) or cloned earlier in this pod.
        return work, time.monotonic() - t0, "seed" if os.path.exists(marker) else "reused"
    if repo == SEED_REPO and os.path.isdir(SEED):
        shutil.copytree(SEED, work, symlinks=True)
        open(marker, "w").close()
        return work, time.monotonic() - t0, "seed"
    os.makedirs(WORK_ROOT, exist_ok=True)
    rc, out, _ = sh(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", clone_url, work], cwd=WORK_ROOT, timeout=180)
    if rc != 0:
        raise RuntimeError(f"git clone failed: {out[-500:]}")
    sh(["git", "config", "user.email", "runner@pr-runtime.local"], cwd=work)
    sh(["git", "config", "user.name", "runner"], cwd=work)
    return work, time.monotonic() - t0, "clone"


# ---- toolchain --------------------------------------------------------------------------------

def load_repo_config(work: str) -> dict:
    """`.pr-runtime.yml` without a YAML dependency: flat `key: value` lines only."""
    cfg: dict = {}
    for name in (".pr-runtime.yml", ".pr-runtime.yaml"):
        path = os.path.join(work, name)
        if os.path.isfile(path):
            for line in open(path, errors="replace"):
                line = line.split("#", 1)[0].strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    cfg[k.strip()] = v.strip().strip("'\"")
            cfg["_source"] = name
            break
    return cfg


def detect_toolchain(work: str, cfg: dict) -> dict:
    """Commands to run, as shell strings (run with shell=True inside the untrusted pod)."""
    tc = {"language": "unknown", "install": cfg.get("install"), "test": cfg.get("test"), "lint": cfg.get("lint"),
          "timeout": int(cfg.get("timeout", DEFAULT_TIMEOUT))}
    has = lambda f: os.path.exists(os.path.join(work, f))  # noqa: E731
    if has("uv.lock") or has("pyproject.toml"):
        tc["language"] = "python"
        tc["install"] = tc["install"] or ("uv sync --all-groups --quiet 2>/dev/null || uv sync --quiet || uv pip install --quiet -e .")
        tc["test"] = tc["test"] or "uv run --no-sync pytest -q -p no:cacheprovider --tb=short -rf"
        tc["lint"] = tc["lint"] or "ruff check --output-format concise ."
    elif has("requirements.txt") or has("setup.py"):
        tc["language"] = "python"
        tc["install"] = tc["install"] or ("uv venv --quiet .venv && uv pip install --quiet -r requirements.txt pytest"
                                          if has("requirements.txt") else "uv venv --quiet .venv && uv pip install --quiet -e . pytest")
        tc["test"] = tc["test"] or ".venv/bin/python -m pytest -q -p no:cacheprovider --tb=short -rf"
        tc["lint"] = tc["lint"] or "ruff check --output-format concise ."
    elif has("package.json"):
        tc["language"] = "node"
        tc["install"] = tc["install"] or ("npm ci --no-audit --no-fund --silent" if has("package-lock.json") else "npm install --no-audit --no-fund --silent")
        tc["test"] = tc["test"] or "npm test --silent"
        tc["lint"] = tc["lint"] or "npx --yes eslint . 2>/dev/null || true"
    elif has("go.mod"):
        tc["language"] = "go"
        tc["install"] = tc["install"] or "go mod download"
        tc["test"] = tc["test"] or "go test ./..."
        tc["lint"] = tc["lint"] or "go vet ./..."
    return tc


# ---- task -------------------------------------------------------------------------------------

def run_task(task: dict) -> dict:
    head, base = task["head_sha"], task["base_sha"]
    repo = task.get("repo", SEED_REPO)
    clone_url = task.get("clone_url") or f"https://github.com/{repo}.git"
    timings: dict[str, float] = {}
    result: dict = {"task_id": task["task_id"], "repo": repo, "head_sha": head, "base_sha": base, "timings": timings}

    work, dt, how = prepare_workdir(repo, clone_url)
    timings["prepare"] = dt
    result["workdir"] = how

    rc, out, dt = sh(["git", "fetch", "--quiet", "origin", head, base], cwd=work, timeout=180)
    timings["fetch"] = dt
    if rc != 0:
        result["error"] = f"git fetch failed: {out[-500:]}"
        return result
    rc, out, dt = sh(["git", "checkout", "--quiet", "--force", head], cwd=work)
    timings["checkout"] = dt
    if rc != 0:
        result["error"] = f"git checkout failed: {out[-500:]}"
        return result

    _, merge_base, _ = sh(["git", "merge-base", base, head], cwd=work)
    merge_base = merge_base.strip() or base
    _, diff, dt = sh(["git", "diff", "--unified=3", f"{merge_base}..{head}"], cwd=work)
    timings["diff"] = dt
    result["diff"] = diff[-MAX_OUTPUT * 4:]
    _, names, _ = sh(["git", "diff", "--name-only", f"{merge_base}..{head}"], cwd=work)
    touched = [n for n in names.split("\n") if n.strip()]
    result["touched_files"] = touched

    files: dict[str, str] = {}
    for rel in touched:
        path = os.path.join(work, rel)
        if os.path.isfile(path) and os.path.getsize(path) <= MAX_FILE * 2:
            try:
                with open(path, "r", errors="replace") as f:
                    files[rel] = f.read(MAX_FILE)
            except OSError:
                pass
    result["files"] = files

    cfg = load_repo_config(work)
    tc = detect_toolchain(work, cfg)
    result["toolchain"] = tc
    deadline = tc["timeout"]

    if tc["install"] and how != "seed":
        rc, out, dt = sh(tc["install"], cwd=work, timeout=deadline, shell=True)
        timings["install"] = dt
        result["install"] = {"returncode": rc, "output": out[-4000:]}
    else:
        timings["install"] = 0.0

    if tc["test"]:
        rc, out, dt = sh(tc["test"], cwd=work, timeout=deadline, shell=True)
        timings["pytest"] = dt
        result["pytest"] = {"returncode": rc, "output": out, "command": tc["test"]}
    else:
        result["pytest"] = {"returncode": None, "output": "no test command detected", "command": None}
    if tc["lint"]:
        rc, out, dt = sh(tc["lint"], cwd=work, timeout=120, shell=True)
        timings["ruff"] = dt
        result["ruff"] = {"returncode": rc, "output": out[-8000:], "command": tc["lint"]}
    else:
        result["ruff"] = {"returncode": None, "output": "", "command": None}

    result["total_seconds"] = sum(timings.values())
    return result


def post_result(result: dict) -> None:
    if not CONTROLLER_URL:
        print(json.dumps(result), flush=True)
        return
    url = f"{CONTROLLER_URL.rstrip('/')}/result/{result['task_id']}"
    data = json.dumps(result).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            return
        except Exception as e:  # noqa: BLE001
            print(f"post_result attempt {attempt} failed: {e}", file=sys.stderr, flush=True)
            time.sleep(2 * (attempt + 1))


def do_task_and_exit(task: dict) -> None:
    try:
        result = run_task(task)
    except Exception as e:  # noqa: BLE001
        result = {"task_id": task.get("task_id"), "error": f"runner exception: {e!r}"}
    post_result(result)
    print(f"task {task.get('task_id')} done in {result.get('total_seconds', 0):.1f}s", flush=True)
    if os.environ.get("RUNNER_MODE", "server") == "job":
        os._exit(0)
    STATE.update(state="done", finished_at=time.time())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if self.path != "/healthz":
            super().log_message(fmt, *args)

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200 if STATE["state"] == "idle" else 503, STATE)
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/task":
            return self._json(404, {"error": "not found"})
        if STATE["state"] != "idle":
            return self._json(409, {"error": "busy", **STATE})
        n = int(self.headers.get("Content-Length", "0"))
        task = json.loads(self.rfile.read(n) or b"{}")
        for k in ("task_id", "head_sha", "base_sha"):
            if k not in task:
                return self._json(400, {"error": f"missing {k}"})
        STATE.update(state="busy", task_id=task["task_id"], accepted_at=time.time())
        threading.Thread(target=do_task_and_exit, args=(task,), daemon=True).start()
        self._json(202, {"accepted": task["task_id"]})


def main() -> None:
    mode = os.environ.get("RUNNER_MODE", "server")
    if mode == "job":
        if os.environ.get("TASK_FILE"):
            if not os.path.exists(os.environ["TASK_FILE"]):
                print("no task file; nothing to do", flush=True)
                return
            with open(os.environ["TASK_FILE"]) as f:
                task = json.load(f)
        else:
            task = json.loads(os.environ["TASK_JSON"])
        do_task_and_exit(task)
        return
    # Warm up: stage the seed repo so the common case (the seed repo) skips clone + install entirely.
    t = 0.0
    if os.path.isdir(SEED):
        _, t, _ = prepare_workdir(SEED_REPO, f"https://github.com/{SEED_REPO}.git")
    STATE.update(state="idle", warm_seconds=round(t, 3), seed_repo=SEED_REPO)
    print(f"runner idle on :{PORT} (seed {SEED_REPO} ready in {t:.2f}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
