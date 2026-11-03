/** Types and pure helpers for the Intraday day-log page. No React, no network: easy to test. */

export type TradeStatus = "open" | "target" | "stop" | "stop_gap" | "flat";
export type Side = "long" | "short";

export interface IntradayTrade {
  id: string;
  symbol: string;
  side: Side;
  tags: string[];
  decided_at: string;
  entry: number;
  stop: number;
  target: number;
  risk_per_share: number;
  risk_pct: number;
  reward_risk: number;
  qty: number;
  status: TradeStatus;
  exit_time: string | null;
  last_price: number;
  last_time: string;
  r_gross: number;
  r_net: number;
  pnl_inr: number;
  mfe_r: number;
  mae_r: number;
  range: { high: number; low: number };
  volume_x: number;
  /** [bar close in minutes since midnight, R before costs at that close] */
  path: Array<[number, number]>;
}

export interface DaySummary {
  taken: number;
  open: number;
  wins: number;
  losses: number;
  flat: number;
  win_rate: number | null;
  net_r: number;
  gross_r: number;
  net_inr: number;
  best: { symbol: string; pnl_inr: number } | null;
  worst: { symbol: string; pnl_inr: number } | null;
}

export interface IndexDay {
  type: string;
  label: string;
  metrics: Record<string, number>;
}

export interface DayKind {
  type: string;
  label: string;
  rule: string;
  nifty50: IndexDay;
  niftybank?: IndexDay;
}

export interface PlannedSymbol {
  symbol: string;
  price: number;
  traded_value_cr: number;
  setups: string[];
  volume_x: number;
  tags: string[];
}

export interface SkippedTrade {
  symbol: string;
  side: Side;
  decided_at: string;
  reason: string;
}

export interface RunLogEntry {
  kind: string;
  at: string;
  ok: boolean;
  note: string;
  source: string;
}

export interface DayRecord {
  date: string;
  weekday: string;
  status: "empty" | "planned" | "live" | "settled";
  settings: IntradaySettings;
  universe: { key: string; label: string; eligible: number; covered: number; charts_read: number; liquid: number; data_source: string } | null;
  planned: PlannedSymbol[];
  trades: IntradayTrade[];
  skipped: SkippedTrade[];
  not_triggered: string[];
  data_gaps: string[];
  day: DayKind | null;
  summary: DaySummary;
  as_of: string | null;
  runs: RunLogEntry[];
  note: string;
}

export interface DayRow {
  date: string;
  weekday: string;
  status: DayRecord["status"];
  type: string | null;
  label: string | null;
  planned: number;
  taken: number;
  wins: number;
  losses: number;
  flat: number;
  win_rate: number | null;
  net_r: number;
  net_inr: number;
  rules_match: boolean;
}

export interface SeriesPoint {
  date: string;
  trades: number;
  wins: number;
  cum_trades: number;
  cum_wins: number;
  cum_win_rate: number | null;
  cum_net_r: number;
  cum_net_inr: number;
}

export interface GroupStat {
  key: string;
  trades: number;
  wins: number;
  win_rate: number;
  avg_r: number;
  net_inr: number;
  days: number | null;
}

export interface IntradayStats {
  days: number;
  mixed_rules: number;
  series: SeriesPoint[];
  by_type: GroupStat[];
  by_side: GroupStat[];
  by_index: GroupStat[];
}

export interface IntradaySettings {
  universe: string;
  sides: "long" | "short" | "both";
  max_trades: number;
  risk_inr: number;
  or_minutes: number;
  stop: string;
  target: string;
  min_vol_x: number | null;
  market_filter: "off" | "with_nifty";
  max_candidates: number;
  flat_by: string;
  open_time: string;
  close_time: string;
  live_refresh_minutes: number;
}

export interface ScheduleStatus {
  enabled: boolean;
  thread_alive: boolean;
  open_time: string;
  close_time: string;
  live_refresh_minutes: number;
  timezone: string;
  market: "open" | "pre_open" | "closed" | "holiday";
  last: Record<string, { date: string; at: string; ok: boolean; note: string } | null>;
  next: { kind: string; at: string } | null;
  how_to_enable: string | null;
  cron: string;
}

export type RunKind = "open" | "refresh" | "close" | "replay";

// --- search across days -----------------------------------------------------------------------------------

