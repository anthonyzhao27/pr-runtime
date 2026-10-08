export type TaskState = "queued" | "running" | "reviewing" | "posted" | "failed" | "superseded";
export const TASK_STATES: TaskState[] = ["queued", "running", "reviewing", "posted", "failed", "superseded"];

export type Verdict = "APPROVE" | "REQUEST_CHANGES";
export type ReviewConfig = "full" | "diff_only";
export type Severity = "blocker" | "major" | "minor" | "nit";

export interface Task {
  id: string;
  repo: string;
  pr_number: number;
  head_sha: string;
  base_sha: string;
  action: string;
  config: string;
  state: TaskState;
  priority: number;
  runner_pod: string | null;
  attempts: number;
  created_at: string;
  admitted_at: string | null;
  result_at: string | null;
  posted_at: string | null;
  timings: Record<string, number>;
  pytest_rc: number | null;
  ruff_rc: number | null;
  verdict: Verdict | null;
  summary: string | null;
  review_url: string | null;
  reviewer_model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tool_calls: number;
  error: string | null;
  cold: boolean;
  touched_files: string[];
  findings_count: number;
  cost_compute_usd: number | null; // runner pod seconds × node $/hr × CPU share
  cost_tokens_usd: number | null;  // LLM tokens at list price
}

export interface FeedbackVote {
  id: string;
  vote: 1 | -1;
  note: string | null;
  created_at: string;
}

export interface Finding {
  id: string;
  task_id: string;
  path: string;
  line: number | null;
  severity: Severity;
  claim: string;
  evidence: string | null;
  posted: boolean;
  feedback: FeedbackVote[];
}

export interface TaskFull extends Task {
  diff: string | null;
  pytest_output: string | null;
  ruff_output: string | null;
  findings: Finding[];
}

export interface Stats {
  scheduler: {
    pending: number;
    busy: number;
    cap: number;
    busy_tasks: Record<string, string>;
    pool_standing_usd_per_hour: number;
    prices: { input_per_m: number; output_per_m: number; node_usd_per_hour: number };
  };
  tasks: Record<string, number>;
}

export type ServerEvent =
  | { type: "task.created" | "task.updated"; data: Task }
  | { type: "task.queued"; data: { id: string; priority: number } }
  | { type: "finding.feedback"; data: Finding };

export interface EvalConfigMetrics {
  recall_strict: number;
  recall_semantic: number;
  fp_rate: number;
  n_bugs: number;
  n_clean: number;
  p50_total_s: number;
}

export interface EvalSummary {
  run_id: string;
  configs: Record<string, EvalConfigMetrics>;
}
