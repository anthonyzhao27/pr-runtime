import { fmtSeconds } from "../lib/format";

export interface Phase {
  key: string;
  label: string;
  color: string;
}

/** Display phases. `fetch` absorbs prepare/checkout/diff so the bar stays readable. */
export const PHASES: Phase[] = [
  { key: "fetch", label: "fetch", color: "bg-zinc-400 dark:bg-zinc-500" },
  { key: "pytest", label: "pytest", color: "bg-sky-500" },
  { key: "ruff", label: "ruff", color: "bg-teal-500" },
  { key: "llm", label: "llm", color: "bg-violet-500" },
  { key: "post", label: "post", color: "bg-emerald-500" },
];

export function phaseValues(timings: Record<string, number> | undefined): Record<string, number> {
  const t = timings ?? {};
  const n = (k: string) => (typeof t[k] === "number" && t[k] > 0 ? t[k] : 0);
  return {
    fetch: n("prepare") + n("fetch") + n("checkout") + n("diff"),
    pytest: n("pytest"),
    ruff: n("ruff"),
    llm: n("llm"),
    post: n("post"),
  };
}

interface Props {
  timings: Record<string, number> | undefined;
  total?: number | null; // override the text total (e.g. live wall clock)
  height?: number;
  showLabels?: boolean;
  className?: string;
}

/** Tiny stacked bar of the phase timings with total seconds as text. */
export function PhaseBar({ timings, total, height = 6, showLabels = false, className = "" }: Props) {
  const v = phaseValues(timings);
  const sum = PHASES.reduce((a, p) => a + (v[p.key] ?? 0), 0);
  const totalText = fmtSeconds(total ?? (sum > 0 ? sum : null));
  const title = PHASES.filter((p) => (v[p.key] ?? 0) > 0)
    .map((p) => `${p.label} ${fmtSeconds(v[p.key] ?? 0)}`)
    .join(" · ");

  return (
    <div className={`flex items-center gap-2 min-w-0 ${className}`} title={title || "no timings yet"}>
      <div className="flex-1 min-w-[64px] rounded-sm overflow-hidden bg-line/60 flex" style={{ height }}>
        {sum > 0 &&
          PHASES.map((p) => {
            const val = v[p.key] ?? 0;
            if (val <= 0) return null;
            return <div key={p.key} className={`${p.color} h-full`} style={{ width: `${(val / sum) * 100}%` }} />;
          })}
      </div>
      <span className="font-mono text-[11px] text-fg/80 tabular-nums w-[48px] text-right shrink-0">{totalText}</span>
      {showLabels && (
        <div className="flex gap-3 text-[11px] text-mute shrink-0">
          {PHASES.map((p) => {
            const val = v[p.key] ?? 0;
            return (
              <span key={p.key} className={`inline-flex items-center gap-1 ${val > 0 ? "" : "opacity-40"}`}>
                <span className={`inline-block h-2 w-2 rounded-sm ${p.color}`} />
                {p.label}
                <span className="font-mono text-fg/80 tabular-nums">{val > 0 ? fmtSeconds(val) : "—"}</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