export type Outcome = "won" | "lost" | "flat" | "open";

export interface SearchTrade {
  id: string;
  symbol: string;
  side: Side;
  tags: string[];
  decided_at: string;
  entry: number;
  stop: number;
  target: number;
  qty: number;
  status: TradeStatus;
  exit_time: string | null;
  last_price: number;
  last_time: string;
  r_net: number;
  pnl_inr: number;
  mfe_r: number;
  mae_r: number;
  outcome: Outcome;
}

export interface SearchDay {
  date: string;
  weekday: string;
  status: DayRecord["status"];
  label: string | null;
  type: string | null;
  rules_match: boolean;
  summary: { trades: number; wins: number; losses: number; flat: number; open: number; net_inr: number; net_r: number };
  trades: SearchTrade[];
}

export interface SearchResult {
  days: SearchDay[];
  totals: { trades: number; wins: number; losses: number; flat: number; open: number; net_inr: number; net_r: number; win_rate: number | null; days: number };
  truncated: boolean;
}

export interface SearchFilters {
  from: string;
  to: string;
  symbol: string;
  side: "" | Side;
  outcome: "" | Outcome;
  kind: string;
  index: "" | "nifty50" | "niftybank";
}

export const EMPTY_FILTERS: SearchFilters = { from: "", to: "", symbol: "", side: "", outcome: "", kind: "", index: "" };

export const DAY_KINDS: Array<[string, string]> = [
  ["trend_up", "Trend up"], ["trend_down", "Trend down"], ["range", "Range day"],
  ["reversal", "Reversal day"], ["gap", "Gap day"],
];

/** The query string for a search: empty filters are left out, symbols are trimmed and upper-cased. */
export function searchQuery(f: SearchFilters): string {
  const q = new URLSearchParams();
  if (f.from) q.set("from", f.from);
  if (f.to) q.set("to", f.to);
  if (f.symbol.trim()) q.set("symbol", f.symbol.trim().toUpperCase());
  if (f.side) q.set("side", f.side);
  if (f.outcome) q.set("outcome", f.outcome);
  if (f.kind) q.set("kind", f.kind);
  if (f.index) q.set("index", f.index);
  return q.toString();
}

/** True when both dates are set and the range runs backwards. */
export const backwardsRange = (f: Pick<SearchFilters, "from" | "to">): boolean => !!f.from && !!f.to && f.from > f.to;

export const filtersActive = (f: SearchFilters): boolean => searchQuery(f) !== "";

// --- words and numbers ----------------------------------------------------------------------------

export const sideWord = (s: Side) => (s === "long" ? "Buy" : "Short");

export function statusLabel(t: Pick<IntradayTrade, "status" | "exit_time" | "last_time" | "last_price">): string {
  switch (t.status) {
    case "open":
      return `Open · last ${t.last_price.toFixed(2)} at ${t.last_time}`;
    case "target":
      return `Hit take-profit at ${t.exit_time}`;
    case "stop":
      return `Cut-loss hit at ${t.exit_time}`;
    case "stop_gap":
      return `Gapped through cut-loss at ${t.exit_time}`;
    default:
      return `Closed at ${t.exit_time ?? t.last_time} (end of day)`;
  }
}

const INR = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** +₹1,234 / −₹1,234. A real minus sign, and a plus so colour is never the only signal. */
export function signedInr(n: number): string {
  const v = Math.round(n);
  if (v === 0) return "₹0";
  return `${v > 0 ? "+" : "−"}₹${INR.format(Math.abs(v))}`;
}

