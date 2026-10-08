#!/usr/bin/env python3
"""Cross-file bugs (variant `xfile`): flip a boolean/int default in a function *signature* in file A when callers in
*other* files rely on that default (call without the kwarg) and the suite stays green.

The PR diff shows only A. Whether the change is safe depends on B, which a diff-only or touched-files-only reviewer
never sees. This is the one place an agentic reviewer (grep for callers) can beat context stuffing.
"""
from __future__ import annotations

import argparse
import ast
import json
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "corpus"
SKIP = {"src/flask/__init__.py", "src/flask/__main__.py", "src/flask/typing.py", "src/flask/testing.py", "src/flask/debughelpers.py"}


def sh(*a, cwd, check=True):
    return subprocess.run(a, cwd=cwd, check=check, capture_output=True, text=True).stdout


def run_tests(repo):
    try:
        p = subprocess.run(["uv", "run", "--no-sync", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--tb=no"], cwd=repo,
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False
    return p.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    repo = a.repo
    rng = random.Random(a.seed)
    sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)

    sites = []
    for f in sorted(Path(repo, "src/flask").rglob("*.py")):
        rel = str(f.relative_to(repo))
        if rel in SKIP:
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            args = node.args
            # kw defaults aligned to the last len(defaults) positional args, plus kwonly defaults
            pos = list(zip(args.args[len(args.args) - len(args.defaults):], args.defaults))
            kwo = [(k, d) for k, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
            for arg, default in pos + kwo:
                if isinstance(default, ast.Constant) and type(default.value) in (bool, int) and default.lineno == default.end_lineno:
                    sites.append((rel, node.name, arg.arg, default))
    rng.shuffle(sites)
    print(f"{len(sites)} default-arg sites", file=sys.stderr)

    entries, kept, tried = [], 0, 0
    for rel, fn, argname, default in sites:
        if kept >= a.n:
            break
        # callers in OTHER files that call fn without passing argname
        out = subprocess.run(["git", "grep", "-n", "-E", rf"(^|[^A-Za-z0-9_]){fn}\(", "origin/main", "--", "src/flask"], cwd=repo, capture_output=True, text=True).stdout
        callers = []
        for line in out.splitlines():
            _, path, rest = line.split(":", 2)
            if path != rel and f"def {fn}(" not in rest and f"{argname}=" not in rest:
                callers.append(f"{path}:{rest.split(':', 1)[0]}")
        if not callers:
            continue
        tried += 1
        path = Path(repo, rel)
        original = path.read_text()
        lines = original.splitlines(keepends=True)
        ln = default.lineno
        line = lines[ln - 1]
        new_val = ("False" if default.value else "True") if isinstance(default.value, bool) else str(default.value + 1)
        new_line = line[: default.col_offset] + new_val + line[default.end_col_offset:]
        if new_line == line:
            continue
        lines[ln - 1] = new_line
        path.write_text("".join(lines))
        green = run_tests(repo)
        path.write_text(original)
        if not green:
            continue
        mid = f"xf-{abs(hash((rel, fn, argname))) % 10**6:06d}"
        branch = f"bug/{mid}-xfile"
        sh("git", "checkout", "-q", "-B", branch, "origin/main", cwd=repo)
        path.write_text("".join(lines))
        sh("git", "commit", "-qam", f"Adjust {fn} default", cwd=repo)
        if not a.no_push:
            sh("git", "push", "-qf", "origin", branch, cwd=repo)
        sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)
        entries.append({"kind": "bug", "variant": "xfile", "synthetic": True, "branch": branch, "mutation": "default_flip",
                        "upstream_msg": f"synthetic cross-file: default of `{argname}` in `{fn}()` ({rel}) flipped to {new_val}; "
                                        f"{len(callers)} caller(s) in other files rely on the default: {', '.join(callers[:4])}",
                        "callers": callers, "tests_pass": True, "hunks": [{"path": rel, "start": ln, "end": ln}], "src_files": [rel]})
        kept += 1
        print(f"  xfile {branch:22s} {rel}:{ln} {fn}({argname}) callers={len(callers)}", file=sys.stderr)
    print(f"tried {tried}; kept {kept}", file=sys.stderr)
    corpus = json.loads((OUT / "corpus.json").read_text())
    corpus = [e for e in corpus if e.get("variant") != "xfile"] + entries
    (OUT / "corpus.json").write_text(json.dumps(corpus, indent=2))
    print(f"corpus now {len(corpus)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
