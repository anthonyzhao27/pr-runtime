import { useEffect, useState } from "react";
import { getEvalSummary } from "../lib/api";
import { fmtSeconds } from "../lib/format";
import type { EvalConfigMetrics, EvalSummary } from "../lib/types";

const METRICS: { key: keyof EvalConfigMetrics; label: string; fmt: (v: number) => string; hint: string }[] = [
  { key: "recall_strict", label: "recall (strict)", fmt: pct, hint: "planted bug found at the exact file:line" },
  { key: "recall_semantic", label: "recall (semantic)", fmt: pct, hint: "planted bug found per LLM judge, any line" },
  { key: "fp_rate", label: "false-positive rate", fmt: pct, hint: "clean PRs that got REQUEST_CHANGES" },
  { key: "n_bugs", label: "n bugs", fmt: (v) => String(v), hint: "planted-bug PRs in the corpus" },
  { key: "n_clean", label: "n clean", fmt: (v) => String(v), hint: "clean PRs in the corpus" },
  { key: "p50_total_s", label: "p50 total", fmt: (v) => fmtSeconds(v), hint: "median end-to-end seconds" },
];

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

type State = { kind: "loading" } | { kind: "none" } | { kind: "error"; message: string } | { kind: "ok"; data: EvalSummary };

export function EvalPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getEvalSummary()
      .then((d) => {
        if (cancelled) return;
        setState(d ? { kind: "ok", data: d } : { kind: "none" });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-[16px] font-semibold">Eval</h1>
        <span className="text-[12px] text-mute">
          context-config ablation from <span className="font-mono">eval/results/latest/summary.json</span>
        </span>
        {state.kind === "ok" && (
          <span className="ml-auto text-[12px] text-mute">
            run <span className="font-mono text-fg/80">{state.data.run_id}</span>
          </span>
        )}
      </div>

      {state.kind === "loading" && <div className="text-[12px] text-mute">loading…</div>}
      {state.kind === "none" && (
        <div className="rounded border border-dashed border-line px-3 py-10 text-center text-[13px] text-mute">no eval results yet</div>
      )}
      {state.kind === "error" && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">{state.message}</div>
      )}
      {state.kind === "ok" && <Results data={state.data} />}
    </div>
  );
}

function Results({ data }: { data: EvalSummary }) {
  const configs = Object.keys(data.configs);
  // Stable order: full first, then diff_only, then anything else alphabetically.
  configs.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="rounded border border-line overflow-hidden">
        <table className="w-full text-[13px]">
          <thead className="bg-panel text-[11px] uppercase tracking-wide text-mute">
            <tr className="h-[30px] border-b border-line text-left">
              <th className="px-3 font-medium">metric</th>
              {configs.map((c) => (
                <th key={c} className="px-3 font-medium text-right font-mono normal-case tracking-normal">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRICS.map((m) => {
              const vals = configs.map((c) => data.configs[c]?.[m.key]);
              const best = bestIndex(m.key, vals);
              return (
                <tr key={m.key} className="h-[34px] border-b border-line last:border-b-0">
                  <td className="px-3" title={m.hint}>
                    {m.label}
                  </td>
                  {configs.map((c, i) => {
                    const v = vals[i];
                    return (
                      <td
                        key={c}
                        className={`px-3 text-right font-mono tabular-nums ${best === i ? "text-fg font-medium" : "text-fg/80"}`}
                      >
                        {typeof v === "number" ? m.fmt(v) : "—"}
                        {best === i && configs.length > 1 && <span className="ml-1 text-accent">●</span>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <RecallChart data={data} configs={configs} />
    </div>
  );
}

function rank(c: string): number {
  return c === "full" ? 0 : c === "diff_only" ? 1 : 2;
}

/** Index of the "best" value per metric (higher recall, lower fp/p50; n counts have no best). */
function bestIndex(key: keyof EvalConfigMetrics, vals: (number | undefined)[]): number | null {
  if (key === "n_bugs" || key === "n_clean") return null;
  const lowerBetter = key === "fp_rate" || key === "p50_total_s";
  let best: number | null = null;
  vals.forEach((v, i) => {
    if (typeof v !== "number") return;
    if (best === null) {
      best = i;
      return;
    }
    const b = vals[best];
    if (typeof b !== "number") return;
    if (lowerBetter ? v < b : v > b) best = i;
  });
  return best;
}

/** Pure-SVG grouped bar chart: recall (strict + semantic) per config. */
function RecallChart({ data, configs }: { data: EvalSummary; configs: string[] }) {
  const W = 420;
  const H = 240;
  const pad = { l: 36, r: 12, t: 16, b: 34 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const group = iw / Math.max(configs.length, 1);
  const barW = Math.min(44, group * 0.32);
  const gap = 6;
  const y = (v: number) => pad.t + ih - Math.max(0, Math.min(1, v)) * ih;

  const series: { key: "recall_strict" | "recall_semantic"; label: string; cls: string }[] = [
    { key: "recall_strict", label: "strict", cls: "fill-accent" },
    { key: "recall_semantic", label: "semantic", cls: "fill-accent/40" },
  ];

  return (
    <div className="rounded border border-line bg-panel/40 p-3">
      <div className="flex items-center gap-4 text-[12px] mb-1">
        <span className="font-medium">recall per config</span>
        <span className="ml-auto flex items-center gap-3 text-mute">
          {series.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <svg width="10" height="10" aria-hidden>
                <rect width="10" height="10" rx="2" className={s.cls} />
              </svg>
              {s.label}
            </span>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="recall per config">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="stroke-line" strokeWidth={1} strokeDasharray={t === 0 ? undefined : "2 3"} />
            <text x={pad.l - 6} y={y(t) + 3.5} textAnchor="end" className="fill-mute" fontSize={10} fontFamily="ui-monospace, monospace">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {configs.map((c, ci) => {
          const m = data.configs[c];
          const cx = pad.l + group * ci + group / 2;
          const totalW = series.length * barW + (series.length - 1) * gap;
          const x0 = cx - totalW / 2;
          return (
            <g key={c}>
              {series.map((s, si) => {
                const v = m?.[s.key];
                if (typeof v !== "number") return null;
                const x = x0 + si * (barW + gap);
                const top = y(v);
                return (
                  <g key={s.key}>
                    <rect x={x} y={top} width={barW} height={pad.t + ih - top} rx={2} className={s.cls} />
                    <text x={x + barW / 2} y={top - 4} textAnchor="middle" fontSize={10} fontFamily="ui-monospace, monospace" className="fill-fg">
                      {Math.round(v * 100)}%
                    </text>
                  </g>
                );
              })}
              <text x={cx} y={H - pad.b + 16} textAnchor="middle" fontSize={11} fontFamily="ui-monospace, monospace" className="fill-fg">
                {c}
              </text>
              {m && (
                <text x={cx} y={H - pad.b + 28} textAnchor="middle" fontSize={9} className="fill-mute">
                  fp {pct(m.fp_rate)} · p50 {fmtSeconds(m.p50_total_s)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
