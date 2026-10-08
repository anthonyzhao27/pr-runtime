import type { Severity, TaskState, Verdict } from "../lib/types";

const STATE_CLASSES: Record<TaskState, string> = {
  queued: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300 ring-zinc-500/30",
  running: "bg-blue-500/15 text-blue-700 dark:text-blue-300 ring-blue-500/30",
  reviewing: "bg-violet-500/15 text-violet-700 dark:text-violet-300 ring-violet-500/30",
  posted: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300 ring-red-500/30",
  superseded: "bg-zinc-500/10 text-mute ring-zinc-500/20 line-through",
};

export const STATE_DOT: Record<TaskState, string> = {
  queued: "bg-zinc-400",
  running: "bg-blue-500",
  reviewing: "bg-violet-500",
  posted: "bg-emerald-500",
  failed: "bg-red-500",
  superseded: "bg-zinc-400",
};

const base = "inline-flex items-center h-[20px] px-1.5 rounded text-[11px] font-medium leading-none ring-1 ring-inset whitespace-nowrap";

export function StatePill({ state, pulse = true }: { state: TaskState; pulse?: boolean }) {
  const cls = STATE_CLASSES[state] ?? STATE_CLASSES.queued;
  const live = pulse && (state === "running" || state === "reviewing");
  return (
    <span className={`${base} ${cls}`}>
      {live && <span className={`mr-1 inline-block h-1.5 w-1.5 rounded-full ${STATE_DOT[state]} animate-pulse`} />}
      {state}
    </span>
  );
}

export function VerdictPill({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <span className="text-mute text-[11px]">—</span>;
  const cls =
    verdict === "APPROVE"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 ring-emerald-500/30"
      : "bg-amber-500/15 text-amber-700 dark:text-amber-300 ring-amber-500/30";
  return <span className={`${base} ${cls}`}>{verdict === "APPROVE" ? "APPROVE" : "REQUEST_CHANGES"}</span>;
}

const SEV_CLASSES: Record<Severity, string> = {
  blocker: "bg-red-500/15 text-red-700 dark:text-red-300 ring-red-500/30",
  major: "bg-orange-500/15 text-orange-700 dark:text-orange-300 ring-orange-500/30",
  minor: "bg-yellow-500/15 text-yellow-800 dark:text-yellow-300 ring-yellow-500/30",
  nit: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300 ring-zinc-500/30",
};

export function SeverityPill({ severity }: { severity: Severity }) {
  const cls = SEV_CLASSES[severity] ?? SEV_CLASSES.nit;
  return <span className={`${base} ${cls} uppercase tracking-wide`}>{severity}</span>;
}

export function ConfigPill({ config }: { config: string }) {
  return (
    <span className={`${base} font-mono bg-panel text-fg/80 ring-line`}>{config}</span>
  );
}

export function ColdDot({ cold, title }: { cold: boolean; title?: string }) {
  return (
    <span
      title={title ?? (cold ? "cold: assigned to a runner pod younger than 10s (pool was empty)" : "warm: assigned to a pre-warmed runner pod")}
      className={`inline-block h-2 w-2 rounded-full ${cold ? "bg-sky-400 ring-2 ring-sky-400/30" : "bg-orange-400 ring-2 ring-orange-400/30"}`}
    />
  );
}
