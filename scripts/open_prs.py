#!/usr/bin/env python3
"""Open N trivial PRs against the fork to exercise the pipeline (burst test).

Each PR adds one line to CHANGES.rst on its own branch. Uses the active `gh` login.
  scripts/open_prs.py 20            # open 20 PRs
  scripts/open_prs.py --close-all   # close every open PR with the burst/ prefix
"""
import argparse
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

REPO = "anthonyzhao27/flask"


def sh(*a, cwd=None, check=True):
    return subprocess.run(a, cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


def open_one(work: str, i: int, stamp: str) -> str:
    br = f"burst/{stamp}-{i:02d}"
    sh("git", "worktree", "add", "-q", "-b", br, f"{work}/wt{i}", "origin/main", cwd=work)
    wt = f"{work}/wt{i}"
    with open(f"{wt}/CHANGES.rst", "a") as f:
        f.write(f"\n.. burst {stamp} #{i}\n")
    sh("git", "commit", "-qam", f"burst {stamp} #{i}", cwd=wt)
    sh("git", "push", "-q", "-u", "origin", br, cwd=wt)
    url = sh("gh", "pr", "create", "--repo", REPO, "--base", "main", "--head", br, "--title", f"burst {stamp} #{i}",
             "--body", "pr-runtime burst test", cwd=wt)
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=1)
    ap.add_argument("--close-all", action="store_true")
    ap.add_argument("--parallel", type=int, default=8)
    a = ap.parse_args()

    if a.close_all:
        nums = sh("gh", "pr", "list", "--repo", REPO, "--state", "open", "--limit", "200", "--json", "number,headRefName",
                  "--jq", '.[] | select(.headRefName | startswith("burst/")) | .number').split()
        for n in nums:
            sh("gh", "pr", "close", n, "--repo", REPO, "--delete-branch", check=False)
            print("closed", n)
        return

    stamp = time.strftime("%m%d-%H%M%S")
    with tempfile.TemporaryDirectory() as work:
        sh("git", "clone", "-q", "--filter=blob:none", f"https://github.com/{REPO}.git", work)
        t0 = time.time()
        with ThreadPoolExecutor(a.parallel) as ex:
            for url in ex.map(lambda i: open_one(work, i, stamp), range(a.n)):
                print(url, flush=True)
        print(f"opened {a.n} PRs in {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
