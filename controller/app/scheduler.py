"""Control plane: admission cap, ranking, warm-pool assignment, deadlines, result handling, review, egress."""
from __future__ import annotations

import datetime as dt
import heapq
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import httpx

from . import events, github, metrics, reviewer
from .config import settings
from .db import Finding, SessionLocal, Task, now
from .k8s import RunnerPods

log = logging.getLogger("scheduler")
WARM_AGE_SECONDS = 10.0


def _short_reason(err: str) -> str:
    """'runner exception: RuntimeError("git clone failed: fatal: ...")' -> 'git clone failed: fatal: ...'."""
    err = err.strip()
    if err.startswith("runner exception:"):
        inner = err.split(":", 1)[1].strip()
        if "(" in inner and inner.endswith(")"):
            inner = inner[inner.index("(") + 1:-1].strip("'\"")
        err = inner
    err = err.replace("\\n", " ").replace("\\t", " ")  # repr() of the runner's message escapes its newlines
    return " ".join(err.split())[:300]


def evidence_line(t: Task) -> str:
    """What backs the review: test/lint exit codes, or, on the API-diff fallback, why there are none."""
    reason = reviewer.fallback_reason(t)
    if reason:
        return f"Tests not run: {reason}. Review is from the diff only."
    return f"pytest exit {t.pytest_rc} · ruff exit {t.ruff_rc}"


def finish_check(t: Task) -> None:
    """Complete a task's Check run from its stored result. Checks API: the bot can block (`failure`) but never
    approves; merge policy stays with humans/rulesets. Called from the posting slot, or late from queue._open_check
    when the check run was opened after the review had already posted."""
    blockers = [f for f in t.findings if f.severity in ("blocker", "major")]
    conclusion = "failure" if blockers else ("neutral" if t.findings else "success")
    title = (f"{len(blockers)} blocking finding(s)" if blockers else
             (f"{len(t.findings)} minor finding(s)" if t.findings else "No findings"))
    if reviewer.fallback_reason(t):
        title = f"{title} (tests not run)"
    text = "\n".join(f"- `{f.path}:{f.line}` **{f.severity}** {f.claim}" for f in t.findings)
    github.check_finish(t.repo, t.check_run_id, t.installation_id, conclusion, title,
                        f"{t.summary or ''}\n\n{evidence_line(t)} · model `{t.reviewer_model}`", text)


