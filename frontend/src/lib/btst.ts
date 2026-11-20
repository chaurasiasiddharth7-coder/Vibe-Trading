/** Types and pure helpers for the Overnight (BTST) page. No React, no network: easy to test. */

export type ExitReason = "target" | "gap_target" | "stop" | "gap_stop" | "time_exit";

export interface BtstWarning {
  key: string;
  label: string;
  detail: string;
  big_loss_flagged_pct: number;
  big_loss_clean_pct: number;
}

export interface BtstOutcome {
  exit?: number;
  reason?: ExitReason;
  method?: "daily" | "intraday";
  ambiguous?: boolean;
  alt_net_pct?: number;
  gross_pct?: number;
  net_pct?: number;
  hit?: boolean;
  data_check?: boolean;
  note?: string;
}

export interface BtstCandidate {
  symbol: string;
  rank: number;
  gain_pct: number | null;
  last: number | null;
  prev_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  traded_cr: number | null;
  turn_x: number | null;
  ret5_pct: number | null;
  ret20_pct: number | null;
  from_high_pct: number | null;
  vol20_pct: number | null;
  gap_pct: number | null;
  off_high_pct: number | null;
  warnings: BtstWarning[];
  chosen: boolean;
  outcome?: BtstOutcome | null;
  outcome_no_stop?: BtstOutcome | null;
}

export interface BtstPick {
  symbol: string;
  entry: number;
  target: number;
  stop: number | null;
  gain_pct: number | null;
  outcome?: BtstOutcome | null;
  outcome_no_stop?: BtstOutcome | null;
}

export interface BtstRecord {
  date: string;
  kind: "live" | "replay";
  rules_version: string;
  rules: { target_pct: number; stop_pct: number; use_stop: boolean; cost_pct: number; candidates: number; scan_time: string; min_traded_cr: number; min_price: number } & Record<string, unknown>;
  status: "picked" | "skipped" | "settled";
  scanned_at?: string;
  history_to?: string | null;
  market: { breadth_pct: number | null; liquid: number };
  coverage: { sources?: string[]; quotes?: number; universe?: number; covered?: number; notes?: string[]; note?: string };
  left_out: Record<string, number>;
  candidates: BtstCandidate[];
  pick: BtstPick | null;
  skip_reason: string | null;
  settled_on?: string;
  notes?: string[];
  runs: Array<{ kind: string; at: string; source: string }>;
}

export interface BtstDayRow {
  date: string;
  kind: "live" | "replay";
  status: string;
  symbol: string | null;
  gain_pct: number | null;
  entry: number | null;
  net_pct: number | null;
  reason: ExitReason | null;
  ambiguous: boolean;
  candidates: number;
  skip_reason: string | null;
  breadth_pct: number | null;
}

export interface BtstSummary {
  trades: number;
  hit_rate?: number | null;
  win_rate?: number;
  avg_net_pct?: number;
  median_net_pct?: number;
  big_loss_rate?: number;
  worst_pct?: number;
  best_pct?: number;
  total_pct?: number;
}

export interface BtstStats {
  kind: "live" | "replay";
  days: number;
  settled_days: number;
  skipped_days: number;
  picks: BtstSummary;
  picks_target_first: BtstSummary;
  picks_no_stop: BtstSummary;
  ambiguous: number;
  exact: number;
  by_year: Array<{ year: string; avg_target_first_pct?: number; avg_no_stop_pct?: number | null } & BtstSummary>;
  first: string | null;
  last: string | null;
  candidates: {
    settled: number;
    by_warning_count: Array<{ warnings: number; label: string } & BtstSummary>;
    by_warning: Array<{ key: string; label: string; flagged: BtstSummary; clean: BtstSummary }>;
  };
}

export interface BtstSchedule {
  enabled: boolean;
  thread_alive: boolean;
  scan_time: string;
  settle_time: string;
  timezone: string;
  last: { scan?: { date: string; at: string; ok: boolean; note: string } | null; settle?: { date: string; at: string; ok: boolean; note: string } | null };
  how_to_enable: string | null;
  cron: string;
}

export interface BtstJob {
  name: string;
  finished: boolean;
  done: number;
  total: number;
  error: string | null;
  result: unknown;
}

export const REASON_TEXT: Record<ExitReason, string> = {
  target: "Take-profit filled",
  gap_target: "Opened above take-profit",
  stop: "Safety stop hit",
  gap_stop: "Opened below the stop",
  time_exit: "Sold near the close",
};

/** Live trades needed before the page stops saying "too early to trust". */
export const TRUST_AFTER = 30;

const MINUS = "−";

export function signedPct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? MINUS : "";
  return `${sign}${Math.abs(n).toFixed(digits)}%`;
}

