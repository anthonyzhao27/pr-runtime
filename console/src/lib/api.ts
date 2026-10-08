import type { EvalSummary, Finding, Stats, Task, TaskFull } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, `${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

export interface TaskQuery {
  limit?: number;
  state?: string;
  pr?: number | string;
}

export function listTasks(q: TaskQuery = {}): Promise<Task[]> {
  const p = new URLSearchParams();
  p.set("limit", String(q.limit ?? 100));
  if (q.state) p.set("state", q.state);
  if (q.pr !== undefined && q.pr !== "") p.set("pr", String(q.pr));
  return request<Task[]>(`/api/tasks?${p.toString()}`);
}

export function getTask(id: string): Promise<TaskFull> {
  return request<TaskFull>(`/api/tasks/${encodeURIComponent(id)}`);
}

export function rerunTask(id: string, config: string): Promise<{ id: string }> {
  return request<{ id: string }>(`/api/tasks/${encodeURIComponent(id)}/rerun`, {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

export function postFeedback(findingId: string, vote: 1 | -1, note?: string): Promise<Finding> {
  const body: { vote: 1 | -1; note?: string } = { vote };
  if (note && note.trim()) body.note = note.trim();
  return request<Finding>(`/api/findings/${encodeURIComponent(findingId)}/feedback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getStats(): Promise<Stats> {
  return request<Stats>("/api/stats");
}

/**
 * Eval summary. The controller's SPA catch-all answers unknown paths with
 * index.html (200, text/html) rather than a 404, so treat anything that is
 * not parseable JSON with a `configs` object as "no results yet".
 */
export async function getEvalSummary(): Promise<EvalSummary | null> {
  const res = await fetch("/eval/results/latest/summary.json", { headers: { Accept: "application/json" } });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText}`);
  const text = await res.text();
  try {
    const parsed = JSON.parse(text) as unknown;
    if (parsed && typeof parsed === "object" && "configs" in parsed) return parsed as EvalSummary;
    return null;
  } catch {
    return null;
  }
}
