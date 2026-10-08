#!/usr/bin/env python3
"""Score an eval run.

caught_strict:   a finding in the same file within +/-N lines of a reverted hunk (N=15).
caught_semantic: the judge model says a finding describes the defect the upstream fix addressed.
fp_rate:         share of clean PRs that got REQUEST_CHANGES; also findings per clean PR.

  eval/score.py results/<run_id> [--judge]        # --judge needs OPENAI_API_KEY + JUDGE_MODEL
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

WINDOW = 15


def strict_hit(task: dict, hunks: list[dict]) -> bool:
    for f in task.get("findings", []):
        if f.get("line") is None:
            continue
        for h in hunks:
            if f["path"] == h["path"] and h["start"] - WINDOW <= f["line"] <= h["end"] + WINDOW:
                return True
    return False


def semantic_hit(task: dict, entry: dict, model: str) -> bool | None:
    from openai import OpenAI
    findings = task.get("findings", [])
    if not findings:
        return False
    client = OpenAI()
    prompt = (
        "A pull request reverted a real bug fix in Flask, re-introducing a defect.\n"
        f"Upstream fix commit message: {entry['upstream_msg']!r}\n"
        f"Files/lines where the bug was re-introduced: {json.dumps(entry['hunks'])}\n\n"
        "Here are the findings an automated reviewer posted on that PR:\n"
        + "\n".join(f"- {f['path']}:{f.get('line')} [{f['severity']}] {f['claim']} — {f.get('evidence') or ''}" for f in findings)
        + "\n\nDoes at least one finding identify that defect (same behavior, same place, not just a vague nearby comment)? "
          "Answer with JSON {\"hit\": true|false, \"why\": \"...\"}."
    )
    r = client.responses.create(model=model, input=prompt, text={"format": {"type": "json_object"}})
    try:
        return bool(json.loads(r.output_text)["hit"])
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--judge", action="store_true")
    a = ap.parse_args()
    run = Path(a.run_dir).resolve()  # resolve so scoring via the `latest` symlink never points it at itself
    raw = json.loads((run / "raw.json").read_text())
    judge_model = os.environ.get("JUDGE_MODEL", "")

    configs = sorted({r["config"] for r in raw})
    summary = {"run_id": run.name, "configs": {}}
    rows = []
    for cfg in configs:
        rs = [r for r in raw if r["config"] == cfg and r["task"]["state"] == "posted"]
        bugs = [r for r in rs if r["kind"] == "bug" and not r.get("equivalent")]
        equivalent = [r for r in rs if r["kind"] == "bug" and r.get("equivalent")]
        clean = [r for r in rs if r["kind"] == "clean"]
        for r in bugs:
            r["strict"] = strict_hit(r["task"], r["hunks"])
            r["semantic"] = semantic_hit(r["task"], r, judge_model) if a.judge and judge_model else None
            r["flagged"] = r["task"].get("verdict") == "REQUEST_CHANGES"
            rows.append({"config": cfg, "variant": r["variant"], "pr": r["pr"], "branch": r["branch"], "strict": r["strict"],
                         "semantic": r["semantic"], "flagged": r["flagged"], "findings": len(r["task"].get("findings", [])),
                         "total_s": r["task"].get("timings", {}).get("total"), "upstream_msg": r["upstream_msg"]})
        for r in clean:
            rows.append({"config": cfg, "variant": "clean", "pr": r["pr"], "branch": r["branch"], "strict": None, "semantic": None,
                         "flagged": r["task"].get("verdict") == "REQUEST_CHANGES",
                         "findings": len(r["task"].get("findings", [])), "total_s": r["task"].get("timings", {}).get("total"),
                         "upstream_msg": r["upstream_msg"]})

        def rate(xs):
            return round(sum(1 for x in xs if x) / len(xs), 3) if xs else None

        by_var = {}
        for var in ("red", "green", "noisy"):
            for origin in ("historical", "synthetic"):
                vs = [r for r in bugs if r["variant"] == var and bool(r.get("synthetic")) == (origin == "synthetic")]
                if not vs:
                    continue
                by_var[f"{var}/{origin}"] = {"n": len(vs), "recall_strict": rate([r["strict"] for r in vs]),
                                             "recall_semantic": rate([r["semantic"] for r in vs]) if a.judge else None,
                                             "flag_rate": rate([r["flagged"] for r in vs])}
            vs = [r for r in bugs if r["variant"] == var]
            if vs:
                by_var[var] = {"n": len(vs), "recall_strict": rate([r["strict"] for r in vs]),
                               "recall_semantic": rate([r["semantic"] for r in vs]) if a.judge else None,
                               "flag_rate": rate([r["flagged"] for r in vs])}
        totals = [r["task"].get("timings", {}).get("total") for r in rs if r["task"].get("timings", {}).get("total")]
        summary["configs"][cfg] = {
            "n_bugs": len(bugs), "n_clean": len(clean), "n_equivalent_excluded": len(equivalent),
            "recall_strict": rate([r["strict"] for r in bugs]),
            "recall_semantic": rate([r["semantic"] for r in bugs]) if a.judge else None,
            "fp_rate": rate([r["task"].get("verdict") == "REQUEST_CHANGES" for r in clean]),
            "findings_per_clean_pr": round(statistics.mean([len(r["task"].get("findings", [])) for r in clean]), 2) if clean else None,
            "p50_total_s": round(statistics.median(totals), 1) if totals else None,
            "p95_total_s": round(sorted(totals)[int(0.95 * (len(totals) - 1))], 1) if totals else None,
            "by_variant": by_var,
        }

    (run / "summary.json").write_text(json.dumps(summary, indent=1))
    (run / "rows.json").write_text(json.dumps(rows, indent=1))
    lines = [f"# Eval {run.name}", "", "| config | bugs | recall (strict) | recall (semantic) | clean PRs | FP rate | findings/clean PR | p50 total | p95 total |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cfg, s in summary["configs"].items():
        lines.append(f"| {cfg} | {s['n_bugs']} | {s['recall_strict']} | {s['recall_semantic']} | {s['n_clean']} | {s['fp_rate']} | {s['findings_per_clean_pr']} | {s['p50_total_s']}s | {s['p95_total_s']}s |")
    lines += ["", "## By variant", "", "| config | variant | n | recall (strict) | recall (semantic) | flagged |", "|---|---|---|---|---|---|"]
    for cfg, s in summary["configs"].items():
        for var, v in s["by_variant"].items():
            lines.append(f"| {cfg} | {var} | {v['n']} | {v['recall_strict']} | {v['recall_semantic']} | {v['flag_rate']} |")
    (run / "summary.md").write_text("\n".join(lines) + "\n")
    latest = run.parent / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(run.name)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
