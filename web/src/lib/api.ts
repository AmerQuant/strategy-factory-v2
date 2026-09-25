import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && path !== "/login") window.dispatchEvent(new Event("sf-unauthorized"));
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : res.statusText;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  del: (p: string) => request<void>("DELETE", p),
};

export function useApi<T>(path: string | null, opts: { refetchInterval?: number } = {}) {
  return useQuery<T, ApiError>({
    queryKey: [path],
    queryFn: () => api.get<T>(path as string),
    enabled: path !== null,
    refetchInterval: opts.refetchInterval,
  });
}

export function useSave<T>(invalidate: string[]) {
  const qc = useQueryClient();
  return useMutation<T, ApiError, { method: "post" | "put" | "del"; path: string; body?: unknown }>({
    mutationFn: ({ method, path, body }) =>
      method === "del" ? (api.del(path) as Promise<T>) : method === "put" ? api.put<T>(path, body) : api.post<T>(path, body),
    onSuccess: () => invalidate.forEach((k) => qc.invalidateQueries({ queryKey: [k] })),
  });
}

// ---- API shapes (the subset the UI uses) ----
export interface Health { ok: boolean; api_version: string; auth_required: boolean; authenticated: boolean }
export interface MethodMeta {
  method: string; family: string; grid: number[]; default: number; needs_context: string | null;
  neutral_exit: Record<string, unknown>; exit_library: Record<string, unknown>[];
}
export interface Meta {
  methods: MethodMeta[]; families: string[]; rungs: string[]; activation_modes: string[];
  activation_defaults: Record<string, unknown>; row_defaults: Record<string, unknown>;
  catalogues: Record<string, { version: string; methods: string[] }>; collections: string[];
}
export interface RunSummary {
  id: string; generated_at: string | null; data: string | null; catalog: string | null; timeframe: string | null;
  rows: number; accepted: number; trials: number | null; combined_sharpe: number | null; effective_n: number | null;
  holdout: string | null;
}
export interface EvidenceRow {
  row: string; rung: string; n: number; sharpe: number; max_dd: number; expectancy: number; p: number; bh: boolean;
  dsr: number; path: string | null; family: string; robust?: boolean; edge_source?: string; horizon?: string;
  direction?: string;
}
export interface Evidence {
  generated_at: string; meta: Record<string, unknown>; trials_in_registry: number; rows: EvidenceRow[];
  combined_dev: { sharpe: number; effective_n: number; weights_per_fold?: Record<string, number>[];
    leverage_per_fold?: number[]; risk_budget?: Record<string, number | null> | null };
  benchmark_all_rows_equal_dev: number; effective_n_all_rows: number;
  dev_curve: { dates: string[]; combined: number[]; benchmark: number[] } | null;
  robustness_of_accepted_rows: Record<string, { mandatory: Record<string, boolean>; warnings: Record<string, boolean>;
    delay_keep: number; mc_dd_p95: number; "cost_x1.5_expectancy": number }> | null;
  spa_any_vs_cash?: { p_value: number } | null; spa_combined_vs_all_equal?: { p_value: number } | null;
  family_ensembles?: Record<string, { sharpe: number; best_member: string; best_member_sharpe: number; ensemble_better: boolean }>;
  holdout?: Record<string, unknown> | null; row_analysis_summary?: Record<string, { persistent: string | null;
    edge_accepted: string[]; sizing_accepted: string[] }>; has_report?: boolean;
}
export interface LiveSummary {
  id: string; last_day: string | null; last_dp: string | null; rows: number; open_positions: number;
  closed_trades: number; net_pnl: number; reconciled: boolean; pending: number;
}
export interface LiveDetail {
  id: string; last_day: string | null; last_dp: string | null;
  book: { row: string; method: string; direction: number; threshold: number; exit: string; eligible: number; filters: string[] }[];
  positions: { row: string; symbol: string; qty: number; entry_date: string; entry_px: number; signal_date: string; dividends: number }[];
  pending: { row: string; symbol: string; qty: number; intent: string }[];
  closed: { row: string; symbol: string; entry_date: string; exit_date: string; net_pnl: number; entry_px: number; exit_px: number }[];
  equity: { date: string; equity: number }[];
  by_row: { row: string; trades: number; net_pnl: number; wins: number }[];
  log: { day: string; reconciled: boolean | null; orders: number; open_positions: number }[];
}
export interface Job {
  id: string; kind: string; preset: string; cmd: string[]; status: string; started_at: string;
  finished_at?: string; returncode: number | null; log?: string[];
}
export interface ScheduleRun {
  id: string; schedule: string; name: string; due: string; started_at: string; finished_at?: string; status: string;
  note: string; steps: { preset: string; job: string; status: string }[];
}
export interface SchedulerSummary {
  heartbeat: { at: string; pid: number; schedules: number } | null; age_seconds: number | null; alive: boolean;
  schedules: { id: string; name: string; enabled: boolean; steps: string[]; next: string | null; error: string | null;
    last: ScheduleRun | null }[];
  runs: ScheduleRun[];
}
export interface Limits { capital: number; max_drawdown: number | null; max_daily_loss: number | null; max_recon_failures: number | null }
export interface KillSwitch { active: boolean; mode: "halt_new" | "flatten"; reason: string; at: string | null; by: string; limits: Limits }
export interface PlatformEvent { at: string; level: "critical" | "warning" | "info"; source: string; message: string }
