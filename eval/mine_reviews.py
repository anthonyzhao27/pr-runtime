#!/usr/bin/env python3
"""S1 step 1: pull upstream pallets/flask PR review comments (what maintainers actually say in review).

Writes eval/guidelines/raw_comments.jsonl: one line per review comment with path, diff hunk, body, PR, url.
"""
import json
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "guidelines" / "raw_comments.jsonl"
PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 8  # 100 comments per page

rows = []
for page in range(1, PAGES + 1):
    out = subprocess.run(["gh", "api", f"repos/pallets/flask/pulls/comments?per_page=100&page={page}&sort=created&direction=desc"],
                         capture_output=True, text=True, check=True).stdout
    items = json.loads(out)
    if not items:
        break
    for c in items:
        body = (c.get("body") or "").strip()
        if len(body) < 15 or body.startswith(("LGTM", "Thanks", "thanks")):
            continue
        rows.append({"id": c["id"], "pr": c["pull_request_url"].rsplit("/", 1)[-1], "path": c.get("path"),
                     "line": c.get("line") or c.get("original_line"), "body": body, "hunk": (c.get("diff_hunk") or "")[-600:],
                     "user": (c.get("user") or {}).get("login", "ghost"), "url": c["html_url"], "created": c["created_at"]})
    print(f"page {page}: {len(items)} comments, kept {len(rows)} so far", file=sys.stderr)

OUT.parent.mkdir(exist_ok=True)
OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
from collections import Counter
dirs = Counter((r["path"] or "").split("/")[0] + ("/" + r["path"].split("/")[1] if (r["path"] or "").startswith("src/") else "") for r in rows)
print(f"wrote {len(rows)} comments; by area: {dict(dirs.most_common(8))}", file=sys.stderr)
