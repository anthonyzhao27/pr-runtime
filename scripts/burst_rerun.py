#!/usr/bin/env python3
"""Fire a burst of reruns through the live controller and record how the pool, the HPA and Karpenter respond.

    scripts/burst_rerun.py --config full --out /tmp/burst 10 14 15 ...   # PR numbers; newest task per PR is rerun

Runs on the driver box (or anywhere with kubectl + the controller port-forwardable). Samples /metrics, the KEDA HPA,
runner pods and NodeClaims every 5s from just before the burst until the pool is back at its floor and no NodeClaims
remain (or --settle-timeout). Writes samples.jsonl, tasks.json and prints p50/p95 of total/wait/llm/post.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

NS = "pr-runtime"
BASE = "http://localhost:18000"


def api(path: str, data: dict | None = None):
    req = urllib.request.Request(BASE + path, method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def metrics() -> dict:
    with urllib.request.urlopen(BASE + "/metrics", timeout=10) as r:
        out = {}
        for line in r.read().decode().splitlines():
            if line.startswith("prr_") and "{" not in line:
                k, v = line.split()[:2]
                out[k] = float(v)
        return out


def kube(*args: str) -> str:
    return subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True, timeout=30).stdout.strip()


def sample() -> dict:
    s = {"t": time.time()}
    try:
        s["m"] = metrics()
    except Exception as e:  # noqa: BLE001
        s["m_err"] = repr(e)
    hpa = kube("get", "hpa", "keda-hpa-runner", "-o", "jsonpath={.status.desiredReplicas} {.status.currentReplicas}")
    s["hpa_desired"], s["hpa_current"] = ([int(x) for x in hpa.split()] + [None, None])[:2] if hpa else (None, None)
    s["deploy_replicas"] = kube("get", "deploy", "runner", "-o", "jsonpath={.spec.replicas}/{.status.readyReplicas}")
    pods = kube("get", "pods", "-l", "app=runner", "-o", "jsonpath={range .items[*]}{.status.phase},{.spec.nodeName}{\"\\n\"}{end}").splitlines()
    s["pods_running"] = sum(1 for p in pods if p.startswith("Running"))
    s["pods_pending"] = sum(1 for p in pods if p.startswith("Pending"))
    s["pods_on_burst"] = sum(1 for p in pods if p.startswith("Running") and ",ip-" in p and p.split(",")[1] in burst_nodes())
    nc = subprocess.run(["kubectl", "get", "nodeclaims", "-o", "json"], capture_output=True, text=True, timeout=30).stdout
    claims = json.loads(nc).get("items", []) if nc else []
    s["nodeclaims"] = [{"name": c["metadata"]["name"], "created": c["metadata"]["creationTimestamp"],
                        "type": c.get("metadata", {}).get("labels", {}).get("node.kubernetes.io/instance-type"),
                        "capacity": c.get("metadata", {}).get("labels", {}).get("karpenter.sh/capacity-type"),
                        "ready": next((x["status"] for x in c.get("status", {}).get("conditions", []) if x["type"] == "Ready"), None),
                        "node": c.get("status", {}).get("nodeName")} for c in claims]
    return s


_burst_cache: tuple[float, set[str]] = (0.0, set())


def burst_nodes() -> set[str]:
    global _burst_cache
    if time.time() - _burst_cache[0] > 10:
        names = subprocess.run(["kubectl", "get", "nodes", "-l", "pr-runtime/pool=burst", "-o", "jsonpath={.items[*].metadata.name}"],
                               capture_output=True, text=True, timeout=30).stdout.split()
        _burst_cache = (time.time(), set(names))
    return _burst_cache[1]


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prs", nargs="+", type=int)
    ap.add_argument("--config", default="full")
    ap.add_argument("--out", default=f"/tmp/burst-{time.strftime('%Y%m%d-%H%M%S')}")
    ap.add_argument("--floor", type=int, default=4)
    ap.add_argument("--settle-timeout", type=int, default=1500)
    ap.add_argument("--fire-parallel", type=int, default=16)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    pf = subprocess.Popen(["kubectl", "-n", NS, "port-forward", "svc/controller", "18000:8000"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                api("/healthz")
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)
        stats0 = api("/api/stats")
        print("stats before:", json.dumps(stats0["scheduler"]), flush=True)

        src = {}
        for pr in a.prs:
            rows = api(f"/api/tasks?pr={pr}&limit=1")
            if rows:
                src[pr] = rows[0]["id"]
            else:
                print(f"no task for pr {pr}; skipped", flush=True)
        print(f"{len(src)} source tasks resolved", flush=True)

        samples: list[dict] = []
        stop = threading.Event()

        def sampler():
            while not stop.is_set():
                try:
                    s = sample()
                    samples.append(s)
                    with open(os.path.join(a.out, "samples.jsonl"), "a") as f:
                        f.write(json.dumps(s) + "\n")
                except Exception as e:  # noqa: BLE001
                    samples.append({"t": time.time(), "err": repr(e)})
                stop.wait(5)

        th = threading.Thread(target=sampler, daemon=True)
        th.start()
        time.sleep(6)

        t_fire = time.time()
        with ThreadPoolExecutor(max_workers=a.fire_parallel) as ex:
            new = dict(zip(src.keys(), ex.map(lambda tid: api(f"/api/tasks/{tid}/rerun", {"config": a.config})["id"], src.values())))
        t_fired = time.time()
        print(f"fired {len(new)} reruns in {t_fired - t_fire:.1f}s", flush=True)
        json.dump({"source": src, "new": new, "t_fire": t_fire, "t_fired": t_fired}, open(os.path.join(a.out, "ids.json"), "w"), indent=1)

        ids = set(new.values())
        done: dict[str, dict] = {}
        while len(done) < len(ids):
            time.sleep(5)
            for row in api("/api/tasks?limit=200"):
                if row["id"] in ids and row["state"] in ("posted", "failed", "superseded"):
                    done[row["id"]] = row
            m = samples[-1].get("m", {}) if samples else {}
            print(f"t+{time.time() - t_fire:5.0f}s done {len(done)}/{len(ids)} pending={m.get('prr_tasks_pending')} busy={m.get('prr_runners_busy')} "
                  f"ready={m.get('prr_runners_ready')} cap={m.get('prr_admission_cap')} inreview={m.get('prr_reviews_in_flight')} "
                  f"hpa={samples[-1].get('hpa_desired')}/{samples[-1].get('hpa_current')} pods={samples[-1].get('pods_running')}+{samples[-1].get('pods_pending')}p "
                  f"claims={len(samples[-1].get('nodeclaims', []))}", flush=True)
            if time.time() - t_fire > 1800:
                print("drain timeout", flush=True)
                break
        t_drained = time.time()
        tasks = [api(f"/api/tasks/{i}") for i in ids]
        json.dump(tasks, open(os.path.join(a.out, "tasks.json"), "w"), indent=1)

        # Settle: wait for the pool to come back to the floor and the burst nodes to go.
        t_settle_start = time.time()
        while time.time() - t_settle_start < a.settle_timeout:
            time.sleep(10)
            s = samples[-1]
            if s.get("hpa_current") == a.floor and s.get("pods_running") <= a.floor and not s.get("nodeclaims"):
                break
            print(f"settling t+{time.time() - t_fire:5.0f}s hpa={s.get('hpa_desired')}/{s.get('hpa_current')} pods={s.get('pods_running')} claims={len(s.get('nodeclaims', []))}", flush=True)
        stop.set()
        th.join(timeout=15)
        t_settled = time.time()

        # ---- summary --------------------------------------------------------------------------
        posted = [t for t in tasks if t["state"] == "posted"]
        print("\n== burst summary ==")
        print(f"n={len(tasks)} posted={len(posted)} failed={sum(1 for t in tasks if t['state']=='failed')} "
              f"attempts>1={sum(1 for t in tasks if t['attempts']>1)} cold={sum(1 for t in tasks if t.get('cold'))}")
        print(f"fired in {t_fired - t_fire:.1f}s; last posted at t+{max((time.mktime(time.strptime(t['posted_at'][:19], '%Y-%m-%dT%H:%M:%S')) for t in posted), default=0) - time.mktime(time.gmtime(t_fire)):.0f}s; "
              f"drained in {t_drained - t_fire:.0f}s; settled at t+{t_settled - t_fire:.0f}s")
        for k in ("total", "wait", "runner_total", "llm", "post"):
            xs = [t["timings"].get(k) for t in posted if t.get("timings") and t["timings"].get(k) is not None]
            if xs:
                print(f"{k:13s} n={len(xs):3d} p50={statistics.median(xs):7.1f} p95={pct(xs, .95):7.1f} max={max(xs):7.1f}")
        ti, to = sum(t["tokens_in"] or 0 for t in posted), sum(t["tokens_out"] or 0 for t in posted)
        print(f"tokens in={ti} out={to} cost tokens=${sum(t['cost_tokens_usd'] or 0 for t in posted):.3f} compute=${sum(t['cost_compute_usd'] or 0 for t in posted):.5f}")
        hp = [s for s in samples if s.get("hpa_current") is not None]
        if hp:
            mx = max(hp, key=lambda s: s["hpa_current"])
            first_up = next((s for s in hp if s["hpa_desired"] and s["hpa_desired"] > a.floor), None)
            print(f"hpa max current={mx['hpa_current']} at t+{mx['t'] - t_fire:.0f}s; first desired>{a.floor} at t+{(first_up['t'] - t_fire) if first_up else float('nan'):.0f}s "
                  f"(desired={first_up['hpa_desired'] if first_up else None})")
            print(f"max running runner pods={max(s.get('pods_running', 0) for s in samples)}; max on burst nodes={max(s.get('pods_on_burst', 0) for s in samples)}; "
                  f"max ready (controller)={max(s.get('m', {}).get('prr_runners_ready', 0) for s in samples)}; max cap={max(s.get('m', {}).get('prr_admission_cap', 0) for s in samples)}")
        seen = {}
        for s in samples:
            for c in s.get("nodeclaims", []):
                seen.setdefault(c["name"], {**c, "first_seen": s["t"] - t_fire})
                if c["ready"] == "True" and "ready_at" not in seen[c["name"]]:
                    seen[c["name"]]["ready_at"] = s["t"] - t_fire
                seen[c["name"]]["last_seen"] = s["t"] - t_fire
        for n, c in seen.items():
            print(f"nodeclaim {n}: {c.get('type')} {c.get('capacity')} first_seen t+{c['first_seen']:.0f}s ready t+{c.get('ready_at', float('nan')):.0f}s last_seen t+{c['last_seen']:.0f}s")
        print(f"final: hpa={samples[-1].get('hpa_desired')}/{samples[-1].get('hpa_current')} pods={samples[-1].get('pods_running')} claims={len(samples[-1].get('nodeclaims', []))}")
        print(f"samples: {os.path.join(a.out, 'samples.jsonl')}  tasks: {os.path.join(a.out, 'tasks.json')}")
        return 0
    finally:
        pf.terminate()


if __name__ == "__main__":
    sys.exit(main())
