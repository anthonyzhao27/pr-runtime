#!/usr/bin/env python3
"""Merge a main run (bugs + clean) with a noisy run into one summary for the console's Eval page."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
main, noisy = (HERE / "results" / sys.argv[1]), (HERE / "results" / sys.argv[2])
extras = [HERE / "results" / x for x in sys.argv[3:]]  # runs whose *new* configs (e.g. guided) are added as-is
out = HERE / "results" / "combined"
out.mkdir(exist_ok=True)
ms, ns = json.loads((main / "summary.json").read_text()), json.loads((noisy / "summary.json").read_text())
merged = {"run_id": f"{main.name}+{noisy.name}", "configs": {}}
for cfg, s in ms["configs"].items():
    n = ns["configs"].get(cfg, {})
    bv = dict(s["by_variant"])
    bv.update({k: v for k, v in n.get("by_variant", {}).items() if k.startswith("noisy")})
    tot_bugs = s["n_bugs"] + n.get("n_bugs", 0)
    merged["configs"][cfg] = {
        **s,
        "n_bugs": tot_bugs,
        "recall_strict": round((s["recall_strict"] * s["n_bugs"] + n.get("recall_strict", 0) * n.get("n_bugs", 0)) / tot_bugs, 3),
        "recall_semantic": round((s["recall_semantic"] * s["n_bugs"] + n.get("recall_semantic", 0) * n.get("n_bugs", 0)) / tot_bugs, 3),
        "n_equivalent_excluded": s.get("n_equivalent_excluded", 0) + n.get("n_equivalent_excluded", 0),
        "by_variant": bv,
    }
for ex in extras:
    es = json.loads((ex / "summary.json").read_text())
    for cfg, s in es["configs"].items():
        if cfg not in merged["configs"]:
            merged["configs"][cfg] = s
    merged["run_id"] += "+" + ex.name
(out / "summary.json").write_text(json.dumps(merged, indent=1))
rows = json.loads((main / "rows.json").read_text()) + json.loads((noisy / "rows.json").read_text())
for ex in extras:
    rows += [r for r in json.loads((ex / "rows.json").read_text()) if r["config"] not in ("full", "diff_only")]
(out / "rows.json").write_text(json.dumps(rows, indent=1))
lines = [f"# Eval combined: {merged['run_id']}", "", "| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | p50 total (burst) | p95 | tokens in/out |", "|---|---|---|---|---|---|---|---|---|"]
for cfg, s in merged["configs"].items():
    lines.append(f"| {cfg} | {s['n_bugs']} | {s['recall_strict']} | {s['recall_semantic']} | {s['n_clean']} | {s['fp_rate']} | {s['p50_total_s']}s | {s['p95_total_s']}s | {s.get('mean_tokens_in','-')}/{s.get('mean_tokens_out','-')} |")
lines += ["", "| config | variant | n | strict | semantic |", "|---|---|---|---|---|"]
for cfg, s in merged["configs"].items():
    for var, v in s["by_variant"].items():
        if "/" in var:
            lines.append(f"| {cfg} | {var} | {v['n']} | {v['recall_strict']} | {v['recall_semantic']} |")
(out / "summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