export function signedR(n: number): string {
  const v = Math.abs(n) < 0.005 ? 0 : n;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}R`;
}

/** "40% of 25": a win rate is never shown without the number of trades beside it. */
export function rateText(rate: number | null, n: number): string {
  return rate === null || n === 0 ? "—" : `${Math.round(rate * 100)}% of ${n}`;
}

export const tone = (n: number): "good" | "bad" | undefined => (n > 0 ? "good" : n < 0 ? "bad" : undefined);

export function clockFromMinutes(m: number): string {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

// --- dates (NSE trades in India Standard Time) -------------------------------------------------------

/** Today's date in IST as YYYY-MM-DD, whatever the browser's time zone. */
export function todayIst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(now);
}

function parts(date: string): [number, number, number] {
  const [y, m, d] = date.split("-").map(Number);
  return [y, m, d];
}

export function weekdayOf(date: string): number {
  const [y, m, d] = parts(date);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 Sunday
}

/** The previous (-1) or next (+1) weekday. NSE holidays are not known here; the server refuses them. */
export function shiftWeekday(date: string, step: 1 | -1): string {
  const [y, m, d] = parts(date);
  const t = new Date(Date.UTC(y, m - 1, d));
  do {
    t.setUTCDate(t.getUTCDate() + step);
  } while (t.getUTCDay() === 0 || t.getUTCDay() === 6);
  return t.toISOString().slice(0, 10);
}

/** The most recent weekday on or before ``today`` (IST). */
export function lastWeekday(today: string): string {
  const w = weekdayOf(today);
  return w === 0 ? shiftWeekday(today, -1) : w === 6 ? shiftWeekday(today, -1) : today;
}

export function dayLabel(date: string, withYear = true): string {
  const [y, m, d] = parts(date);
  const f = new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", ...(withYear ? { year: "numeric" as const } : {}), timeZone: "UTC" });
  return f.format(new Date(Date.UTC(y, m - 1, d))).replace(/,(?=\s\d{4}$)|,$/g, "");
}

// --- charts: plain SVG from data ---------------------------------------------------------------------

export interface SparkGeometry {
  points: string;
  zeroY: number;
  endX: number;
  endY: number;
}

/** A trade's R through the day as an SVG polyline in a ``w`` x ``h`` box, zero line included. */
export function sparkGeometry(path: Array<[number, number]>, w: number, h: number, pad = 2): SparkGeometry | null {
  if (path.length < 2) return null;
  const xs = path.map((p) => p[0]);
  const ys = path.map((p) => p[1]);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const x0 = Math.min(...xs);
  const xspan = Math.max(...xs) - x0 || 1;
  const px = (x: number) => pad + ((x - x0) / xspan) * (w - 2 * pad);
  const py = (y: number) => pad + (1 - (y - lo) / span) * (h - 2 * pad);
  const pts = path.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`);
  const last = path[path.length - 1];
  return { points: pts.join(" "), zeroY: py(0), endX: px(last[0]), endY: py(last[1]) };
}

export interface LineGeometry {
  points: string;
  baseline: number | null;
  endX: number;
  endY: number;
  lo: number;
  hi: number;
}

/**
 * Cumulative win ratio over days as a polyline. The axis spans the data rounded out to 10%, not
 * 0..100%, so the line is readable; the end labels say the range and a dashed line marks 50% when
 * it falls inside it.
 */
export function winRatioGeometry(series: SeriesPoint[], w: number, h: number, pad = 4): LineGeometry | null {
  const pts = series.filter((s) => s.cum_win_rate !== null);
  if (pts.length < 2) return null;
  const rates = pts.map((s) => s.cum_win_rate as number);
  const lo = Math.max(0, Math.floor(Math.min(...rates) * 10) / 10 - 0.1);
  const hi = Math.min(1, Math.ceil(Math.max(...rates) * 10) / 10 + 0.1);
  const span = hi - lo || 1;
  const px = (i: number) => pad + (i / (pts.length - 1)) * (w - 2 * pad);
  const py = (v: number) => pad + (1 - (v - lo) / span) * (h - 2 * pad);
  const coords = pts.map((s, i) => [px(i), py(s.cum_win_rate as number)] as const);
  const last = coords[coords.length - 1];
  return {
    points: coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "),
    baseline: lo <= 0.5 && 0.5 <= hi ? py(0.5) : null,
    endX: last[0], endY: last[1], lo, hi,
  };
}

// --- waiting for a background job ----------------------------------------------------------------------

/**
 * Re-read a day until the server says no job is running for it. A few failed reads in a row are
 * retried (the server may be restarting) instead of ending the wait.
 */
export async function waitForIdle<T extends { running: string | null }>(
  read: () => Promise<T>,
  onUpdate: (r: T) => void,
  alive: () => boolean,
  pause: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  maxBlips = 6,
): Promise<T | null> {
  let blips = 0;
  for (;;) {
    await pause(2000);
    if (!alive()) return null;
    try {
      const r = await read();
      blips = 0;
      onUpdate(r);
      if (r.running === null) return r;
    } catch (e) {
      if (++blips > maxBlips) throw e;
    }
  }
}
