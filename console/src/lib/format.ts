import type { Task } from "./types";

export function shortSha(sha: string | null | undefined, n = 7): string {
  return sha ? sha.slice(0, n) : "—";
}

export function prUrl(t: Pick<Task, "repo" | "pr_number">): string {
  return `https://github.com/${t.repo}/pull/${t.pr_number}`;
}

export function fileUrl(repo: string, sha: string, path: string, line: number | null): string {
  const base = `https://github.com/${repo}/blob/${sha}/${path}`;
  return line ? `${base}#L${line}` : base;
}

export function fmtSeconds(s: number | null | undefined, digits = 1): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  if (s < 10) return `${s.toFixed(digits)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s - m * 60);
  return `${m}m${r.toString().padStart(2, "0")}s`;
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
  return String(n);
}

export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const d = Math.max(0, Math.round((now - t) / 1000));
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  if (d < 86400) return `${Math.floor(d / 3600)}h`;
  return `${Math.floor(d / 86400)}d`;
}

export function absTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** Total seconds for a task: timings.total, else sum of displayed phases, else wall clock so far. */
export function taskTotalSeconds(t: Task, now = Date.now()): number | null {
  const tm = t.timings ?? {};
  if (typeof tm.total === "number") return tm.total;
  // In flight: wall clock since admission (or creation while still queued) so the number ticks.
  if (t.state === "queued" || t.state === "running" || t.state === "reviewing") {
    const start = Date.parse(t.admitted_at ?? t.created_at);
    if (!Number.isNaN(start)) return Math.max(0, (now - start) / 1000);
  }
  const phases = ["wait", "prepare", "fetch", "checkout", "diff", "pytest", "ruff", "llm", "post"];
  const sum = phases.reduce((acc, k) => acc + (typeof tm[k] === "number" ? tm[k] : 0), 0);
  return sum > 0 ? sum : null;
}
