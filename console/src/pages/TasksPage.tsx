import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getStats, getTask, listTasks } from "../lib/api";
import { fmtInt, prUrl, relTime, shortSha, taskTotalSeconds } from "../lib/format";
import type { ServerEvent, Stats, Task, TaskState } from "../lib/types";
import { TASK_STATES } from "../lib/types";
import { useEvents, useTick } from "../lib/useEvents";
import { ColdDot, ConfigPill, STATE_DOT, StatePill, VerdictPill } from "../components/Pill";
import { PhaseBar } from "../components/PhaseBar";
import { LiveDot } from "../components/LiveDot";

const LIMIT = 100;

function sortNewest(a: Task, b: Task): number {
  return Date.parse(b.created_at) - Date.parse(a.created_at);
}

export function TasksPage() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const stateFilter = (params.get("state") ?? "") as TaskState | "";
  const prFilter = params.get("pr") ?? "";
  const [prInput, setPrInput] = useState(prFilter);

  const [tasks, setTasks] = useState<Task[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const now = useTick(1000);

  // Keep the current filters in a ref so the SSE handler can apply them without re-subscribing.
  const filters = useRef({ state: stateFilter, pr: prFilter });
  filters.current = { state: stateFilter, pr: prFilter };

  const matches = useCallback((t: Task) => {
    const f = filters.current;
    if (f.state && t.state !== f.state) return false;
    if (f.pr && String(t.pr_number) !== f.pr) return false;
    return true;
  }, []);

  const refresh = useCallback(async () => {
    try {
      const rows = await listTasks({ limit: LIMIT, state: stateFilter || undefined, pr: prFilter || undefined });
      setTasks(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [stateFilter, prFilter]);

  const refreshStats = useCallback(async () => {
    try {
      setStats(await getStats());
    } catch {
      /* stats strip is best-effort */
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refreshStats();
    const id = window.setInterval(() => void refreshStats(), 10_000);
    return () => window.clearInterval(id);
  }, [refreshStats]);

  const upsert = useCallback(
    (t: Task) => {
      setTasks((prev) => {
        const idx = prev.findIndex((x) => x.id === t.id);
        if (!matches(t)) return idx >= 0 ? prev.filter((x) => x.id !== t.id) : prev;
        const next = idx >= 0 ? prev.map((x) => (x.id === t.id ? t : x)) : [t, ...prev];
        next.sort(sortNewest);
        return next.length > LIMIT ? next.slice(0, LIMIT) : next;
      });
    },
    [matches],
  );

  const statsTimer = useRef<number | null>(null);
  const bumpStats = useCallback(() => {
    if (statsTimer.current) return;
    statsTimer.current = window.setTimeout(() => {
      statsTimer.current = null;
      void refreshStats();
    }, 400);
  }, [refreshStats]);

  const live = useEvents(
    useCallback(
      (ev: ServerEvent) => {
        if (ev.type === "task.created" || ev.type === "task.updated") {
          upsert(ev.data);
          bumpStats();
        } else if (ev.type === "task.queued") {
          getTask(ev.data.id)
            .then((t) => upsert(t))
            .catch(() => void refresh());
          bumpStats();
        } else if (ev.type === "finding.feedback") {
          // No list-level change; findings_count is unaffected by votes.
        }
      },
      [upsert, bumpStats, refresh],
    ),
  );

  // When SSE comes back after a gap, resync in case we missed events.
  const prevLive = useRef(live);
  useEffect(() => {
    if (prevLive.current !== "live" && live === "live" && !loading) {
      void refresh();
      void refreshStats();
    }
    prevLive.current = live;
  }, [live, loading, refresh, refreshStats]);

  const setState = (s: TaskState | "") => {
    const p = new URLSearchParams(params);
    if (s) p.set("state", s);
    else p.delete("state");
    setParams(p, { replace: true });
  };

  const applyPr = (v: string) => {
    const p = new URLSearchParams(params);
    const clean = v.trim().replace(/^#/, "");
    if (clean && /^\d+$/.test(clean)) p.set("pr", clean);
    else p.delete("pr");
    setParams(p, { replace: true });
  };

  const counts = stats?.tasks ?? {};
  const totalCount = useMemo(() => Object.values(counts).reduce((a, b) => a + b, 0), [counts]);

  return (
    <div className="space-y-3">
      {/* Stats strip */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded border border-line bg-panel/60 px-3 h-[36px] text-[12px]">
        <Stat label="pending" value={stats?.scheduler.pending} />
        <Stat
          label="busy"
          value={stats ? `${stats.scheduler.busy} / ${stats.scheduler.cap}` : undefined}
          hint={stats ? `${Object.keys(stats.scheduler.busy_tasks).length} runner(s) assigned` : undefined}
        />
        <span className="h-4 w-px bg-line" />
        <div className="flex items-center gap-3">
          {TASK_STATES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setState(stateFilter === s ? "" : s)}
              className={`inline-flex items-center gap-1.5 hover:text-fg ${stateFilter === s ? "text-fg" : "text-mute"}`}
              title={`filter: ${s}`}
            >
              <span className={`inline-block h-2 w-2 rounded-full ${STATE_DOT[s]}`} />
              <span className={s === "superseded" ? "line-through" : ""}>{s}</span>
              <span className="font-mono tabular-nums text-fg/80">{counts[s] ?? 0}</span>
            </button>
          ))}
          <span className="text-mute">
            total <span className="font-mono tabular-nums text-fg/80">{totalCount}</span>
          </span>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <LiveDot status={live} />
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <Chip active={stateFilter === ""} onClick={() => setState("")}>
            all
          </Chip>
          {TASK_STATES.map((s) => (
            <Chip key={s} active={stateFilter === s} onClick={() => setState(stateFilter === s ? "" : s)} dot={STATE_DOT[s]}>
              {s}
            </Chip>
          ))}
        </div>
        <form
          className="ml-auto flex items-center gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            applyPr(prInput);
          }}
        >
          <label className="text-[12px] text-mute" htmlFor="pr-filter">
            PR
          </label>
          <input
            id="pr-filter"
            inputMode="numeric"
            placeholder="#"
            value={prInput}
            onChange={(e) => setPrInput(e.target.value)}
            onBlur={() => applyPr(prInput)}
            className="h-[28px] w-[88px] rounded border border-line bg-bg px-2 font-mono text-[12px] focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          {prFilter && (
            <button
              type="button"
              onClick={() => {
                setPrInput("");
                applyPr("");
              }}
              className="h-[28px] px-2 rounded text-[12px] text-mute hover:text-fg hover:bg-panel"
            >
              clear
            </button>
          )}
        </form>
      </div>

      {error && (
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="rounded border border-line overflow-hidden">
        <table className="w-full table-fixed text-[13px]">
          <colgroup>
            <col className="w-[150px]" />
            <col className="w-[104px]" />
            <col className="w-[84px]" />
            <col className="w-[70px]" />
            <col />
            <col className="w-[44px]" />
            <col className="w-[150px]" />
            <col className="w-[64px]" />
            <col className="w-[60px]" />
          </colgroup>
          <thead className="bg-panel text-[11px] uppercase tracking-wide text-mute">
            <tr className="h-[30px] border-b border-line text-left">
              <th className="px-3 font-medium">PR</th>
              <th className="px-2 font-medium">State</th>
              <th className="px-2 font-medium">Config</th>
              <th className="px-2 font-medium text-right">Lines</th>
              <th className="px-2 font-medium">Phases</th>
              <th className="px-2 font-medium text-center" title="cold / warm runner">
                Pod
              </th>
              <th className="px-2 font-medium">Verdict</th>
              <th className="px-2 font-medium text-right">Find.</th>
              <th className="px-3 font-medium text-right">Age</th>
            </tr>
          </thead>
          <tbody>
            {loading && tasks.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-mute text-[12px]">
                  loading…
                </td>
              </tr>
            )}
            {!loading && tasks.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-center text-mute text-[12px]">
                  no tasks{stateFilter || prFilter ? " match the current filter" : " yet"}
                </td>
              </tr>
            )}
            {tasks.map((t) => {
              const total = taskTotalSeconds(t, now);
              return (
                <tr
                  key={t.id}
                  onClick={() => nav(`/tasks/${t.id}`)}
                  className={`h-[36px] border-b border-line last:border-b-0 cursor-pointer hover:bg-panel/70 ${
                    t.state === "superseded" ? "opacity-60" : ""
                  }`}
                >
                  <td className="px-3 truncate">
                    <a
                      href={prUrl(t)}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      className="text-accent hover:underline font-medium"
                      title={`${t.repo}#${t.pr_number} @ ${t.head_sha}`}
                    >
                      #{t.pr_number}
                    </a>
                    <span className="ml-1.5 font-mono text-[11px] text-mute">{shortSha(t.head_sha)}</span>
                    {t.action === "manual" && <span className="ml-1.5 text-[10px] text-mute">rerun</span>}
                  </td>
                  <td className="px-2">
                    <StatePill state={t.state} />
                  </td>
                  <td className="px-2">
                    <ConfigPill config={t.config} />
                  </td>
                  <td className="px-2 text-right font-mono text-[12px] tabular-nums text-fg/80">{fmtInt(t.priority)}</td>
                  <td className="px-2">
                    <PhaseBar timings={t.timings} total={total} />
                  </td>
                  <td className="px-2 text-center">
                    <ColdDot cold={t.cold} />
                  </td>
                  <td className="px-2">
                    <VerdictPill verdict={t.verdict} />
                  </td>
                  <td className="px-2 text-right font-mono text-[12px] tabular-nums">
                    {t.findings_count > 0 ? t.findings_count : <span className="text-mute">0</span>}
                  </td>
                  <td className="px-3 text-right font-mono text-[12px] tabular-nums text-mute" title={t.created_at}>
                    {relTime(t.created_at, now)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-4 text-[11px] text-mute px-1">
        <span className="inline-flex items-center gap-1.5">
          <ColdDot cold={false} /> warm pod
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ColdDot cold /> cold pod (pool was empty)
        </span>
        <span className="ml-auto">
          showing {tasks.length}
          {tasks.length >= LIMIT ? ` (latest ${LIMIT})` : ""}
        </span>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: number | string | undefined; hint?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5" title={hint}>
      <span className="text-mute">{label}</span>
      <span className="font-mono tabular-nums text-[13px] font-medium">{value ?? "—"}</span>
    </span>
  );
}

function Chip({
  active,
  onClick,
  children,
  dot,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  dot?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-[26px] px-2 rounded-full text-[12px] inline-flex items-center gap-1.5 border transition-colors ${
        active ? "border-accent/60 bg-accent/10 text-accent" : "border-line text-fg/70 hover:bg-panel hover:text-fg"
      }`}
    >
      {dot && <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} />}
      {children}
    </button>
  );
}
