from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc

from . import events
from .db import Feedback, Finding, SessionLocal, Task

router = APIRouter(prefix="/api")


@router.get("/tasks")
def list_tasks(limit: int = 100, state: str | None = None, pr: int | None = None):
    with SessionLocal() as s:
        q = s.query(Task)
        if state:
            q = q.filter(Task.state == state)
        if pr:
            q = q.filter(Task.pr_number == pr)
        rows = q.order_by(desc(Task.created_at)).limit(min(limit, 500)).all()
        return [t.to_dict() for t in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    with SessionLocal() as s:
        t = s.get(Task, task_id)
        if not t:
            raise HTTPException(404, "task not found")
        return t.to_dict(full=True)


class RerunBody(BaseModel):
    config: str = "full"


@router.post("/tasks/{task_id}/rerun")
def rerun(task_id: str, body: RerunBody, request: Request):
    with SessionLocal() as s:
        t = s.get(Task, task_id)
        if not t:
            raise HTTPException(404, "task not found")
        repo, pr, head, base, inst = t.repo, t.pr_number, t.head_sha, t.base_sha, t.installation_id
    new_id = request.app.state.consumer.create_task(repo=repo, pr_number=pr, head_sha=head, base_sha=base,
                                                     action="manual", config=body.config, installation_id=inst)
    return {"id": new_id}


class FeedbackBody(BaseModel):
    vote: int
    note: str | None = None


@router.post("/findings/{finding_id}/feedback")
def feedback(finding_id: str, body: FeedbackBody):
    if body.vote not in (1, -1):
        raise HTTPException(400, "vote must be 1 or -1")
    with SessionLocal() as s:
        f = s.get(Finding, finding_id)
        if not f:
            raise HTTPException(404, "finding not found")
        fb = Feedback(finding_id=finding_id, vote=body.vote, note=body.note)
        s.add(fb)
        s.commit()
        out = f.to_dict()
    events.publish("finding.feedback", out)
    return out


@router.get("/stats")
def stats(request: Request):
    sched = request.app.state.scheduler.snapshot()
    with SessionLocal() as s:
        by_state = {st: s.query(Task).filter(Task.state == st).count()
                    for st in ("queued", "running", "reviewing", "posted", "failed", "superseded")}
    return {"scheduler": sched, "tasks": by_state}


@router.get("/events")
async def sse(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
