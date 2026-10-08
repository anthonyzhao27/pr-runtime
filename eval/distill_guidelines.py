#!/usr/bin/env python3
"""S1 step 2: distill mined review comments into per-directory, checkable rules with provenance.

One reviewer-model call per area. Output: eval/guidelines/<dir with / -> __>.md, each rule tagged with source URLs.
"compiled, not written": re-run after mining to regenerate.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from openai import OpenAI

HERE = Path(__file__).resolve().parent / "guidelines"
MODEL = os.environ.get("REVIEWER_MODEL", "gpt-6-astra")
rows = [json.loads(l) for l in (HERE / "raw_comments.jsonl").read_text().splitlines() if l.strip()]

def area(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("src/flask/") or path.startswith("flask/"):
        return "src/flask"
    if path.startswith("tests/"):
        return "tests"
    if path.startswith("docs/"):
        return "docs"
    return None

groups: dict[str, list] = defaultdict(list)
for r in rows:
    a = area(r["path"])
    if a:
        groups[a].append(r)

client = OpenAI()
for a, items in groups.items():
    items = items[:260]
    bullet = "\n".join(f"[{i}] ({r['path']}:{r['line']}) {r['body'][:400].replace(chr(10), ' ')}  <{r['url']}>" for i, r in enumerate(items))
    prompt = f"""You are turning a maintainer's review history into a short, checkable review checklist for the directory `{a}/` of the Flask repository.
Below are {len(items)} real review comments maintainers left on pull requests touching that directory, each with an index and a source URL.

Write at most 15 rules. Requirements:
- Each rule must be concrete and checkable by reading a diff (not "write good code"). Prefer rules that recur across several comments.
- Format exactly: `- **{a.split('/')[-1].upper()}-NN** <rule in one sentence>. _Why:_ <one short clause>. _Sources:_ <2-5 comment indices like [12] [87]>`
- Skip anything about CHANGES.rst formatting, typos, or thanks. Skip mypy noise unless it reflects a real typing convention.
- Order by how often the concern appears.
Output only the rules, no preamble.

Review comments:
{bullet}"""
    r = client.responses.create(model=MODEL, input=prompt, reasoning={"effort": "medium"})
    text = r.output_text.strip()
    # replace indices with URLs for provenance
    import re
    def sub(m):
        i = int(m.group(1))
        return f"[{items[i]['pr']}]({items[i]['url']})" if i < len(items) else m.group(0)
    text = re.sub(r"\[(\d+)\]", sub, text)
    out = HERE / (a.replace("/", "__") + ".md")
    out.write_text(f"# Review guidelines for `{a}/`\n\nDistilled from {len(items)} upstream review comments ({items[-1]['created'][:10]} → {items[0]['created'][:10]}) by {MODEL}. Each rule links the PRs it came from.\n\n{text}\n")
    print(f"{a}: {text.count(chr(10)) + 1} rules -> {out.name} ({r.usage.input_tokens} in / {r.usage.output_tokens} out)", file=sys.stderr)
