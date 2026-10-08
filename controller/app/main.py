from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import events
from .api import router
from .config import settings
from .db import init_db
from .queue import QueueConsumer
from .scheduler import Scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")
STATIC = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "static"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    events.bind_loop(asyncio.get_running_loop())
    sched = Scheduler()
    consumer = QueueConsumer(sched)
    app.state.scheduler = sched
    app.state.consumer = consumer
    threading.Thread(target=sched.run, name="scheduler", daemon=True).start()
    threading.Thread(target=consumer.run, name="sqs", daemon=True).start()
    log.info("controller up: repo=%s cap=%d reviewer=%s post=%s", settings.repo, settings.admission_cap,
             settings.reviewer_model or "-", settings.post_reviews)
    yield
    sched.stop()
    consumer.stop()


app = FastAPI(title="pr-runtime controller", lifespan=lifespan)
app.include_router(router)


@app.get("/metrics")
def metrics_endpoint():
    # Explicit route: a Mount would lose to the SPA catch-all for the bare /metrics path.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/result/{task_id}")
async def result(task_id: str, request: Request):
    payload = await request.json()
    await asyncio.to_thread(request.app.state.scheduler.on_result, task_id, payload)
    return JSONResponse({"ok": True})


@app.get("/healthz")
def healthz():
    return {"ok": True}


if os.path.isdir(STATIC):
    if os.path.isdir(os.path.join(STATIC, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(STATIC, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = os.path.join(STATIC, path)
        if path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC, "index.html"))
