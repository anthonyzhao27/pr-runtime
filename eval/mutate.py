#!/usr/bin/env python3
"""Synthetic bugs: single-operator mutations of src/flask that the test suite does NOT catch.

Each mutant is one AST-level change (comparison flip, boolean operator swap, off-by-one, negation drop,
early-return removal, constant tweak). Tests are run per mutant; survivors (suite still green) become
`bug/mut-<id>` branches: a defect in real code with no failing test. Killed mutants are kept as a small
red set. Entries are labeled synthetic=true so results separate historical from synthetic bugs.
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

CMP_FLIP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
            ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
SKIP_FILES = {"src/flask/__init__.py", "src/flask/__main__.py", "src/flask/typing.py", "src/flask/testing.py", "src/flask/debughelpers.py"}


class Sites(ast.NodeVisitor):
    def __init__(self) -> None:
        self.sites: list[tuple[str, ast.AST]] = []

    def visit_Compare(self, node):
        if len(node.ops) == 1 and type(node.ops[0]) in CMP_FLIP:
            self.sites.append(("cmp", node))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.sites.append(("boolop", node))
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            self.sites.append(("not", node))
        self.generic_visit(node)

    def visit_BinOp(self, node):
        if isinstance(node.op, (ast.Add, ast.Sub)) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, int):
            self.sites.append(("offby1", node))
        self.generic_visit(node)

    def visit_If(self, node):
        # if-body that is a single `return X` / `raise` / `continue` guard
        if len(node.body) == 1 and isinstance(node.body[0], (ast.Return, ast.Continue)) and not node.orelse:
            self.sites.append(("guard", node))
        self.generic_visit(node)

    def visit_Call(self, node):
        if len(node.args) >= 2 and not node.keywords and all(isinstance(a, (ast.Name, ast.Attribute)) for a in node.args[:2]):
            self.sites.append(("swap_args", node))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        for i, stmt in enumerate(node.body):
            # a single-line call statement or simple assignment that is not the only statement
            if len(node.body) > 2 and isinstance(stmt, (ast.Expr, ast.Assign, ast.AugAssign)) and \
               getattr(stmt, "end_lineno", stmt.lineno) == stmt.lineno and not (i == 0 and isinstance(stmt, ast.Expr)):
                self.sites.append(("del_stmt", stmt))
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            self.sites.append(("bool", node))
        self.generic_visit(node)


def mutate_text(kind: str, node: ast.AST, lines: list[str]) -> str | None:
    """Apply one mutation as a text edit on the node's own line, using AST column offsets. Returns the new line."""
    if getattr(node, "end_lineno", node.lineno) != node.lineno and kind != "guard":
        return None
    line = lines[node.lineno - 1]
    if kind == "cmp":
        a, b = node.left.end_col_offset, node.comparators[0].col_offset
        seg = line[a:b]
        table = {"==": "!=", "!=": "==", "<=": "<", "<": "<=", ">=": ">", ">": ">=", " is not ": " is ", " is ": " is not ",
                 " not in ": " in ", " in ": " not in "}
        for old, new in table.items():
            if old in seg:
                return line[:a] + seg.replace(old, new, 1) + line[b:]
        return None
    if kind == "boolop":
        a, b = node.values[0].end_col_offset, node.values[1].col_offset
        seg = line[a:b]
        if " and " in seg:
            return line[:a] + seg.replace(" and ", " or ", 1) + line[b:]
        if " or " in seg:
            return line[:a] + seg.replace(" or ", " and ", 1) + line[b:]
        return None
    if kind == "not":
        a, b = node.col_offset, node.operand.col_offset
        seg = line[a:b]
        if seg.startswith("not"):
            return line[:a] + line[b:]
        return None
    if kind == "offby1":
        r = node.right
        return line[: r.col_offset] + str(r.value + 1) + line[r.end_col_offset:]
    if kind == "bool":
        return line[: node.col_offset] + ("False" if node.value else "True") + line[node.end_col_offset:]
    if kind == "swap_args":
        a0, a1 = node.args[0], node.args[1]
        if a0.lineno != a1.lineno or a0.lineno != node.lineno:
            return None
        t0, t1 = line[a0.col_offset:a0.end_col_offset], line[a1.col_offset:a1.end_col_offset]
        if t0 == t1:
            return None
        return line[:a0.col_offset] + t1 + line[a0.end_col_offset:a1.col_offset] + t0 + line[a1.end_col_offset:]
    if kind == "del_stmt":
        indent = line[: len(line) - len(line.lstrip())]
        return indent + "pass\n" if False else ("__GUARD__", node.lineno, indent + "pass  # noqa\n")
    if kind == "guard":
        stmt = node.body[0]
        if getattr(stmt, "end_lineno", stmt.lineno) != stmt.lineno:
            return None
        body_line = lines[stmt.lineno - 1]
        indent = body_line[: len(body_line) - len(body_line.lstrip())]
        return ("__GUARD__", stmt.lineno, indent + "pass\n")
    return None


def describe(kind: str, node: ast.AST, src_lines: list[str]) -> str:
    line = src_lines[node.lineno - 1].strip()
    return {"cmp": "flip comparison operator", "boolop": "swap and/or", "not": "drop a negation",
            "offby1": "off-by-one on an integer constant", "guard": "remove an early-return guard",
            "bool": "flip a boolean literal", "swap_args": "swap two call arguments",
            "del_stmt": "delete a statement"}[kind] + f" in `{line[:80]}`"


