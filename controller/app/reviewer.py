"""LLM review: one structured call with the diff, touched files, and test/lint evidence, plus a capped read_file tool."""
from __future__ import annotations

import json
import logging
import time

from openai import OpenAI

from . import github, repo as repo_tools
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


AGENT_TOOLS = [
    READ_FILE_TOOL,
    {"type": "function", "name": "grep", "strict": True,
     "description": "Search the repository at the PR head for a regex (git grep -n). Use to find callers, definitions, tests.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"pattern": {"type": "string"}, "path_glob": {"type": "string", "description": "optional pathspec, e.g. 'src/flask/*.py' or '' for all"}},
                    "required": ["pattern", "path_glob"]}},
    {"type": "function", "name": "list_dir", "strict": True,
     "description": "List a directory at the PR head.",
     "parameters": {"type": "object", "additionalProperties": False,
                    "properties": {"path": {"type": "string", "description": "repository-relative directory, '' for root"}},
                    "required": ["path"]}},
]

AGENT_SYSTEM_SUFFIX = """
You may explore the repository with read_file, grep and list_dir (up to the tool budget). Use them to check callers and
tests of anything the diff changes: a change can be locally reasonable and still break a caller elsewhere. Stop exploring
as soon as you can justify a verdict."""


def _run_tool(task: Task, name: str, args: dict) -> str:
    if name == "read_file":
        path = args.get("path", "")
        return (task.files or {}).get(path) or repo_tools.read_file(task.repo, task.head_sha, path)
    if name == "grep":
        return repo_tools.grep(task.repo, task.head_sha, args.get("pattern", ""), args.get("path_glob", ""))
    if name == "list_dir":
        return repo_tools.list_dir(task.repo, task.head_sha, args.get("path", ""))
    return f"error: unknown tool {name}"


def _clip(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n... [{len(s) - n} chars truncated]"


def load_guidelines(touched: list[str]) -> str:
    """S1: per-directory rules mined from upstream review comments. File names: src__flask.md, tests.md, ... (slashes -> __)."""
    import os
    seen, parts = set(), []
    for path in touched:
        segs = path.split("/")
        candidates = ["/".join(segs[:i]) for i in range(min(len(segs) - 1, 3), 0, -1)]  # deepest dir first
        for d in candidates:
            name = d.replace("/", "__") + ".md"
            fp = os.path.join(settings.guidelines_dir, name)
            if d not in seen and os.path.isfile(fp):
                seen.add(d)
                with open(fp) as f:
                    parts.append(f"### Guidelines for `{d}/`\n" + f.read().strip())
                break
    return "\n\n".join(parts)


def build_prompt(task: Task) -> str:
    parts = [f"# Pull request #{task.pr_number} in {task.repo}", f"base {task.base_sha[:10]} -> head {task.head_sha[:10]}", ""]
    parts += ["## Diff", "```diff", _clip(task.diff, 60_000), "```", ""]
    if task.config != "diff_only":
        parts += [f"## pytest (exit {task.pytest_rc})", "```", _clip(task.pytest_output, 8_000), "```", ""]
        parts += [f"## ruff (exit {task.ruff_rc})", "```", _clip(task.ruff_output, 3_000), "```", ""]
        parts += ["## Touched files (head revision)"]
        for path, content in (task.files or {}).items():
            parts += [f"### {path}", "```python", _clip(content, 40_000), "```", ""]
    if task.config == "guided":
        g = load_guidelines(task.touched_files or [])
        if g:
            parts += ["## Project review guidelines (distilled from maintainers' past review comments; cite the rule id when a finding applies one)", g, ""]
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

    agentic = task.config == "agentic"
    input_items: list = [
        {"role": "system", "content": SYSTEM + (AGENT_SYSTEM_SUFFIX if agentic else "")},
        {"role": "user", "content": build_prompt(task)},
    ]
    tools = AGENT_TOOLS if agentic else ([READ_FILE_TOOL] if task.config != "diff_only" else [])
    budget = settings.agent_max_tool_calls if agentic else settings.read_file_max_calls

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
        if not calls or tool_calls >= budget:
            break
        input_items += resp.output
        for c in calls:
            tool_calls += 1
            try:
                args = json.loads(c.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                out = _run_tool(task, c.name, args)
            except Exception as e:  # noqa: BLE001
                out = f"error: {e!r}"
            input_items.append({"type": "function_call_output", "call_id": c.call_id, "output": _clip(out, 40_000)})
            if tool_calls >= budget:
                tools = []  # last round: force an answer

    try:
        parsed = json.loads(resp.output_text)
    except json.JSONDecodeError:
        log.error("reviewer returned non-JSON: %s", resp.output_text[:500])
        parsed = {"verdict": "REQUEST_CHANGES", "summary": "reviewer output unparseable", "findings": []}

    return {**parsed, "model": f"{model} (effort={settings.reasoning_effort})", "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tool_calls": tool_calls, "seconds": time.monotonic() - t0}
