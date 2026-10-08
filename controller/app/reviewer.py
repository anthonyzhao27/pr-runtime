"""LLM review: one structured call with the diff, touched files, and test/lint evidence, plus a capped read_file tool."""
from __future__ import annotations

import json
import logging
import time

from openai import OpenAI

from . import github
from .config import settings
from .db import Task

log = logging.getLogger("reviewer")

SYSTEM = """You are a strict, fast code reviewer for the Flask web framework repository.
You receive a pull request diff, the full contents of touched files, the pytest result, and the ruff result.
Your job: find real defects introduced or exposed by this change. Logic errors, broken invariants, wrong
return values, exception handling mistakes, security issues, behavior changes not covered by tests.
Prefer precision over volume: report only findings you can justify from the code. Each finding must point at a
file and a line in the NEW version of the file and quote the evidence. Do not comment on style unless ruff did.
If the tests fail, say which failure the diff explains. If a change looks correct, say so and APPROVE.
Use read_file only when you need code that is not already shown (callers, helpers, tests)."""

SCHEMA = {
    "name": "review",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": ["APPROVE", "REQUEST_CHANGES"]},
            "summary": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "path": {"type": "string"},
                        "line": {"type": "integer"},
                        "severity": {"type": "string", "enum": ["blocker", "major", "minor", "nit"]},
                        "claim": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["path", "line", "severity", "claim", "evidence"],
                },
            },
        },
        "required": ["verdict", "summary", "findings"],
    },
}

READ_FILE_TOOL = {
    "type": "function",
    "name": "read_file",
    "description": "Read a file from the PR head revision. Use for code not shown in the prompt.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": {"type": "string", "description": "Repository-relative path"}},
        "required": ["path"],
    },
    "strict": True,
}


def _clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n... [{len(s) - n} chars truncated]"


def build_prompt(task: Task) -> str:
    parts = [f"# Pull request #{task.pr_number} in {task.repo}", f"base {task.base_sha[:10]} -> head {task.head_sha[:10]}", ""]
    parts += ["## Diff", "```diff", _clip(task.diff, 60_000), "```", ""]
    if task.config != "diff_only":
        parts += [f"## pytest (exit {task.pytest_rc})", "```", _clip(task.pytest_output, 8_000), "```", ""]
        parts += [f"## ruff (exit {task.ruff_rc})", "```", _clip(task.ruff_output, 3_000), "```", ""]
        parts += ["## Touched files (head revision)"]
        for path, content in (task.files or {}).items():
            parts += [f"### {path}", "```python", _clip(content, 40_000), "```", ""]
    return "\n".join(parts)


def review(task: Task) -> dict:
    """Returns {verdict, summary, findings, model, tokens_in, tokens_out, tool_calls, seconds}."""
    if not settings.openai_api_key:
        return {"verdict": None, "summary": "reviewer disabled: no OPENAI_API_KEY", "findings": [],
                "model": None, "tokens_in": 0, "tokens_out": 0, "tool_calls": 0, "seconds": 0.0}

    client = OpenAI(api_key=settings.openai_api_key)
    model = settings.reviewer_model
    t0 = time.monotonic()
    tokens_in = tokens_out = tool_calls = 0

    input_items: list = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": build_prompt(task)},
    ]
    tools = [READ_FILE_TOOL] if task.config != "diff_only" else []

    while True:
        resp = client.responses.create(
            model=model,
            input=input_items,
            tools=tools,
            reasoning={"effort": settings.reasoning_effort},
            text={"format": {"type": "json_schema", **SCHEMA}},
        )
        if resp.usage:
            tokens_in += resp.usage.input_tokens or 0
            tokens_out += resp.usage.output_tokens or 0

        calls = [o for o in resp.output if getattr(o, "type", "") == "function_call"]
        if not calls or tool_calls >= settings.read_file_max_calls:
            break
        input_items += resp.output
        for c in calls:
            tool_calls += 1
            path = json.loads(c.arguments).get("path", "")
            content = (task.files or {}).get(path) or github.get_file(task.repo, path, task.head_sha)
            input_items.append({
                "type": "function_call_output",
                "call_id": c.call_id,
                "output": _clip(content, 40_000) if content else f"error: {path} not found at {task.head_sha[:10]}",
            })
            if tool_calls >= settings.read_file_max_calls:
                tools = []  # last round: force an answer

    try:
        parsed = json.loads(resp.output_text)
    except json.JSONDecodeError:
        log.error("reviewer returned non-JSON: %s", resp.output_text[:500])
        parsed = {"verdict": "REQUEST_CHANGES", "summary": "reviewer output unparseable", "findings": []}

    return {**parsed, "model": f"{model} (effort={settings.reasoning_effort})", "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tool_calls": tool_calls, "seconds": time.monotonic() - t0}
