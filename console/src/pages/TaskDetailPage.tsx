import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, getTask, postFeedback, rerunTask } from "../lib/api";
import { absTime, fileUrl, fmtInt, fmtSeconds, fmtTokens, prUrl, shortSha, taskTotalSeconds } from "../lib/format";
import type { Finding, ReviewConfig, ServerEvent, TaskFull } from "../lib/types";
import { useEvents, useTick } from "../lib/useEvents";
import { ColdDot, ConfigPill, SeverityPill, StatePill, VerdictPill } from "../components/Pill";
import { PhaseBar } from "../components/PhaseBar";
import { DiffView } from "../components/DiffView";
import { OutputBlock } from "../components/OutputBlock";
import { LiveDot } from "../components/LiveDot";

type Tab = "findings" | "diff" | "pytest" | "ruff";

export function TaskDetailPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [task, setTask] = useState<TaskFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("findings");
  const [rerunConfig, setRerunConfig] = useState<ReviewConfig>("full");
  const [rerunning, setRerunning] = useState(false);
  const now = useTick(1000);

  const load = useCallback(async () => {
    try {
      const t = await getTask(id);
      setTask(t);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? `task ${id} not found` : e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    setTask(null);
    void load();
  }, [load]);

  const live = useEvents(
    useCallback(
      (ev: ServerEvent) => {
        if ((ev.type === "task.updated" || ev.type === "task.created") && ev.data.id === id) {
          // Task events carry the slim Task; refetch for diff/outputs/findings.
          void load();
        } else if (ev.type === "task.queued" && ev.data.id === id) {
          void load();
        } else if (ev.type === "finding.feedback" && ev.data.task_id === id) {
          const f = ev.data;
          setTask((prev) => (prev ? { ...prev, findings: prev.findings.map((x) => (x.id === f.id ? f : x)) } : prev));
        }
      },
      [id, load],
    ),
  );

  useEffect(() => {
    // Default to a more useful tab when there are no findings yet.
    if (task && task.findings.length === 0 && tab === "findings" && task.diff) {
      // keep findings tab; it explains itself. (no-op, but leaves room for a preference later)
    }
  }, [task, tab]);

  const onRerun = async () => {
    if (!task || rerunning) return;
    setRerunning(true);
    try {
      const r = await rerunTask(task.id, rerunConfig);
      nav(`/tasks/${r.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRerunning(false);
    }
  };

  const onFeedback = (updated: Finding) => {
    setTask((prev) => (prev ? { ...prev, findings: prev.findings.map((x) => (x.id === updated.id ? updated : x)) } : prev));
  };

  if (error && !task) {
    return (
      <div className="space-y-3">
        <Crumb id={id} />
        <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">{error}</div>
      </div>
    );
  }
  if (!task) {
    return (
      <div className="space-y-3">
        <Crumb id={id} />
        <div className="text-[12px] text-mute">loading…</div>
      </div>
    );
  }

  const inflight = task.state === "running" || task.state === "reviewing";
  const total = taskTotalSeconds(task, now);
  const tabs: { key: Tab; label: string; count?: number | string }[] = [
    { key: "findings", label: "Findings", count: task.findings.length },
    { key: "diff", label: "Diff", count: task.touched_files.length ? `${task.touched_files.length} files` : undefined },
    { key: "pytest", label: "pytest", count: task.pytest_rc === null ? undefined : `rc ${task.pytest_rc}` },
    { key: "ruff", label: "ruff", count: task.ruff_rc === null ? undefined : `rc ${task.ruff_rc}` },
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Crumb id={id} />
        <div className="ml-auto">
          <LiveDot status={live} />
        </div>
      </div>

      {/* Header */}
      <div className="rounded border border-line bg-panel/60 px-4 py-3 space-y-2.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <a href={prUrl(task)} target="_blank" rel="noreferrer" className="text-[16px] font-semibold text-accent hover:underline">
            {task.repo}#{task.pr_number}
          </a>
          <span className="font-mono text-[12px] text-mute" title={`head ${task.head_sha} · base ${task.base_sha}`}>
            {shortSha(task.base_sha)}
            <span className="mx-1 text-mute/60">→</span>
            {shortSha(task.head_sha)}
          </span>
          <StatePill state={task.state} />
          <VerdictPill verdict={task.verdict} />
          <ConfigPill config={task.config} />
          <span className="inline-flex items-center gap-1.5 text-[11px] text-mute">
            <ColdDot cold={task.cold} /> {task.cold ? "cold" : "warm"}
          </span>
          <span className="text-[11px] text-mute">
            {task.action} · attempt {task.attempts}
            {task.runner_pod && (
              <>
                {" "}
                · <span className="font-mono">{task.runner_pod}</span>
              </>
            )}
          </span>

          <div className="ml-auto flex items-center gap-2">
            {task.review_url && (
              <a
                href={task.review_url}
                target="_blank"
                rel="noreferrer"
                className="h-[28px] inline-flex items-center px-2.5 rounded border border-line text-[12px] hover:bg-panel"
              >
                view review ↗
              </a>
            )}
            <select
              value={rerunConfig}
              onChange={(e) => setRerunConfig(e.target.value as ReviewConfig)}
              className="h-[28px] rounded border border-line bg-bg px-2 font-mono text-[12px] focus:outline-none focus:ring-2 focus:ring-accent/40"
              aria-label="re-run config"
            >
              <option value="full">full</option>
              <option value="diff_only">diff_only</option>
            </select>
            <button
              type="button"
              onClick={() => void onRerun()}
              disabled={rerunning}
              className="h-[28px] px-3 rounded bg-accent text-white text-[12px] font-medium hover:bg-accent/90 disabled:opacity-50"
            >
              {rerunning ? "re-running…" : "Re-run"}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-x-5 gap-y-1 text-[12px]">
          <Meta label="model" value={task.reviewer_model ?? "—"} mono />
          <Meta label="tokens" value={`${fmtTokens(task.tokens_in)} in / ${fmtTokens(task.tokens_out)} out`} mono />
          <Meta label="tool calls" value={fmtInt(task.tool_calls)} mono />
          <Meta label="changed lines" value={fmtInt(task.priority)} mono />
          <Meta label="created" value={absTime(task.created_at)} mono />
          <Meta label="admitted" value={absTime(task.admitted_at)} mono />
          <Meta label="result" value={absTime(task.result_at)} mono />
          <Meta label="posted" value={absTime(task.posted_at)} mono />
        </div>

        <PhaseBar timings={task.timings} total={inflight ? total : undefined} height={10} showLabels />
        {typeof task.timings.runner_total === "number" && (
          <div className="text-[11px] text-mute">
            runner wall clock <span className="font-mono text-fg/80">{fmtSeconds(task.timings.runner_total)}</span>
            {typeof task.timings.total === "number" && (
              <>
                {" "}
                · end-to-end <span className="font-mono text-fg/80">{fmtSeconds(task.timings.total)}</span>
              </>
            )}
          </div>
        )}

        {task.summary && <p className="text-[13px] leading-snug text-fg/90 max-w-[1100px]">{task.summary}</p>}
        {task.error && (
          <pre className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 font-mono text-[12px] text-red-700 dark:text-red-300 whitespace-pre-wrap">
            {task.error}
          </pre>
        )}
        {error && <div className="text-[12px] text-red-600 dark:text-red-400">{error}</div>}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`h-[34px] px-3 -mb-px border-b-2 text-[13px] inline-flex items-center gap-1.5 ${
              tab === t.key ? "border-accent text-fg font-medium" : "border-transparent text-mute hover:text-fg"
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className="font-mono text-[11px] rounded bg-panel px-1.5 py-0.5 text-mute ring-1 ring-inset ring-line">{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "findings" && <FindingsList task={task} onFeedback={onFeedback} />}
      {tab === "diff" &&
        (task.diff ? (
          <DiffView diff={task.diff} />
        ) : (
          <Empty>{inflight || task.state === "queued" ? "diff not available yet" : "no diff recorded"}</Empty>
        ))}
      {tab === "pytest" && <OutputBlock title="pytest" output={task.pytest_output} rc={task.pytest_rc} />}
      {tab === "ruff" && <OutputBlock title="ruff" output={task.ruff_output} rc={task.ruff_rc} />}
    </div>
  );
}

function Crumb({ id }: { id: string }) {
  return (
    <div className="text-[12px] text-mute">
      <Link to="/" className="hover:text-fg hover:underline">
        Tasks
      </Link>
      <span className="mx-1.5">/</span>
      <span className="font-mono text-fg/80">{id}</span>
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="text-mute">{label}</span>
      <span className={`${mono ? "font-mono text-[12px]" : ""} text-fg/90`}>{value}</span>
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded border border-dashed border-line px-3 py-8 text-center text-[12px] text-mute">{children}</div>;
}

const SEV_ORDER: Record<string, number> = { blocker: 0, major: 1, minor: 2, nit: 3 };

function FindingsList({ task, onFeedback }: { task: TaskFull; onFeedback: (f: Finding) => void }) {
  const findings = [...task.findings].sort((a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9));
  if (findings.length === 0) {
    const inflight = task.state === "queued" || task.state === "running" || task.state === "reviewing";
    return <Empty>{inflight ? "review in progress — no findings yet" : task.verdict === "APPROVE" ? "no findings; reviewer approved" : "no findings"}</Empty>;
  }
  return (
    <div className="rounded border border-line divide-y divide-line">
      {findings.map((f) => (
        <FindingRow key={f.id} f={f} repo={task.repo} sha={task.head_sha} onFeedback={onFeedback} />
      ))}
    </div>
  );
}

function FindingRow({ f, repo, sha, onFeedback }: { f: Finding; repo: string; sha: string; onFeedback: (f: Finding) => void }) {
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [busy, setBusy] = useState<1 | -1 | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const ups = f.feedback.filter((v) => v.vote === 1).length;
  const downs = f.feedback.filter((v) => v.vote === -1).length;
  const notes = f.feedback.filter((v) => v.note);

  const vote = async (v: 1 | -1) => {
    if (busy) return;
    setBusy(v);
    setErr(null);
    try {
      const updated = await postFeedback(f.id, v, note);
      onFeedback(updated);
      setNote("");
      setNoteOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="px-4 py-3 flex gap-4">
      <div className="w-[76px] shrink-0 pt-0.5">
        <SeverityPill severity={f.severity} />
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <a
            href={fileUrl(repo, sha, f.path, f.line)}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-[12px] text-accent hover:underline break-all"
          >
            {f.path}
            {f.line !== null && <span className="text-accent/70">:{f.line}</span>}
          </a>
          {!f.posted && (
            <span className="text-[10px] uppercase tracking-wide text-mute ring-1 ring-inset ring-line rounded px-1" title="not posted to GitHub">
              not posted
            </span>
          )}
        </div>
        <p className="text-[13px] leading-snug text-fg">{f.claim}</p>
        {f.evidence && (
          <blockquote className="border-l-2 border-line pl-3 font-mono text-[12px] leading-[18px] text-fg/75 whitespace-pre-wrap break-words">
            {f.evidence}
          </blockquote>
        )}
        {notes.length > 0 && (
          <ul className="space-y-0.5">
            {notes.map((v) => (
              <li key={v.id} className="text-[12px] text-fg/80">
                <span className={`font-mono mr-1.5 ${v.vote === 1 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                  {v.vote === 1 ? "+1" : "−1"}
                </span>
                {v.note}
                <span className="ml-2 text-[11px] text-mute">{absTime(v.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
        {noteOpen && (
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="optional note — recorded with your next vote"
            className="w-full max-w-[640px] h-[28px] rounded border border-line bg-bg px-2 text-[12px] focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
        )}
        {err && <div className="text-[12px] text-red-600 dark:text-red-400">{err}</div>}
      </div>
      <div className="shrink-0 flex items-start gap-1">
        <VoteButton kind={1} count={ups} busy={busy === 1} disabled={busy !== null} onClick={() => void vote(1)} />
        <VoteButton kind={-1} count={downs} busy={busy === -1} disabled={busy !== null} onClick={() => void vote(-1)} />
        <button
          type="button"
          onClick={() => setNoteOpen((o) => !o)}
          className={`h-[28px] px-2 rounded border text-[12px] ${noteOpen ? "border-accent/60 text-accent bg-accent/10" : "border-line text-mute hover:text-fg hover:bg-panel"}`}
          title="add a note to your vote"
        >
          note
        </button>
      </div>
    </div>
  );
}

function VoteButton({ kind, count, busy, disabled, onClick }: { kind: 1 | -1; count: number; busy: boolean; disabled: boolean; onClick: () => void }) {
  const up = kind === 1;
  const active = count > 0;
  const cls = up
    ? active
      ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : "border-line text-mute hover:text-emerald-700 dark:hover:text-emerald-300 hover:border-emerald-500/50"
    : active
      ? "border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300"
      : "border-line text-mute hover:text-red-700 dark:hover:text-red-300 hover:border-red-500/50";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={up ? "thumbs up: finding is correct" : "thumbs down: finding is wrong or unhelpful"}
      className={`h-[28px] min-w-[44px] px-2 rounded border inline-flex items-center justify-center gap-1 text-[12px] disabled:opacity-50 ${cls}`}
    >
      <ThumbIcon up={up} />
      <span className="font-mono tabular-nums">{busy ? "…" : count}</span>
    </button>
  );
}

function ThumbIcon({ up }: { up: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={up ? "" : "rotate-180"}
      aria-hidden
    >
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}