export function inr(n: number, digits = 0): string {
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function signedInr(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : n < 0 ? MINUS : "";
  return `${sign}${inr(Math.abs(Math.round(n)))}`;
}

export const tone = (n: number | null | undefined): "good" | "bad" | undefined =>
  n === null || n === undefined ? undefined : n > 0 ? "good" : n < 0 ? "bad" : undefined;

/** Whole shares a capital buys at a price (never fewer than zero). */
export function qtyFor(capital: number, price: number): number {
  if (!(capital > 0) || !(price > 0)) return 0;
  return Math.floor(capital / price);
}

/** Rupees made or lost on ``qty`` shares for a net % result (costs are already inside the %). */
export function rupeesFor(netPct: number | null | undefined, qty: number, entry: number): number | null {
  if (netPct === null || netPct === undefined || !(qty > 0) || !(entry > 0)) return null;
  return (netPct / 100) * qty * entry;
}

/** What to type into the broker app: one CNC buy now, then one GTT OCO for tomorrow. */
export function orderSteps(pick: BtstPick, qty: number): string[] {
  const steps = [`Buy ${qty} ${pick.symbol} now (delivery / CNC), around ₹${pick.entry.toFixed(2)}.`];
  if (pick.stop !== null) {
    steps.push(`Place a GTT OCO for tomorrow: sell at ₹${pick.target.toFixed(2)} (take-profit) or ₹${pick.stop.toFixed(2)} (safety stop).`);
  } else {
    steps.push(`Place a GTT sell for tomorrow at ₹${pick.target.toFixed(2)} (take-profit).`);
  }
  steps.push("If neither fills by 15:20 tomorrow, sell at market and cancel the GTT.");
  return steps;
}

export function outcomeText(o: BtstOutcome | null | undefined): string {
  if (!o) return "Waiting for tomorrow's prices";
  if (o.data_check) return o.note ?? "Left out of the record";
  const how = o.reason ? REASON_TEXT[o.reason] : "Settled";
  const at = o.exit !== undefined ? ` at ₹${o.exit.toFixed(2)}` : "";
  return `${how}${at}`;
}

export function pctRate(r: number | null | undefined): string {
  return r === null || r === undefined ? "—" : `${Math.round(r * 100)}%`;
}

/** One honest sentence about how much to trust the live record so far. */
export function trustSentence(live: BtstStats | null, replay: BtstStats | null): string {
  const n = live?.picks.trades ?? 0;
  if (n === 0) {
    return replay && replay.picks.trades > 0
      ? `No live trades yet. On stored prices since ${replay.first?.slice(0, 4)} the rules made ${signedPct(replay.picks_no_stop.avg_net_pct)} a trade without the stop; treat that as a hypothesis until live days agree.`
      : "No live trades yet, and no replay. Build the replay from stored prices to see how the rules did in the past.";
  }
  if (n < TRUST_AFTER) {
    return `Too early to trust: ${n} of ${TRUST_AFTER} live trades so far (${signedPct(live?.picks.avg_net_pct)} a trade after costs).`;
  }
  const avg = live?.picks.avg_net_pct ?? 0;
  return avg > 0
    ? `${n} live trades: ${signedPct(avg)} a trade after costs. Small edge; one bad week can erase a month.`
    : `${n} live trades: ${signedPct(avg)} a trade after costs. The rules are losing money live; pause and review.`;
}

/** "Scanned 15:20 · 812 liquid stocks, 54% up · prices: nse" */
export function scanLine(rec: BtstRecord | null): string {
  if (!rec) return "";
  const parts: string[] = [];
  if (rec.scanned_at) parts.push(`Scanned ${rec.scanned_at}`);
  if (rec.market.liquid) parts.push(`${rec.market.liquid} liquid stocks${rec.market.breadth_pct !== null ? `, ${Math.round(rec.market.breadth_pct)}% up` : ""}`);
  if (rec.coverage.sources?.length) parts.push(`prices: ${rec.coverage.sources.join(" + ")}`);
  if (rec.coverage.universe) parts.push(`covered ${rec.coverage.covered ?? 0} of ${rec.coverage.universe}`);
  return parts.join(" · ");
}

const CAPITAL_KEY = "btst.capital";
export const DEFAULT_CAPITAL = 100_000;

export function loadCapital(): number {
  try {
    const v = Number(localStorage.getItem(CAPITAL_KEY));
    return v > 0 ? v : DEFAULT_CAPITAL;
  } catch {
    return DEFAULT_CAPITAL;
  }
}

export function saveCapital(v: number): void {
  try {
    localStorage.setItem(CAPITAL_KEY, String(v));
  } catch {
    // storage blocked: the value lives for this visit only
  }
}