def run_tests(repo: str) -> bool:
    p = subprocess.run(["uv", "run", "--no-sync", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--tb=no"],
                       cwd=repo, capture_output=True, text=True, timeout=600)
    return p.returncode == 0


def sh(*a, cwd):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--survivors", type=int, default=22, help="green (uncaught) mutants to keep")
    ap.add_argument("--killed", type=int, default=6, help="red (caught) mutants to keep")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--noisy", action="store_true",
                    help="pair each green mutant with a clean-base/<sha> branch as PR base, so the bug hides inside a real refactor diff")
    ap.add_argument("--kinds", default="", help="comma list of mutation kinds to use (default all)")
    ap.add_argument("--max-per-kind", type=int, default=8)
    a = ap.parse_args()
    repo = a.repo
    rng = random.Random(a.seed)

    sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)
    files = sorted(p for p in Path(repo, "src/flask").rglob("*.py") if str(p.relative_to(repo)) not in SKIP_FILES)
    sites = []
    for f in files:
        rel = str(f.relative_to(repo))
        tree = ast.parse(f.read_text())
        v = Sites()
        v.visit(tree)
        for kind, node in v.sites:
            # Skip docstring-adjacent and obviously test-irrelevant spots like __repr__.
            sites.append((rel, kind, node.lineno, getattr(node, "end_lineno", node.lineno), node))
    if a.kinds:
        sites = [x for x in sites if x[1] in a.kinds.split(",")]
    rng.shuffle(sites)
    print(f"{len(sites)} mutation sites in {len(files)} files", file=sys.stderr)
    per_kind: dict[str, int] = {}
    clean_bases = []
    if a.noisy:
        corpus = json.loads((OUT / "corpus.json").read_text()) if (OUT / "corpus.json").exists() else []
        clean_bases = [e for e in corpus if e["kind"] == "clean"]
        if not clean_bases:
            sys.exit("--noisy needs clean entries in corpus.json (run build_corpus.py first)")

    entries, survivors, killed, tried = [], 0, 0, 0
    for rel, kind, lineno, end_lineno, _ in sites:
        if survivors >= a.survivors and killed >= a.killed:
            break
        if per_kind.get(kind, 0) >= a.max_per_kind:
            continue
        tried += 1
        path = Path(repo, rel)
        original = path.read_text()
        tree = ast.parse(original)
        v = Sites()
        v.visit(tree)
        target = next((n for k, n in v.sites if k == kind and n.lineno == lineno), None)
        if target is None:
            continue
        orig_lines = original.splitlines(keepends=True)
        edit = mutate_text(kind, target, orig_lines)
        if edit is None:
            continue
        if isinstance(edit, tuple):
            _, ln, text = edit
            orig_lines[ln - 1] = text
            lineno = ln
        else:
            if edit == orig_lines[lineno - 1]:
                continue
            orig_lines[lineno - 1] = edit
        candidate = "".join(orig_lines)
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        path.write_text(candidate)
        # Must still import and lint-parse; run tests.
        green = run_tests(repo)
        mid = f"mut-{abs(hash((rel, kind, lineno))) % 10**6:06d}"
        keep = (green and survivors < a.survivors) or (not green and killed < a.killed)
        if keep and a.noisy and not green:
            keep = False  # noisy set is green-only
        if keep:
            per_kind[kind] = per_kind.get(kind, 0) + 1
            variant = ("noisy" if a.noisy else "green") if green else "red"
            branch = f"bug/{mid}-{variant}"
            sh("git", "checkout", "-q", "-B", branch, "origin/main", cwd=repo)
            path.write_text(candidate)
            sh("git", "commit", "-qam", f"Simplify {Path(rel).stem} logic", cwd=repo)
            # head must be a descendant of base for GitHub to open the PR; clean-base = main minus C, so main is ahead. OK.
            if not a.no_push:
                sh("git", "push", "-qf", "origin", branch, cwd=repo)
            entry = {"kind": "bug", "variant": variant, "synthetic": True, "branch": branch,
                     "mutation": kind, "upstream_msg": f"synthetic: {describe(kind, target, original.splitlines())}",
                     "tests_pass": green, "hunks": [{"path": rel, "start": lineno, "end": lineno}], "src_files": [rel]}
            if a.noisy:
                cb = clean_bases[(survivors) % len(clean_bases)]
                entry["base_branch"] = cb["base_branch"]
                entry["noise_commit"] = cb["upstream_commit"]
                entry["noise_msg"] = cb["upstream_msg"]
            entries.append(entry)
            survivors += green
            killed += not green
            print(f"  {'green' if green else 'red  '} {branch:24s} {rel}:{lineno} {kind}", file=sys.stderr)
        sh("git", "checkout", "-q", "--force", "origin/main", cwd=repo)
        path.write_text(original)
    print(f"tried {tried}; kept {survivors} green + {killed} red", file=sys.stderr)

    OUT.mkdir(exist_ok=True)
    existing = json.loads((OUT / "corpus.json").read_text()) if (OUT / "corpus.json").exists() else []
    drop = {"noisy"} if a.noisy else {"green", "red"}
    existing = [e for e in existing if not (e.get("synthetic") and e["variant"] in drop)]
    (OUT / "corpus.json").write_text(json.dumps(existing + entries, indent=2))
    print(f"corpus now {len(existing) + len(entries)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()
