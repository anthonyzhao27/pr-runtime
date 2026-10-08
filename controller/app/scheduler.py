"""Control plane: admission cap, ranking, warm-pool assignment, deadlines, result handling, review, egress."""
from __future__ import annotations

import datetime as dt
import heapq
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import events, github, metrics, reviewer
from .config import settings
from .db import Finding, SessionLocal, Task, now
from .k8s import RunnerPods

log = logging.getLogger("scheduler")
WARM_AGE_SECONDS = 10.0


class Scheduler:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: list[tuple[int, float, str]] = []  # (priority, created_ts, task_id)
        self.busy: dict[str, dict] = {}  # task_id -> {pod, ip, deadline, assigned_at}
        self.assigned_pods: set[str] = set()
        self.bad_pods: dict[str, float] = {}
        self.pods = RunnerPods()
        self.review_pool = ThreadPoolExecutor(max_workers=settings.review_workers, thread_name_prefix="review")
        self._stop = threading.Event()

    # ---- queue side -------------------------------------------------------------------------
    def enqueue(self, task_id: str, priority: int) -> None:
        with self.lock:
            heapq.heappush(self.pending, (priority, time.time(), task_id))
            metrics.tasks_pending.set(len(self.pending))
        events.publish("task.queued", {"id": task_id, "priority": priority})

    def supersede(self, repo: str, pr_number: int, keep_task_id: str) -> None:
        """A new push to the same PR makes older in-flight work pointless."""
        with SessionLocal() as s:
            olds = s.query(Task).filter(Task.repo == repo, Task.pr_number == pr_number, Task.id != keep_task_id,
                                        Task.state.in_(("queued", "admitted", "running", "reviewing"))).all()
            for t in olds:
                t.state = "superseded"
                with self.lock:
                    self.pending = [p for p in self.pending if p[2] != t.id]
                    heapq.heapify(self.pending)
                    b = self.busy.pop(t.id, None)
                if b:
                    self.pods.delete(b["pod"])
                    self.assigned_pods.discard(b["pod"])
                metrics.tasks_total.labels(state="superseded").inc()
                events.publish("task.updated", {"id": t.id, "state": "superseded"})
            s.commit()

    # ---- main loop --------------------------------------------------------------------------
    def run(self) -> None:
        log.info("scheduler started cap=%s deadline=%ss", settings.admission_cap, settings.task_deadline)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            time.sleep(1.0)

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        ready = self.pods.list_ready()
        nowt = time.time()
        self.bad_pods = {k: v for k, v in self.bad_pods.items() if nowt - v < 60}
        idle = [p for p in ready if p["name"] not in self.assigned_pods and p["name"] not in self.bad_pods]
        # Warmest first: the oldest ready pod has had the longest to settle.
        idle.sort(key=lambda p: p["started"])

        with self.lock:
            metrics.runners_idle.set(len(idle))
            metrics.runners_busy.set(len(self.busy))
            metrics.reviews_in_flight.set(self.review_pool._work_queue.qsize() + sum(1 for t in self.review_pool._threads if t.is_alive()) if hasattr(self.review_pool, "_threads") else 0)
            metrics.tasks_pending.set(len(self.pending))

            if self.pending and len(self.busy) >= settings.admission_cap:
                metrics.admission_rejects.inc()

            to_assign: list[tuple[str, dict]] = []
            while self.pending and len(self.busy) + len(to_assign) < settings.admission_cap and idle:
                _, _, task_id = heapq.heappop(self.pending)
                pod = idle.pop(0)
                to_assign.append((task_id, pod))

            expired = [tid for tid, b in self.busy.items() if nowt > b["deadline"]]

        for task_id, pod in to_assign:
            self._assign(task_id, pod)
        for tid in expired:
            self._expire(tid)

    def _assign(self, task_id: str, pod: dict) -> None:
        age = (dt.datetime.now(dt.timezone.utc) - pod["started"]).total_seconds()
        cold = age < WARM_AGE_SECONDS
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None or t.state not in ("queued",):
                return
            payload = {"task_id": t.id, "head_sha": t.head_sha, "base_sha": t.base_sha, "pr_number": t.pr_number}
            try:
                r = httpx.post(f"http://{pod['ip']}:{settings.runner_port}/task", json=payload, timeout=5)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                log.warning("assign %s -> %s failed: %s; requeue", t.id, pod["name"], e)
                self.bad_pods[pod["name"]] = time.time()
                self.enqueue(t.id, t.priority)
                return
            t.state = "running"
            t.runner_pod = pod["name"]
            t.admitted_at = now()
            t.attempts += 1
            t.cold = cold
            waited = (t.admitted_at - t.created_at).total_seconds()
            t.timings = {**(t.timings or {}), "wait": round(waited, 3)}
            s.commit()
            snapshot = t.to_dict()
        with self.lock:
            self.busy[task_id] = {"pod": pod["name"], "ip": pod["ip"], "deadline": time.time() + settings.task_deadline,
                                  "assigned_at": time.time()}
            self.assigned_pods.add(pod["name"])
        metrics.wait_for_runner.observe(waited)
        if cold:
            metrics.cold_assignments.inc()
        log.info("assigned %s (pr#%s, %d lines) -> %s (%s, waited %.1fs)", task_id, snapshot["pr_number"],
                 snapshot["priority"], pod["name"], "cold" if cold else "warm", waited)
        events.publish("task.updated", snapshot)

    def _expire(self, task_id: str) -> None:
        with self.lock:
            b = self.busy.pop(task_id, None)
        if not b:
            return
        self.pods.delete(b["pod"])
        self.assigned_pods.discard(b["pod"])
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None:
                return
            if t.attempts < 2:
                t.state = "queued"
                t.error = f"deadline exceeded on {b['pod']}; requeued"
                s.commit()
                self.enqueue(t.id, t.priority)
            else:
                t.state = "failed"
                t.error = f"deadline exceeded twice (last {b['pod']})"
                s.commit()
                metrics.tasks_total.labels(state="failed").inc()
            log.warning("task %s expired on %s", task_id, b["pod"])
            events.publish("task.updated", t.to_dict())

    # ---- results ----------------------------------------------------------------------------
    def on_result(self, task_id: str, result: dict) -> None:
        with self.lock:
            b = self.busy.pop(task_id, None)
        if b:
            # The pod did one task. Delete it; the ReplicaSet brings up a fresh warm one.
            self.pods.delete(b["pod"])
            self.assigned_pods.discard(b["pod"])

        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None:
                log.warning("result for unknown task %s", task_id)
                return
            if t.state == "superseded":
                return
            t.result_at = now()
            timings = dict(t.timings or {})
            timings.update({k: round(v, 3) for k, v in (result.get("timings") or {}).items()})
            if b:
                timings["runner_total"] = round(time.time() - b["assigned_at"], 3)
            t.timings = timings
            for k, v in (result.get("timings") or {}).items():
                metrics.phase_seconds.labels(phase=k).observe(v)
            if result.get("error"):
                t.state = "failed"
                t.error = str(result["error"])[:2000]
                s.commit()
                metrics.tasks_total.labels(state="failed").inc()
                events.publish("task.updated", t.to_dict())
                return
            t.diff = result.get("diff")
            t.touched_files = result.get("touched_files") or []
            t.files = result.get("files") or {}
            t.pytest_rc = (result.get("pytest") or {}).get("returncode")
            t.pytest_output = (result.get("pytest") or {}).get("output")
            t.ruff_rc = (result.get("ruff") or {}).get("returncode")
            t.ruff_output = (result.get("ruff") or {}).get("output")
            t.state = "reviewing"
            s.commit()
            events.publish("task.updated", t.to_dict())
        self.review_pool.submit(self._review_and_post, task_id)

    def _review_and_post(self, task_id: str) -> None:
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None or t.state != "reviewing":
                return
            try:
                r = reviewer.review(t)
            except Exception as e:  # noqa: BLE001
                log.exception("review failed for %s", task_id)
                t.state = "failed"
                t.error = f"review failed: {e!r}"[:2000]
                s.commit()
                metrics.tasks_total.labels(state="failed").inc()
                events.publish("task.updated", t.to_dict())
                return
            timings = dict(t.timings or {})
            timings["llm"] = round(r["seconds"], 3)
            metrics.phase_seconds.labels(phase="llm").observe(r["seconds"])
            metrics.llm_tokens.labels(direction="in").inc(r["tokens_in"])
            metrics.llm_tokens.labels(direction="out").inc(r["tokens_out"])
            t.verdict = r.get("verdict")
            t.summary = r.get("summary")
            t.reviewer_model = r.get("model")
            t.tokens_in, t.tokens_out, t.tool_calls = r["tokens_in"], r["tokens_out"], r["tool_calls"]
            t.findings = [Finding(path=f["path"], line=f.get("line"), severity=f["severity"], claim=f["claim"],
                                  evidence=f.get("evidence")) for f in r.get("findings", [])]

            if settings.post_reviews and t.verdict:
                t0 = time.monotonic()
                comments = [{"path": f.path, "line": f.line, "side": "RIGHT",
                             "body": f"**{f.severity}** — {f.claim}\n\n> {f.evidence}" if f.evidence else f"**{f.severity}** — {f.claim}"}
                            for f in t.findings if f.line]
                body = (f"{t.summary}\n\n"
                        f"_pr-runtime_ · pytest exit {t.pytest_rc} · ruff exit {t.ruff_rc} · "
                        f"{len(t.findings)} finding(s) · model `{t.reviewer_model}` · config `{t.config}`")
                try:
                    t.review_url = github.post_review(t.repo, t.pr_number, t.head_sha, t.verdict, body, comments)
                    for f in t.findings:
                        f.posted = bool(t.review_url)
                    timings["post"] = round(time.monotonic() - t0, 3)
                    metrics.phase_seconds.labels(phase="post").observe(timings["post"])
                except Exception as e:  # noqa: BLE001
                    log.exception("post review failed for %s", task_id)
                    t.error = f"post failed: {e!r}"[:2000]
            t.timings = timings
            t.state = "posted"
            t.posted_at = now()
            total = (t.posted_at - t.created_at).total_seconds()
            timings["total"] = round(total, 3)
            t.timings = timings
            s.commit()
            metrics.time_to_comment.observe(total)
            metrics.tasks_total.labels(state="posted").inc()
            log.info("posted %s pr#%s verdict=%s findings=%d total=%.1fs", task_id, t.pr_number, t.verdict,
                     len(t.findings), total)
            events.publish("task.updated", t.to_dict())

    def snapshot(self) -> dict:
        with self.lock:
            return {"pending": len(self.pending), "busy": len(self.busy), "cap": settings.admission_cap,
                    "busy_tasks": {k: v["pod"] for k, v in self.busy.items()}}
