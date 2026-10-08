"""Untrusted runner: fetch a PR head, run the test suite and linter, ship raw results back.

Holds no secrets. Clones the public fork anonymously. Exits after one task so the pod is
ephemeral per task; the Deployment's ReplicaSet replaces it, which is the warm-pool refill.

Modes:
  server (default): idle HTTP server, accepts one POST /task, then exits.
  job:              read TASK_JSON from env, run once, print result JSON, exit (KEDA baseline).
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
WORK = os.environ.get("WORK_DIR", "/work/flask")
REPO_URL = os.environ.get("REPO_URL", "https://github.com/anthonyzhao27/flask.git")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
MAX_OUTPUT = 30_000
MAX_FILE = 200_000
TASK_DEADLINE = int(os.environ.get("TASK_DEADLINE_SECONDS", "300"))

STATE = {"state": "booting", "task_id": None, "started_at": time.time()}


def sh(args: list[str], cwd: str = WORK, timeout: int = 240) -> tuple[int, str, float]:
    t0 = time.monotonic()
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout + ("\n" + p.stderr if p.stderr else ""))[-MAX_OUTPUT:]
    return p.returncode, out, time.monotonic() - t0


def prepare_workdir() -> float:
    """Copy the baked seed clone into the writable emptyDir. Root fs is read-only."""
    t0 = time.monotonic()
    if not os.path.exists(WORK):
        shutil.copytree(SEED, WORK, symlinks=True)
    return time.monotonic() - t0


def run_task(task: dict) -> dict:
    head, base = task["head_sha"], task["base_sha"]
    timings: dict[str, float] = {}
    result: dict = {"task_id": task["task_id"], "head_sha": head, "base_sha": base, "timings": timings}

    timings["prepare"] = prepare_workdir()

    rc, out, dt = sh(["git", "fetch", "--quiet", "origin", head, base])
    timings["fetch"] = dt
    if rc != 0:
        result["error"] = f"git fetch failed: {out}"
        return result
    rc, out, dt = sh(["git", "checkout", "--quiet", "--force", head])
    timings["checkout"] = dt
    if rc != 0:
        result["error"] = f"git checkout failed: {out}"
        return result

    _, merge_base, _ = sh(["git", "merge-base", base, head])
    merge_base = merge_base.strip()
    _, diff, dt = sh(["git", "diff", "--unified=3", f"{merge_base}..{head}"])
    timings["diff"] = dt
    result["diff"] = diff[-MAX_OUTPUT * 4:]

    _, names, _ = sh(["git", "diff", "--name-only", f"{merge_base}..{head}"])
    touched = [n for n in names.split("\n") if n.strip()]
    result["touched_files"] = touched

    files: dict[str, str] = {}
    for rel in touched:
        path = os.path.join(WORK, rel)
        if os.path.isfile(path) and rel.endswith((".py", ".rst", ".md", ".toml", ".cfg", ".txt", ".html", ".txt")):
            with open(path, "r", errors="replace") as f:
                files[rel] = f.read(MAX_FILE)
    result["files"] = files

    rc, out, dt = sh(["uv", "run", "--no-sync", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "-rf"], timeout=TASK_DEADLINE)
    timings["pytest"] = dt
    result["pytest"] = {"returncode": rc, "output": out}

    rc, out, dt = sh(["uv", "run", "--no-sync", "ruff", "check", "--output-format", "concise", "src", "tests"])
    timings["ruff"] = dt
    result["ruff"] = {"returncode": rc, "output": out}

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
    print(f"task {task.get('task_id')} done in {result.get('total_seconds', 0):.1f}s; exiting", flush=True)
    sys.stdout.flush()
    os._exit(0)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
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
            self._json(200, STATE)
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
    # Warm up: copy seed into the writable workdir before declaring idle.
    t = prepare_workdir()
    STATE.update(state="idle", warm_seconds=round(t, 3))
    print(f"runner idle on :{PORT} (workdir ready in {t:.2f}s)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
