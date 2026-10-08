#!/usr/bin/env python3
"""Build the eval corpus on the Flask fork.

Bugs: historical upstream bug-fix commits whose *source* hunks reverse-apply cleanly onto current main.
  - red variant:   source reverted, tests kept        -> the suite fails (CI would be red)
  - green variant: source reverted, that commit's test changes reverted too -> suite passes (CI green)
  The green variant is the one that matters: it measures whether the reviewer reads code, not CI.
Clean: recent upstream commits touching src/, replayed forward as a PR (base = main with the commit
  reverted, head = main). Real merged changes, tests green on head.

Writes eval/corpus/corpus.json and pushes branches to the fork. Idempotent per branch name.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus"
GREP = ["fix", "bug", "regress", "incorrect", "wrong", "broken", "crash", "leak", "handle", "correctly", "ensure"]


def sh(*a, cwd, check=True, env=None) -> str:
    p = subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(a)}\n{p.stdout}\n{p.stderr}")
    return p.stdout


def run_tests(repo: str) -> tuple[bool, str]:
    p = subprocess.run(["uv", "run", "--no-sync", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--tb=line"],
                       cwd=repo, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]


def changed_files(repo: str, sha: str) -> list[str]:
    return [f for f in sh("git", "show", "--pretty=format:", "--name-only", sha, cwd=repo).split("\n") if f]


def new_side_ranges(repo: str, base: str, head: str, pathspec: str = "src") -> list[dict]:
    """Line ranges on the head side for every hunk in base..head (ground truth for 'where is the bug')."""
    diff = sh("git", "diff", "--unified=0", f"{base}..{head}", "--", pathspec, cwd=repo)
    out, path = [], None
    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            out.append({"path": path, "start": start, "end": max(start, start + count - 1)})
    return out


def candidates(repo: str, since: str) -> list[tuple[str, str]]:
    args = ["git", "log", "upstream/main", f"--since={since}", "--no-merges", "--format=%H %s", "-i"]
    for g in GREP:
        args += ["--grep", g]
    out = []
    for line in sh(*args, cwd=repo).split("\n"):
        if not line:
            continue
        sha, _, msg = line.partition(" ")
        files = changed_files(repo, sha)
        src = [f for f in files if re.match(r"src/flask/.*\.py$", f)]
        tst = [f for f in files if re.match(r"tests/.*\.py$", f)]
        other = [f for f in files if not re.match(r"(src/flask/.*\.py|tests/.*\.py|CHANGES\.rst|docs/.*)$", f)]
        if re.search(r"docstring|typing|type hint|annotation|lint|format|pre-commit|mypy|pyright|quote|comment|\bdev\b|docs?\b|changelog|spelling|typo", msg, re.I):
            continue
        # Tests touching is NOT required: a src-only fix that reverse-applies and keeps the suite green is a
        # green-test bug by construction (nothing covers it).
        if 1 <= len(src) <= 4 and not other:
            out.append((sha, msg))
    return out


def neutral_title(msg: str, i: int) -> str:
    # Do not leak "revert fix X" into the PR. Keep it bland, like a real sloppy PR.
    area = msg.split()[-1].strip(".") if msg else "internals"
    return f"Simplify {area} handling" if i % 2 else f"Refactor {area} logic"


def build_bug(repo: str, sha: str, msg: str, i: int, push: bool) -> list[dict]:
    short = sha[:8]
    entries = []
    sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)
    sh("git", "clean", "-fdq", "-e", ".venv", cwd=repo)

    # red: reverse only the source hunks
    rc = subprocess.run(["sh", "-c", f"git show {sha} -- src | git apply -R --check"], cwd=repo, capture_output=True)
    if rc.returncode != 0:
        return []
    red = f"bug/{short}-red"
    sh("git", "checkout", "-q", "-B", red, "origin/main", cwd=repo)
    sh("sh", "-c", f"git show {sha} -- src | git apply -R", cwd=repo)
    sh("git", "commit", "-qam", neutral_title(msg, i), cwd=repo)
    ok, tail = run_tests(repo)
    hunks = new_side_ranges(repo, "origin/main", red)
    if ok:
        # Tests do not catch it on current main: the whole thing is a green-test bug by itself.
        entries.append({"kind": "bug", "variant": "green", "branch": red, "upstream_fix": sha, "upstream_msg": msg,
                        "tests_pass": True, "hunks": hunks, "src_files": sorted({h["path"] for h in hunks})})
        if push:
            sh("git", "push", "-qf", "origin", red, cwd=repo)
        return entries
    entries.append({"kind": "bug", "variant": "red", "branch": red, "upstream_fix": sha, "upstream_msg": msg,
                    "tests_pass": False, "pytest_tail": tail[-600:], "hunks": hunks,
                    "src_files": sorted({h["path"] for h in hunks})})
    if push:
        sh("git", "push", "-qf", "origin", red, cwd=repo)

    # green: also reverse the test hunks from that commit
    rc = subprocess.run(["sh", "-c", f"git show {sha} -- tests | git apply -R --check"], cwd=repo, capture_output=True)
    if rc.returncode == 0:
        green = f"bug/{short}-green"
        sh("git", "checkout", "-q", "-B", green, red, cwd=repo)
        sh("sh", "-c", f"git show {sha} -- tests | git apply -R", cwd=repo)
        sh("git", "commit", "-qam", "Tidy tests", cwd=repo)
        ok2, _ = run_tests(repo)
        if ok2:
            entries.append({"kind": "bug", "variant": "green", "branch": green, "upstream_fix": sha, "upstream_msg": msg,
                            "tests_pass": True, "hunks": hunks, "src_files": sorted({h["path"] for h in hunks})})
            if push:
                sh("git", "push", "-qf", "origin", green, cwd=repo)
        else:
            sh("git", "branch", "-qD", green, cwd=repo) if False else None
    return entries


def build_clean(repo: str, n: int, push: bool) -> list[dict]:
    """Recent upstream commits touching src/, replayed forward: base = main minus C, head = main."""
    out = []
    log = sh("git", "log", "upstream/main", "-n", "400", "--no-merges", "--format=%H %s", "--", "src/flask", cwd=repo)
    for line in log.split("\n"):
        if len(out) >= n or not line:
            continue
        sha, _, msg = line.partition(" ")
        if re.search(r"\b(fix|bug|regress)", msg, re.I) or msg.lower().startswith(("release", "start version", "bump", "merge")):
            continue
        if len(changed_files(repo, sha)) > 6:
            continue
        files = changed_files(repo, sha)
        if not any(f.startswith("src/flask") and f.endswith(".py") for f in files):
            continue
        if any(not re.match(r"(src/flask/.*|tests/.*|CHANGES\.rst|docs/.*|examples/.*)$", f) for f in files):
            continue
        short = sha[:8]
        base = f"clean-base/{short}"
        head = f"clean/{short}"
        sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)
        sh("git", "clean", "-fdq", "-e", ".venv", cwd=repo)
        rc = subprocess.run(["git", "revert", "--no-edit", "--no-commit", sha], cwd=repo, capture_output=True)
        if rc.returncode != 0:
            sh("git", "revert", "--abort", cwd=repo, check=False)
            continue
        sh("git", "checkout", "-q", "-B", base, cwd=repo)
        sh("git", "commit", "-qam", f"base for {short}", cwd=repo)
        sh("git", "checkout", "-q", "-B", head, "origin/main", cwd=repo)
        # head must be ahead of base: add an empty-ish marker so the PR has a head commit distinct from main
        sh("git", "commit", "-q", "--allow-empty", "-m", msg.split("\n")[0][:70], cwd=repo)
        hunks = new_side_ranges(repo, base, head)
        out.append({"kind": "clean", "variant": "clean", "branch": head, "base_branch": base, "upstream_commit": sha,
                    "upstream_msg": msg, "tests_pass": True, "hunks": hunks,
                    "src_files": sorted({h["path"] for h in hunks})})
        if push:
            sh("git", "push", "-qf", "origin", base, head, cwd=repo)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to a clone of the fork with an `upstream` remote")
    ap.add_argument("--since", default="2016-01-01")
    ap.add_argument("--max-bugs", type=int, default=40)
    ap.add_argument("--clean", type=int, default=20)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    push = not a.no_push

    sh("git", "fetch", "-q", "origin", cwd=repo)
    sh("git", "fetch", "-q", "upstream", cwd=repo)
    cands = candidates(repo, a.since)
    print(f"{len(cands)} candidate fix commits", file=sys.stderr)
    entries: list[dict] = []
    for i, (sha, msg) in enumerate(cands):
        if sum(1 for e in entries if e["kind"] == "bug") >= a.max_bugs:
            break
        try:
            got = build_bug(repo, sha, msg, i, push)
        except Exception as e:  # noqa: BLE001
            print(f"  skip {sha[:8]}: {str(e).splitlines()[0][:100]}", file=sys.stderr)
            continue
        for g in got:
            print(f"  {g['variant']:5s} {g['branch']:28s} {msg[:60]}", file=sys.stderr)
        entries += got
    cleans = build_clean(repo, a.clean, push)
    for c in cleans:
        print(f"  clean {c['branch']:28s} {c['upstream_msg'][:60]}", file=sys.stderr)
    entries += cleans

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "corpus.json").write_text(json.dumps(entries, indent=2))
    kinds = {}
    for e in entries:
        kinds[e["variant"]] = kinds.get(e["variant"], 0) + 1
    print(f"wrote {len(entries)} entries: {kinds}", file=sys.stderr)
    sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)


if __name__ == "__main__":
    main()
