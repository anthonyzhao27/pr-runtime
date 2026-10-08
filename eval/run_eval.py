#!/usr/bin/env python3
"""Run the eval: open one PR per corpus branch, let the runtime review it under each config, collect results.

Opening the PRs in one burst is also the load test. Requires `kubectl port-forward svc/controller 18000:8000`
(or --api pointing at the controller) and the active `gh` login.

  eval/run_eval.py --configs full,diff_only
  eval/run_eval.py --reuse-prs        # don't open new PRs; re-run configs on existing corpus PRs
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
REPO = "anthonyzhao27/flask"


def gh(*a) -> str:
    return subprocess.run(["gh", *a], check=True, capture_output=True, text=True).stdout.strip()


def ensure_pr(entry: dict) -> int:
    head = entry["branch"]
    base = entry.get("base_branch", "main")
    existing = gh("pr", "list", "--repo", REPO, "--head", head, "--state", "open", "--json", "number", "--jq", ".[0].number")
    if existing:
        return int(existing)
    title = subprocess.run(["git", "log", "-1", "--format=%s", f"origin/{head}"], capture_output=True, text=True).stdout.strip() or head
    url = gh("pr", "create", "--repo", REPO, "--base", base, "--head", head, "--title", title or head,
             "--body", "Small cleanup. Tests pass locally.")
    return int(url.rstrip("/").split("/")[-1])


def wait_posted(api: str, pr: int, config: str, since_iso: str, timeout: int = 900) -> dict | None:
    t0 = time.time()
    first = True
    while first or time.time() - t0 < timeout:
        first = False
        tasks = httpx.get(f"{api}/api/tasks", params={"pr": pr, "limit": 20}, timeout=20).json()
        for t in tasks:
            if t["config"] == config and t["created_at"] >= since_iso and t["state"] in ("posted", "failed"):
                return httpx.get(f"{api}/api/tasks/{t['id']}", timeout=20).json()
        time.sleep(3)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:18000")
    ap.add_argument("--configs", default="full,diff_only")
    ap.add_argument("--reuse-prs", action="store_true")
    ap.add_argument("--only", help="comma list of variants: red,green,clean")
    ap.add_argument("--run-id", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--corpus", default=str(HERE / "corpus" / "corpus.json"))
    ap.add_argument("--limit", type=int, help="first N entries only (smoke test)")
    ap.add_argument("--accept-existing", action="store_true",
                    help="for non-default configs, reuse an already-posted task for that PR+config instead of firing a rerun")
    a = ap.parse_args()
    CORPUS = json.loads(Path(a.corpus).read_text())
    configs = a.configs.split(",")
    entries = [e for e in CORPUS if not a.only or e["variant"] in a.only.split(",")]
    if a.limit:
        entries = entries[: a.limit]
    out_dir = HERE / "results" / a.run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    print(f"run {a.run_id}: {len(entries)} PRs x {configs}", file=sys.stderr)

    # 1) PRs. Opening them all at once = burst. The webhook creates the default-config task.
    with ThreadPoolExecutor(6) as ex:
        prs = list(ex.map(ensure_pr, entries))
    for e, pr in zip(entries, prs):
        e["pr"] = pr
    print(f"PRs ready: {prs}", file=sys.stderr)

    # 2) Default config comes from the webhook (burst #1). Wait for all of them.
    default_cfg = configs[0]
    since_default = "1970" if a.reuse_prs else since
    results = []
    firsts: dict[int, dict] = {}
    with ThreadPoolExecutor(8) as ex:
        for e, t in zip(entries, ex.map(lambda e: wait_posted(a.api, e["pr"], default_cfg, since_default), entries)):
            if t is None:
                print(f"  pr#{e['pr']} {e['branch']}: no result for {default_cfg}", file=sys.stderr)
                continue
            firsts[e["pr"]] = t
            results.append({**e, "config": default_cfg, "task": t})
            print(f"  pr#{e['pr']} {e['variant']:5s} {default_cfg:9s} verdict={t.get('verdict')} findings={len(t.get('findings', []))} "
                  f"total={t.get('timings', {}).get('total')}", file=sys.stderr)

    # 3) Every other config: fire all reruns at once (burst #2..n), then collect.
    for cfg in configs[1:]:
        mark = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        todo = [e for e in entries if e["pr"] in firsts]
        existing: dict[int, dict] = {}
        if a.accept_existing:
            for e in todo:
                t = wait_posted(a.api, e["pr"], cfg, "1970", timeout=0)
                if t and t["state"] == "posted":
                    existing[e["pr"]] = t
            print(f"accepting {len(existing)} existing posted tasks for config {cfg}", file=sys.stderr)
        fire = [e for e in todo if e["pr"] not in existing]
        for e in fire:
            httpx.post(f"{a.api}/api/tasks/{firsts[e['pr']]['id']}/rerun", json={"config": cfg}, timeout=20)
        print(f"fired {len(fire)} reruns for config {cfg}", file=sys.stderr)
        with ThreadPoolExecutor(8) as ex:
            for e, t in zip(todo, ex.map(lambda e: existing.get(e["pr"]) or wait_posted(a.api, e["pr"], cfg, mark), todo)):
                if t is None:
                    print(f"  pr#{e['pr']} {e['branch']}: no result for {cfg}", file=sys.stderr)
                    continue
                results.append({**e, "config": cfg, "task": t})
                print(f"  pr#{e['pr']} {e['variant']:5s} {cfg:9s} verdict={t.get('verdict')} findings={len(t.get('findings', []))} "
                      f"total={t.get('timings', {}).get('total')}", file=sys.stderr)

    (out_dir / "raw.json").write_text(json.dumps(results, indent=1))
    print(f"wrote {out_dir / 'raw.json'} ({len(results)} task results)", file=sys.stderr)


if __name__ == "__main__":
    main()
