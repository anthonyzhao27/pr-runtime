from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import settings


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    repo: Mapped[str] = mapped_column(String(200))
    pr_number: Mapped[int] = mapped_column(Integer, index=True)
    head_sha: Mapped[str] = mapped_column(String(40))
    base_sha: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(32), default="opened")
    delivery: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[str] = mapped_column(String(32), default="full")  # reviewer context config
    # queued -> admitted -> running -> reviewing -> posted | failed | superseded
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # diff size in changed lines
    runner_pod: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    admitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    timings: Mapped[dict] = mapped_column(JSON, default=dict)  # runner phases + llm + post
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    touched_files: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    pytest_rc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pytest_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    ruff_rc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ruff_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewer_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cold: Mapped[bool] = mapped_column(Boolean, default=False)  # true when no warm runner was available
    cost_compute_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_tokens_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # GitHub App installation
    check_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)      # Checks API run for this task
    trigger: Mapped[str] = mapped_column(String(16), default="webhook")           # webhook | mention | manual

    findings: Mapped[list["Finding"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    def to_dict(self, full: bool = False) -> dict:
        d = {
            "id": self.id, "repo": self.repo, "pr_number": self.pr_number, "head_sha": self.head_sha,
            "base_sha": self.base_sha, "action": self.action, "config": self.config, "state": self.state,
            "priority": self.priority, "runner_pod": self.runner_pod, "attempts": self.attempts,
            "created_at": _iso(self.created_at), "admitted_at": _iso(self.admitted_at),
            "result_at": _iso(self.result_at), "posted_at": _iso(self.posted_at),
            "timings": self.timings or {}, "pytest_rc": self.pytest_rc, "ruff_rc": self.ruff_rc,
            "verdict": self.verdict, "summary": self.summary, "review_url": self.review_url,
            "reviewer_model": self.reviewer_model, "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "tool_calls": self.tool_calls, "error": self.error, "cold": self.cold,
            "touched_files": self.touched_files or [], "findings_count": len(self.findings),
            "cost_compute_usd": self.cost_compute_usd, "cost_tokens_usd": self.cost_tokens_usd,
            "installation_id": self.installation_id, "check_run_id": self.check_run_id, "trigger": self.trigger,
        }
        if full:
            d.update({
                "diff": self.diff, "pytest_output": self.pytest_output, "ruff_output": self.ruff_output,
                "findings": [f.to_dict() for f in self.findings],
            })
        return d


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    path: Mapped[str] = mapped_column(String(300))
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(16))  # blocker | major | minor | nit
    claim: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    task: Mapped[Task] = relationship(back_populates="findings")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="finding", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "task_id": self.task_id, "path": self.path, "line": self.line,
            "severity": self.severity, "claim": self.claim, "evidence": self.evidence, "posted": self.posted,
            "feedback": [fb.to_dict() for fb in self.feedback],
        }


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex[:12])
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    vote: Mapped[int] = mapped_column(Integer)  # +1 / -1
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    finding: Mapped[Finding] = relationship(back_populates="feedback")

    def to_dict(self) -> dict:
        return {"id": self.id, "vote": self.vote, "note": self.note, "created_at": _iso(self.created_at)}


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat() if t else None


engine = create_engine(settings.database_url or "sqlite:///./dev.db", pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_compute_usd FLOAT",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS cost_tokens_usd FLOAT",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS installation_id INTEGER",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS check_run_id INTEGER",
    "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS trigger VARCHAR(16) DEFAULT 'webhook'",
]


def init_db() -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text
        with engine.begin() as conn:
            for stmt in MIGRATIONS:
                conn.execute(text(stmt))
