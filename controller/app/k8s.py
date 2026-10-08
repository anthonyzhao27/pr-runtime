from __future__ import annotations

import datetime as dt
import logging

from kubernetes import client, config

from .config import settings

log = logging.getLogger("k8s")


def load() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


class RunnerPods:
    def __init__(self) -> None:
        self.api = load()

    def list_ready(self) -> list[dict]:
        pods = self.api.list_namespaced_pod(settings.namespace, label_selector=settings.runner_selector).items
        out = []
        for p in pods:
            if p.metadata.deletion_timestamp is not None or p.status.phase != "Running":
                continue
            ready = any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or []))
            if not ready or not p.status.pod_ip:
                continue
            started = p.status.start_time or dt.datetime.now(dt.timezone.utc)
            out.append({"name": p.metadata.name, "ip": p.status.pod_ip, "started": started})
        return out

    def list_all(self) -> list[dict]:
        """Every runner pod that still exists (any phase), for lost-pod detection."""
        pods = self.api.list_namespaced_pod(settings.namespace, label_selector=settings.runner_selector).items
        return [{"name": p.metadata.name, "phase": p.status.phase} for p in pods]

    def delete(self, name: str) -> None:
        try:
            self.api.delete_namespaced_pod(name, settings.namespace, grace_period_seconds=0)
        except client.ApiException as e:  # noqa: PERF203
            if e.status != 404:
                log.warning("delete pod %s failed: %s", name, e)
