import { request } from "@/lib/api";

export interface Condition {
  kind: string;
  [param: string]: string | number;
}

export interface Rules {
  side: "LONG" | "SHORT";
  entry: Condition[];
  exit: Partial<Record<"stop_pct" | "target_pct" | "stop_atr" | "target_atr" | "trail_atr" | "max_hold_days", number>>;
  universe: string | null;
  symbols: string[] | null;
}

export interface TrackStats {
  trades: number;
  win_rate?: number;
  avg_pct?: number;
  total_pct?: number;
  worst_pct?: number;
  best_pct?: number;
  max_drawdown_pct?: number;
  profit_factor?: number | null;
  first?: string;
  last?: string;
  open?: number;
  pnl_inr?: number;
  paper_since?: string | null;
}

export interface GateCheck {
  label: string;
  ok: boolean;
  value?: unknown;
  manual?: boolean;
}

export interface TestResult {
  summary: TrackStats;
  gate?: { passed: boolean; checks: GateCheck[] };
  robustness?: Record<string, TrackStats>;
  equity?: Array<{ date: string; cum_pct: number }>;
  by_symbol?: Array<TrackStats & { symbol: string }>;
  trades?: Array<{ symbol: string; entry_date: string; exit_date: string; net_pct: number; reason: string }>;
  trades_sample?: Array<{ symbol: string; entry_date: string; exit_date: string; net_pct: number; reason: string }>;
  ran_at?: string;
  years?: number;
  cost_pct?: number;
  symbols_tested?: number;
  symbols_with_trades?: number;
  /** How a built-in was replayed (what stands in for what). */
  note?: string;
  universe?: string;
  days?: number;
  skipped_days?: number;
  sessions?: number;
}

export interface StrategyRow {
  id: string;
  name: string;
  kind: "built-in" | "lab" | "research" | "exit settings";
  profile: string | null;
  profile_name: string;
  paper: boolean | null;
  live: boolean;
  live_locked: boolean;
  rules?: Rules;
  rules_text?: string[];
  notes?: string;
  source?: string | null;
  author?: string;
  archived?: boolean;
  paper_stats?: TrackStats | null;
  last_test?: Pick<TestResult, "summary" | "gate" | "ran_at" | "years"> | null;
  tests_run?: number;
  /** Built-ins with a past-price replay (signal board, BTST). */
  testable?: boolean;
  gate: GateCheck[];
  last_scan?: { at: string; opened: number } | null;
  status?: string;
}

export interface Catalogue {
  strategies: StrategyRow[];
  live_locked_reason: string;
  conditions: Record<string, { params: Record<string, [number, number, number]>; text: string }>;
  exit_limits: Record<string, [number, number]>;
}

export interface Draft {
  name: string;
  rules: Rules | null;
  problem: string | null;
  unsupported: string[];
  source_url: string | null;
  rules_text: string[];
}

export interface Job {
  id: string;
  status: "running" | "done" | "failed";
  result: (TestResult & { years?: number }) | null;
  error: string | null;
  done?: number;
  total?: number;
}

type Ok<T> = { ok: boolean; data: T };
const json = (body: unknown) => ({ body: JSON.stringify(body) });
const q = (profile: string | null | undefined) => (profile ? `?profile=${encodeURIComponent(profile)}` : "");

export const strategiesApi = {
  list: () => request<Ok<Catalogue>>("/api/desk/strategies"),
  detail: (id: string, profile?: string | null) =>
    request<Ok<{ strategy: unknown; tests: TestResult[]; paper_trades: Array<Record<string, unknown>> }>>(
      `/api/desk/strategies/${encodeURIComponent(id)}${q(profile)}`,
    ),
  create: (body: { name: string; rules: Rules; notes?: string; source_url?: string | null }) =>
    request<Ok<{ id: string }>>("/api/desk/strategies", { method: "POST", ...json(body) }),
  edit: (id: string, body: Partial<{ name: string; notes: string; rules: Rules; archived: boolean }>, profile?: string | null) =>
    request<Ok<unknown>>(`/api/desk/strategies/${encodeURIComponent(id)}${q(profile)}`, { method: "PATCH", ...json(body) }),
  paper: (id: string, on: boolean, profile?: string | null) =>
    request<Ok<{ id: string; paper: boolean }>>(`/api/desk/strategies/${encodeURIComponent(id)}/paper${q(profile)}`, {
      method: "PUT",
      ...json({ on }),
    }),
  test: (id: string, years: number, profile?: string | null, universe?: string, sessions?: number) =>
    request<{ ok: boolean; job_id: string }>(`/api/desk/strategies/${encodeURIComponent(id)}/test${q(profile)}`, {
      method: "POST",
      ...json({ years, ...(universe ? { universe } : {}), ...(sessions ? { sessions } : {}) }),
    }),
  tryRules: (rules: Rules, years: number) =>
    request<{ ok: boolean; job_id: string }>("/api/desk/strategies/test", { method: "POST", ...json({ rules, years }) }),
  job: (jobId: string, profile?: string | null) => request<Ok<Job>>(`/api/desk/strategies/jobs/${encodeURIComponent(jobId)}${q(profile)}`),
  scanNow: (id: string, profile?: string | null) =>
    request<Ok<{ opened: Array<{ symbol: string }>; skipped: Array<{ symbol: string; why: string }>; note?: string }>>(
      `/api/desk/strategies/${encodeURIComponent(id)}/scan-now${q(profile)}`,
      { method: "POST", body: "{}" },
    ),
  importDraft: (body: { text?: string; url?: string }) =>
    request<Ok<Draft>>("/api/desk/strategies/import", { method: "POST", ...json(body) }),
};

/** Poll a backtest job until it ends (every 2s, up to 30 minutes: a first overnight replay rebuilds every day). */
export async function waitForJob(
  jobId: string,
  onTick?: (seconds: number, progress?: { done: number; total: number }) => void,
  signal?: { stop: boolean },
  profile?: string | null,
): Promise<Job> {
  const started = Date.now();
  for (;;) {
    if (signal?.stop) throw new Error("stopped");
    // Tests the owner starts in someone else's profile live in that profile.
    const r = await strategiesApi.job(jobId, profile);
    if (r.data.status !== "running") return r.data;
    if (Date.now() - started > 1_800_000) throw new Error("The test is taking too long. Try fewer years or symbols.");
    onTick?.(Math.round((Date.now() - started) / 1000), r.data.total ? { done: r.data.done ?? 0, total: r.data.total } : undefined);
    await new Promise((res) => setTimeout(res, 2000));
  }
}

export const pct = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;

export const UNIVERSE_OPTIONS = [
  { id: "nifty50", label: "Nifty 50" },
  { id: "niftybank", label: "Nifty Bank" },
  { id: "nifty100", label: "Nifty 100" },
  { id: "core", label: "Original 30" },
  { id: "midcap150", label: "Midcap 150 (first 100)" },
  { id: "smallcap250", label: "Smallcap 250 (first 100)" },
];