class Scheduler:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: list[tuple[int, float, str]] = []  # (priority, created_ts, task_id)
        self.busy: dict[str, dict] = {}  # task_id -> {pod, ip, deadline, assigned_at}
        self.assigned_pods: set[str] = set()
        self.bad_pods: dict[str, float] = {}
        self.pods = RunnerPods()
        self.ready_count = 0
        self.cap = settings.admission_cap
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
    def reconcile(self) -> None:
        """After a restart: in-memory state is gone, Postgres is not. Re-queue running tasks, resume reviews."""
        with SessionLocal() as s:
            running = s.query(Task).filter(Task.state == "running").all()
            reviewing = s.query(Task).filter(Task.state == "reviewing").all()
            queued = s.query(Task).filter(Task.state == "queued").all()
            for t in running:
                t.state = "queued"
                t.error = f"controller restarted while running on {t.runner_pod}; requeued"
                t.runner_pod = None
            s.commit()
            # Anything the database says is queued but memory does not know about (restart, or a crash
            # between the insert and the enqueue) goes back on the heap.
            for t in running + queued:
                self.enqueue(t.id, t.priority)
            for t in reviewing:
                self.review_pool.submit(self._review_and_post, t.id)
        if running or reviewing or queued:
            log.info("reconciled after restart: %d running requeued, %d queued re-enqueued, %d reviews resumed",
                     len(running), len(queued), len(reviewing))
        # Any pod that is Ready but not assigned is idle by definition; stale 'done' pods are unready and get cleaned up.
        for p in self.pods.list_ready():
            pass

    def run(self) -> None:
        log.info("scheduler started cap=%s deadline=%ss review_workers=%s",
                 settings.admission_cap or "pool (ready runners)", settings.task_deadline, settings.review_workers)
        try:
            self.reconcile()
        except Exception:  # noqa: BLE001
            log.exception("reconcile failed")
        n = 0
        while not self._stop.is_set():
            try:
                self.tick()
                n += 1
                if n % 30 == 0:
                    self.sweep_orphans()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            time.sleep(1.0)

    def sweep_orphans(self) -> None:
        """A task the DB calls running but memory does not track can only happen when a write failed after the pod was
        released (seen under DB-pool starvation). Without this it would wait for a restart's reconcile()."""
        with self.lock:
            tracked = set(self.busy)
        with SessionLocal() as s:
            rows = s.query(Task).filter(Task.state == "running").all()
            orphans = [t for t in rows if t.id not in tracked and t.admitted_at and (now() - t.admitted_at).total_seconds() > 30]
            for t in orphans:
                t.state = "queued"
                t.error = f"result bookkeeping lost on {t.runner_pod}; requeued"
                t.runner_pod = None
            s.commit()
            orphans = [(t.id, t.priority) for t in orphans]
        for tid, prio in orphans:
            log.warning("orphaned running task %s requeued", tid)
            self.enqueue(tid, prio)

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        ready = self.pods.list_ready()
        nowt = time.time()
        self.bad_pods = {k: v for k, v in self.bad_pods.items() if nowt - v < 60}
        idle = [p for p in ready if p["name"] not in self.assigned_pods and p["name"] not in self.bad_pods]
        # Warmest first: the oldest ready pod has had the longest to settle.
        idle.sort(key=lambda p: p["started"])
        # Cap follows the pool: with ADMISSION_CAP=0 the cap is whatever is Ready right now, so an autoscaled
        # Deployment (KEDA on prr_tasks_pending, Karpenter for nodes) raises admission as it grows.
        cap = settings.effective_cap(len(ready))

        with self.lock:
            self.ready_count, self.cap = len(ready), cap
            metrics.runners_ready.set(len(ready))
            metrics.admission_cap.set(cap)
            metrics.runners_idle.set(len(idle))
            metrics.runners_busy.set(len(self.busy))
            metrics.reviews_in_flight.set(self.review_pool._work_queue.qsize() + sum(1 for t in self.review_pool._threads if t.is_alive()) if hasattr(self.review_pool, "_threads") else 0)
            metrics.tasks_pending.set(len(self.pending))

            if self.pending and len(self.busy) >= cap:
                metrics.admission_rejects.inc()

            to_assign: list[tuple[str, dict]] = []
            while self.pending and len(self.busy) + len(to_assign) < cap and idle:
                _, _, task_id = heapq.heappop(self.pending)
                pod = idle.pop(0)
                to_assign.append((task_id, pod))

            expired = [tid for tid, b in self.busy.items() if nowt > b["deadline"]]
            # Node loss / eviction: a busy pod that no longer exists will never report. Requeue now, not at the deadline.
            live = {p["name"] for p in self.pods.list_all()}
            lost = [tid for tid, b in self.busy.items() if b["pod"] not in live and nowt - b["assigned_at"] > 5]

        for task_id, pod in to_assign:
            self._assign(task_id, pod)
        for tid in expired:
            self._expire(tid)
        for tid in lost:
            self._expire(tid, reason="runner pod disappeared (node loss or eviction)")

    def _assign(self, task_id: str, pod: dict) -> None:
        age = (dt.datetime.now(dt.timezone.utc) - pod["started"]).total_seconds()
        cold = age < WARM_AGE_SECONDS
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None or t.state not in ("queued",):
                return
            payload = {"task_id": t.id, "head_sha": t.head_sha, "base_sha": t.base_sha, "pr_number": t.pr_number,
                       "repo": t.repo, "clone_url": settings.clone_url_override or f"https://github.com/{t.repo}.git"}
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

    def _expire(self, task_id: str, reason: str = "deadline exceeded") -> None:
        with self.lock:
            b = self.busy.pop(task_id, None)
        if not b:
            return
        self.pods.delete(b["pod"])
        self.assigned_pods.discard(b["pod"])
        metrics.tasks_lost.labels(reason="deadline" if reason.startswith("deadline") else "pod_lost").inc()
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None:
                return
            if t.attempts < 3:
                t.state = "queued"
                t.error = f"{reason} on {b['pod']}; requeued (attempt {t.attempts})"
                s.commit()
                self.enqueue(t.id, t.priority)
            else:
                t.state = "failed"
                t.error = f"{reason}; gave up after {t.attempts} attempts (last {b['pod']})"
                s.commit()
                metrics.tasks_total.labels(state="failed").inc()
            log.warning("task %s: %s on %s", task_id, reason, b["pod"])
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
                t.cost_compute_usd = round(settings.compute_usd(timings["runner_total"]), 6)
                metrics.cost_usd.labels(kind="compute").inc(t.cost_compute_usd)
            t.timings = timings
            s.commit()
            for k, v in (result.get("timings") or {}).items():
                metrics.phase_seconds.labels(phase=k).observe(v)
            error = result.get("error")
            args = (t.repo, t.pr_number, t.head_sha, t.installation_id)
        if error:
            # The runner could not produce evidence (private repo it cannot clone, bad ref, crash). Keep the task
            # alive and review from the GitHub API instead; only a failed fetch makes it `failed`. The fetch runs
            # outside the session so a slow GitHub does not pin a pooled connection.
            self._fallback_review(task_id, _short_reason(str(error)), *args)
            return
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None or t.state == "superseded":
                return
            t.diff = result.get("diff")
            t.touched_files = result.get("touched_files") or []
            t.files = result.get("files") or {}
            t.pytest_rc = (result.get("pytest") or {}).get("returncode")
            t.pytest_output = (result.get("pytest") or {}).get("output")
            t.ruff_rc = (result.get("ruff") or {}).get("returncode")
            t.ruff_output = (result.get("ruff") or {}).get("output")
            t.meta = {"toolchain": result.get("toolchain") or {}, "install": result.get("install") or {},
                      "workdir": result.get("workdir"), "test_command": (result.get("pytest") or {}).get("command")}
            t.state = "reviewing"
            s.commit()
            events.publish("task.updated", t.to_dict())
        self.review_pool.submit(self._review_and_post, task_id)

    def _fallback_review(self, task_id: str, reason: str, repo: str, pr_number: int, head_sha: str,
                         installation_id: int | None) -> None:
        """API-diff fallback: fetch the PR diff, the touched paths and their head contents through the installation
        token (the controller holds credentials; the runner never does), store them as if a runner had produced them
        with no test run, and review under `diff_only` semantics. The posted review and the Check say why."""
        t0 = time.monotonic()
        try:
            ctx = github.pr_context(repo, pr_number, head_sha, installation_id)
        except Exception as e:  # noqa: BLE001
            log.warning("task %s: runner failed (%s) and the API fallback failed too: %r", task_id, reason[:120], e)
            with SessionLocal() as s:
                t = s.get(Task, task_id)
                if t is None or t.state == "superseded":
                    return
                t.state = "failed"
                t.error = f"{reason} · API fallback failed: {e!r}"[:2000]
                s.commit()
                metrics.tasks_total.labels(state="failed").inc()
                github.check_finish(repo, t.check_run_id, installation_id, "neutral", "Runner failed", t.error)
                events.publish("task.updated", t.to_dict())
            return
        with SessionLocal() as s:
            t = s.get(Task, task_id)
            if t is None or t.state == "superseded":
                return
            t.diff, t.touched_files, t.files = ctx["diff"], ctx["touched_files"], ctx["files"]
            t.pytest_rc = t.ruff_rc = None
            t.pytest_output = t.ruff_output = None
            t.meta = {"fallback": {"reason": reason, "source": "github_api"}}  # no toolchain: nothing ran
            t.timings = {**(t.timings or {}), "fallback_fetch": round(time.monotonic() - t0, 3)}
            t.state = "reviewing"
            s.commit()
            metrics.fallback_reviews.inc()
            events.publish("task.updated", t.to_dict())
        log.info("task %s pr#%s: runner failed (%s); reviewing from the GitHub API diff (%d files)",
                 task_id, pr_number, reason[:120], len(ctx["files"]))
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
            t.cost_tokens_usd = round(settings.tokens_usd(r["tokens_in"], r["tokens_out"]), 6)
            metrics.cost_usd.labels(kind="tokens").inc(t.cost_tokens_usd)
            t.findings = [Finding(path=f["path"], line=f.get("line"), severity=f["severity"], claim=f["claim"],
                                  evidence=f.get("evidence")) for f in r.get("findings", [])]

            posting = settings.post_reviews and bool(t.verdict)
            # One serialized GitHub slot per task: the review POST and the check-run PATCH are two endpoints but
            # cost one MIN_POST_GAP between them and the next task, not one each (see github.py).
            with (github.post_slot(t.installation_id) if posting or t.check_run_id else nullcontext()):
                if posting:
                    t0 = time.monotonic()
                    comments = [{"path": f.path, "line": f.line, "side": "RIGHT",
                                 "body": f"**{f.severity}** — {f.claim}\n\n> {f.evidence}" if f.evidence else f"**{f.severity}** — {f.claim}"}
                                for f in t.findings if f.line]
                    body = (f"{t.summary}\n\n"
                            f"_pr-runtime_ · {evidence_line(t)} · "
                            f"{len(t.findings)} finding(s) · model `{t.reviewer_model}` · config `{reviewer.effective_config(t)}`")
                    try:
                        t.review_url = github.post_review(t.repo, t.pr_number, t.head_sha, t.verdict, body, comments,
                                                          installation_id=t.installation_id)
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
                # The check run is opened off the ingest path (queue._open_check) and may not have landed yet; read
                # the id fresh after committing `posted`, so exactly one side finishes it (see queue._open_check).
                s.refresh(t, attribute_names=["check_run_id"])
                if t.check_run_id:
                    finish_check(t)
            log.info("posted %s pr#%s verdict=%s findings=%d total=%.1fs%s", task_id, t.pr_number, t.verdict,
                     len(t.findings), total, " (API-diff fallback)" if reviewer.fallback_reason(t) else "")
            events.publish("task.updated", t.to_dict())

    def snapshot(self) -> dict:
        with self.lock:
            return {"pending": len(self.pending), "busy": len(self.busy), "cap": self.cap,
                    "cap_mode": "fixed" if settings.admission_cap > 0 else "pool", "runners_ready": self.ready_count,
                    "busy_tasks": {k: v["pod"] for k, v in self.busy.items()},
                    "pool_standing_usd_per_hour": round(settings.pool_standing_usd_per_hour(self.ready_count), 4),
                    "prices": {"input_per_m": settings.price_input_per_m, "output_per_m": settings.price_output_per_m,
                               "node_usd_per_hour": settings.node_usd_per_hour}}
