"""SQS consumer: webhook payload -> Task row -> scheduler."""
from __future__ import annotations

import json
import logging
import threading

import boto3

from . import events, github, metrics
from .config import settings
from .db import SessionLocal, Task
from .scheduler import Scheduler

log = logging.getLogger("queue")


class QueueConsumer:
    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self.sqs = boto3.client("sqs", region_name=settings.aws_region)
        self._stop = threading.Event()

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
        action = body.get("action", "")
        pr = body.get("pull_request") or {}
        if not pr or action in settings.ignore_actions:
            return
        metrics.events_total.labels(action=action).inc()
        repo = body["repository"]["full_name"]
        self.create_task(repo=repo, pr_number=pr["number"], head_sha=pr["head"]["sha"], base_sha=pr["base"]["sha"],
                         action=action, delivery=attrs.get("delivery", {}).get("StringValue"),
                         size_hint=int(pr.get("additions", 0)) + int(pr.get("deletions", 0)))

    def create_task(self, *, repo: str, pr_number: int, head_sha: str, base_sha: str, action: str = "manual",
                    delivery: str | None = None, config: str | None = None, size_hint: int | None = None) -> str:
        priority = size_hint if size_hint is not None else github.pr_size(repo, pr_number)
        with SessionLocal() as s:
            dup = s.query(Task).filter(Task.repo == repo, Task.pr_number == pr_number, Task.head_sha == head_sha,
                                       Task.config == (config or settings.default_config),
                                       Task.state.in_(("queued", "running", "reviewing", "posted"))).first()
            if dup and action != "manual":
                log.info("duplicate event for pr#%s %s; ignoring", pr_number, head_sha[:8])
                return dup.id
            t = Task(repo=repo, pr_number=pr_number, head_sha=head_sha, base_sha=base_sha, action=action,
                     delivery=delivery, config=config or settings.default_config, priority=priority)
            s.add(t)
            s.commit()
            task_id = t.id
            snap = t.to_dict()
        if action != "manual":
            self.scheduler.supersede(repo, pr_number, task_id)
        self.scheduler.enqueue(task_id, priority)
        events.publish("task.created", snap)
        log.info("task %s queued pr#%s %s (%d lines, %s)", task_id, pr_number, head_sha[:8], priority, action)
        return task_id
