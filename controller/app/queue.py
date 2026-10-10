"""SQS consumer: webhook payload -> Task row -> scheduler."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import boto3

from . import events, github, metrics
from .config import settings
from .db import SessionLocal, Task
from .scheduler import Scheduler, finish_check

log = logging.getLogger("queue")


def repo_mode(repo: str, ref: str, installation_id: int | None) -> str:
    """'auto' (review every PR) or 'mention' (only when @-tagged). Read from .pr-runtime.yml at the PR head."""
    text = github.get_file(repo, ".pr-runtime.yml", ref, installation_id) or github.get_file(repo, ".pr-runtime.yaml", ref, installation_id)
    if not text:
        return "auto"
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("mode:"):
            return line.split(":", 1)[1].strip().strip("'\"") or "auto"
    return "auto"


class QueueConsumer:
    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self.sqs = boto3.client("sqs", region_name=settings.aws_region)
        self._stop = threading.Event()
        # One worker keeps check-run opens in creation order; they serialize in the GitHub bucket anyway.
        self.checks_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="checks")

    def run(self) -> None:
        if not settings.queue_url:
            log.warning("QUEUE_URL unset; consumer idle")
            return
        log.info("consuming %s", settings.queue_url)
        while not self._stop.is_set():
            try:
                resp = self.sqs.receive_message(QueueUrl=settings.queue_url, MaxNumberOfMessages=10,
                                                WaitTimeSeconds=20, MessageAttributeNames=["All"])
            except Exception:  # noqa: BLE001
                log.exception("receive failed")
                self._stop.wait(5)
                continue
            for m in resp.get("Messages", []):
                try:
                    self.handle(m)
                finally:
                    self.sqs.delete_message(QueueUrl=settings.queue_url, ReceiptHandle=m["ReceiptHandle"])

    def stop(self) -> None:
        self._stop.set()

    def handle(self, m: dict) -> None:
        body = json.loads(m["Body"])
        attrs = m.get("MessageAttributes") or {}
        gh_event = (attrs.get("event") or {}).get("StringValue", "pull_request")
        action = body.get("action", "")
        repo = (body.get("repository") or {}).get("full_name", "")
        installation_id = (body.get("installation") or {}).get("id")
        delivery = (attrs.get("delivery") or {}).get("StringValue")

        if gh_event == "issue_comment":
            # "@pr-runtime" mention on a PR: review that PR's current head.
            issue = body.get("issue") or {}
            comment = body.get("comment") or {}
            if "pull_request" not in issue or settings.mention_handle.lower() not in (comment.get("body") or "").lower():
                return
            metrics.events_total.labels(action="mention").inc()
            if not settings.repo_allowed(repo):
                github.comment(repo, issue["number"], installation_id,
                               f"{settings.mention_handle} is not enabled for `{repo}` (allowlist: {', '.join(settings.repos)}).")
                return
            github.react(repo, comment["id"], installation_id, "eyes")
            shas = github.pr_head(repo, issue["number"], installation_id)
            if not shas:
                return
            self.create_task(repo=repo, pr_number=issue["number"], head_sha=shas[0], base_sha=shas[1], action="mention",
                             delivery=delivery, installation_id=installation_id, trigger="mention")
            return

        pr = body.get("pull_request") or {}
        if not pr or action in settings.ignore_actions:
            return
        if not settings.repo_allowed(repo):
            log.info("ignoring %s event from unlisted repo %s", action, repo)
            return
        # Per-repo policy: `.pr-runtime.yml` with `mode: mention` means only review when tagged.
        if repo_mode(repo, pr["head"]["sha"], installation_id) == "mention":
            log.info("repo %s is in mention mode; ignoring auto event for pr#%s", repo, pr["number"])
            return
        metrics.events_total.labels(action=action).inc()
        self.create_task(repo=repo, pr_number=pr["number"], head_sha=pr["head"]["sha"], base_sha=pr["base"]["sha"],
                         action=action, delivery=delivery, installation_id=installation_id,
                         size_hint=int(pr.get("additions", 0)) + int(pr.get("deletions", 0)))

    def create_task(self, *, repo: str, pr_number: int, head_sha: str, base_sha: str, action: str = "manual",
                    delivery: str | None = None, config: str | None = None, size_hint: int | None = None,
                    installation_id: int | None = None, trigger: str = "webhook") -> str:
        priority = size_hint if size_hint is not None else github.pr_size(repo, pr_number, installation_id)
        with SessionLocal() as s:
            dup = s.query(Task).filter(Task.repo == repo, Task.pr_number == pr_number, Task.head_sha == head_sha,
                                       Task.config == (config or settings.default_config),
                                       Task.state.in_(("queued", "running", "reviewing", "posted"))).first()
            if dup and action not in ("manual", "mention"):
                log.info("duplicate event for pr#%s %s; ignoring", pr_number, head_sha[:8])
                return dup.id
            if installation_id is None and dup is not None:
                installation_id = dup.installation_id  # manual reruns inherit the App installation
            if installation_id is None:
                # Reruns of PAT-era tasks (and PAT webhooks) post as the App once the repo has been seen through it;
                # if the App is gone from the repo the token mint fails and github_app falls back to the PAT.
                prior = (s.query(Task).filter(Task.repo == repo, Task.installation_id.isnot(None))
                         .order_by(Task.created_at.desc()).first())
                if prior is not None:
                    installation_id = prior.installation_id
            t = Task(repo=repo, pr_number=pr_number, head_sha=head_sha, base_sha=base_sha, action=action,
                     delivery=delivery, config=config or settings.default_config, priority=priority,
                     installation_id=installation_id, trigger=trigger if action != "manual" else "manual")
            s.add(t)
            s.commit()
            task_id = t.id
            snap = t.to_dict()
        if action not in ("manual", "mention"):
            self.scheduler.supersede(repo, pr_number, task_id)
        if settings.checks_enabled and installation_id:
            self.checks_pool.submit(self._open_check, task_id, repo, head_sha, installation_id)
        self.scheduler.enqueue(task_id, priority)
        events.publish("task.created", snap)
        log.info("task %s queued pr#%s %s (%d lines, %s)", task_id, pr_number, head_sha[:8], priority, action)
        return task_id

    def _open_check(self, task_id: str, repo: str, head_sha: str, installation_id: int) -> None:
        """Open the task's Check run off the ingest path. A check-run POST is a serialized GitHub write (one gap per
        identity), so doing it inline would meter task creation at ~40/min under a burst. Best effort: a Checks API
        problem never blocks the review. If the review finished while this waited in the bucket, complete the run
        now from the stored result instead of leaving it `in_progress` (the poster reads check_run_id fresh after
        committing `posted`, so exactly one side finishes it)."""
        try:
            # Never touch the DB while holding a GitHub slot: the review thread updates this row inside its own slot,
            # so a row-lock wait here while holding the slot is a deadlock (Oct 10 burst). check_start takes its own
            # slot; the late-finish writes below take another, which is rare enough not to matter.
            with SessionLocal() as s:
                t = s.get(Task, task_id)
                if t is None:
                    return
                if t.state == "posted":
                    # Under a burst the 32 review threads out-compete this one worker for the bucket (the lock is not
                    # fair), so by the time an open gets its slot the review is often already up. Then create the
                    # run completed in one write instead of open + finish in two.
                    t.check_run_id = finish_check(t, create=True)
                    s.commit()
                    return
            cid = github.check_start(repo, head_sha, installation_id)
            if not cid:
                return
            with SessionLocal() as s:
                t = s.get(Task, task_id)
                if t is None:
                    return
                t.check_run_id = cid
                s.commit()
                if t.state == "posted":
                    finish_check(t)  # lost the race with the poster between the state read above and this commit
                elif t.state == "failed":
                    github.check_finish(repo, cid, installation_id, "neutral", "Runner failed", t.error or "")
                elif t.state == "superseded":
                    github.check_finish(repo, cid, installation_id, "neutral", "Superseded", "A newer push replaced this task.")
        except Exception:  # noqa: BLE001
            log.exception("check run bookkeeping failed for %s", task_id)
