#!/usr/bin/env python3
"""Fire a burst through the real ingress path (SQS -> controller) and watch the pool, HPA and NodeClaims respond.

    scripts/burst_sqs.py --repo anthonyzhao27/flask --installation 12345 --out /tmp/burst 10 14 15 ...

Each PR number becomes one `issue_comment` message on the task queue, shaped like the Lambda would enqueue for an
`@pr-runtime review` mention. The controller reacts (fails harmlessly on the fake comment id), looks up the PR head
and reviews it as a mention task, so duplicates of already-reviewed heads are allowed. Unlike burst_rerun.py this
exercises SQS leasing and the aws-sqs-queue KEDA trigger. Needs AWS creds (AWS_PROFILE) that can send to the queue,
kubectl, and the controller port-forwarded on localhost:18000 for metrics sampling.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request

import boto3

NS = "pr-runtime"
BASE = "http://localhost:18000"


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


def sample(sqs, queue_url: str) -> dict:
    s: dict = {"t": time.time()}
    try:
        s["m"] = metrics()
    except Exception as e:  # noqa: BLE001
        s["m_err"] = repr(e)
    try:
        a = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages",
                                                                          "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        s["sqs"] = {"visible": int(a["ApproximateNumberOfMessages"]), "inflight": int(a["ApproximateNumberOfMessagesNotVisible"])}
    except Exception as e:  # noqa: BLE001
        s["sqs_err"] = repr(e)
    hpa = kube("get", "hpa", "keda-hpa-runner", "-o", "json")
    if hpa:
        h = json.loads(hpa)
        s["hpa"] = {"desired": h["status"].get("desiredReplicas"), "current": h["status"].get("currentReplicas"),
                    "metrics": [m.get("external", {}).get("current", {}).get("averageValue") or
                                m.get("external", {}).get("current", {}).get("value")
                                for m in h["status"].get("currentMetrics") or []]}
    pods = kube("get", "pods", "-l", "app=runner", "-o", "json")
    if pods:
        items = json.loads(pods)["items"]
        s["runners"] = {"ready": sum(1 for p in items if any(c.get("type") == "Ready" and c.get("status") == "True"
                                                               for c in p["status"].get("conditions", []))),
                        "total": len(items)}
    nc = subprocess.run(["kubectl", "get", "nodeclaims", "-o", "json"], capture_output=True, text=True, timeout=30).stdout
    if nc:
        s["nodeclaims"] = len(json.loads(nc)["items"])
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prs", nargs="+", type=int)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--installation", type=int, required=True)
    ap.add_argument("--queue-url", default=os.environ.get("QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/511572480852/pr-runtime-tasks"))
    ap.add_argument("--out", default="/tmp/burst-sqs")
    ap.add_argument("--settle-timeout", type=int, default=900)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sqs = boto3.client("sqs", region_name="us-east-1")
    samples = [sample(sqs, a.queue_url)]
    print("before:", json.dumps(samples[0]), flush=True)

    t0 = time.time()
    entries = []
    for pr in a.prs:
        body = {"action": "created", "repository": {"full_name": a.repo}, "installation": {"id": a.installation},
                "issue": {"number": pr, "pull_request": {"url": f"https://api.github.com/repos/{a.repo}/pulls/{pr}"}},
                "comment": {"id": 0, "body": "@pr-runtime review (burst_sqs)"}}
        entries.append({"Id": str(pr), "MessageBody": json.dumps(body), "MessageAttributes": {
            "event": {"DataType": "String", "StringValue": "issue_comment"},
            "action": {"DataType": "String", "StringValue": "created"},
            "delivery": {"DataType": "String", "StringValue": f"burst-sqs-{int(t0)}-{pr}"}}})
    for i in range(0, len(entries), 10):
        sqs.send_message_batch(QueueUrl=a.queue_url, Entries=entries[i:i + 10])
    print(f"sent {len(entries)} messages in {time.time() - t0:.1f}s", flush=True)

    floor = None
    while True:
        s = sample(sqs, a.queue_url)
        samples.append(s)
        m = s.get("m", {})
        print(f"t+{s['t'] - t0:5.0f}s sqs={s.get('sqs')} pending={m.get('prr_tasks_pending')} busy={m.get('prr_runners_busy')} "
              f"review={m.get('prr_reviews_in_flight')} hpa={s.get('hpa')} runners={s.get('runners')} nodeclaims={s.get('nodeclaims')}",
              flush=True)
        if floor is None and s.get("hpa"):
            floor = int(kube("get", "scaledobject", "runner", "-o", "jsonpath={.spec.minReplicaCount}") or 4)
        done = (s.get("sqs", {}).get("visible", 1) == 0 and s.get("sqs", {}).get("inflight", 1) == 0
                and m.get("prr_runners_busy", 1) == 0 and m.get("prr_reviews_in_flight", 1) == 0
                and s.get("hpa", {}).get("current") == floor and s.get("nodeclaims", 1) == 0)
        if (done and time.time() - t0 > 45) or time.time() - t0 > a.settle_timeout:
            break
        time.sleep(5)
    with open(os.path.join(a.out, "samples.jsonl"), "w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")

    # Per-task timings from the controller API for the tasks this burst created.
    with urllib.request.urlopen(BASE + "/api/tasks?limit=500", timeout=30) as r:
        tasks = json.loads(r.read())
    tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
    import datetime as dt
    since = dt.datetime.fromtimestamp(t0 - 2, dt.timezone.utc).isoformat()
    mine = [t for t in tasks if t.get("action") == "mention" and (t.get("created_at") or "") >= since]
    with open(os.path.join(a.out, "tasks.json"), "w") as f:
        json.dump(mine, f, indent=1)
    tot = [t["timings"].get("total") for t in mine if t.get("timings") and t["timings"].get("total")]
    wait = [t["timings"].get("wait") for t in mine if t.get("timings") and t["timings"].get("wait") is not None]
    states = {}
    for t in mine:
        states[t.get("state")] = states.get(t.get("state"), 0) + 1
    print(f"tasks={len(mine)} states={states}")
    if tot:
        print(f"time-to-comment p50={statistics.median(tot):.0f}s p95={sorted(tot)[int(len(tot) * 0.95) - 1]:.0f}s")
    if wait:
        print(f"wait-for-runner p50={statistics.median(wait):.0f}s")
    peak = max((s.get("hpa", {}).get("current") or 0) for s in samples)
    peak_nc = max((s.get("nodeclaims") or 0) for s in samples)
    print(f"pool peak={peak} nodeclaims peak={peak_nc} settled_in={samples[-1]['t'] - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
