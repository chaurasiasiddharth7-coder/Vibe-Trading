/** The practice (fake-money) ledger read as a report: day by day, cumulative win ratio, and splits. */
import type { PaperTrade } from "@/lib/api";
import { investedDate } from "@/components/desk/tradeFilters";
import type { SeriesPoint } from "@/lib/intraday";

export interface PracticeDay {
  date: string;
  opened: number;
  closed: number;
  wins: number;
  losses: number;
  flat: number;
  win_rate: number | null;
  net_inr: number;
  net_r: number;
}

export interface PracticeSplit {
  key: string;
  trades: number;
  wins: number;
  win_rate: number | null;
  net_inr: number;
  net_r: number;
}

export interface PracticeReport {
  closed: number;
  opened_total: number;
  days: PracticeDay[];
  series: SeriesPoint[];
  win_rate: number | null;
  net_inr: number;
  net_r: number;
  by_side: PracticeSplit[];
  by_exit: PracticeSplit[];
  open_now: { count: number; unrealized_inr: number };
}

const IST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });

/** The calendar date a closed trade's result belongs to: the exit bar's date, else the close time in IST. */
export function closedDate(t: Pick<PaperTrade, "exit_date" | "closed_at">): string {
  const bar = (t.exit_date ?? "").slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(bar)) return bar;
  const d = t.closed_at ? new Date(t.closed_at) : null;
  return d && !Number.isNaN(d.getTime()) ? IST.format(d) : "";
}

const EXIT_WORDS: Record<string, string> = {
  target: "Hit take-profit", stop: "Hit cut-loss", trail: "Hit moved cut-loss", time: "Closed on time limit", manual: "Closed by you",
};

const rate = (wins: number, n: number): number | null => (n > 0 ? wins / n : null);
const outcome = (pnl: number): "win" | "loss" | "flat" => (pnl > 0 ? "win" : pnl < 0 ? "loss" : "flat");

function split(key: string, list: PaperTrade[]): PracticeSplit {
  const wins = list.filter((t) => (t.pnl_inr ?? 0) > 0).length;
  return {
    key, trades: list.length, wins, win_rate: rate(wins, list.length),
    net_inr: list.reduce((a, t) => a + (t.pnl_inr ?? 0), 0),
    net_r: list.reduce((a, t) => a + (t.r_multiple ?? 0), 0),
  };
}

export function buildPracticeReport(trades: PaperTrade[]): PracticeReport {
  const closed = trades.filter((t) => t.status === "CLOSED" && t.pnl_inr != null);
  const open = trades.filter((t) => t.status === "OPEN");

  const byDate = new Map<string, PracticeDay>();
  const day = (date: string): PracticeDay => {
    let d = byDate.get(date);
    if (!d) {
      d = { date, opened: 0, closed: 0, wins: 0, losses: 0, flat: 0, win_rate: null, net_inr: 0, net_r: 0 };
      byDate.set(date, d);
    }
    return d;
  };
  for (const t of trades) {
    const d = investedDate(t);
    if (d) day(d).opened += 1;
  }
  for (const t of closed) {
    const date = closedDate(t);
    if (!date) continue;
    const d = day(date);
    const o = outcome(t.pnl_inr ?? 0);
    d.closed += 1;
    if (o === "win") d.wins += 1; else if (o === "loss") d.losses += 1; else d.flat += 1;
    d.net_inr += t.pnl_inr ?? 0;
    d.net_r += t.r_multiple ?? 0;
  }
  for (const d of byDate.values()) d.win_rate = rate(d.wins, d.closed);
  const days = [...byDate.values()].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));

  // Cumulative line: oldest first, only dates on which something closed.
  let ct = 0, cw = 0, cr = 0, ci = 0;
  const series: SeriesPoint[] = [...days].reverse().filter((d) => d.closed > 0).map((d) => {
    ct += d.closed; cw += d.wins; cr += d.net_r; ci += d.net_inr;
    return { date: d.date, trades: d.closed, wins: d.wins, cum_trades: ct, cum_wins: cw, cum_win_rate: rate(cw, ct), cum_net_r: cr, cum_net_inr: ci };
  });

  const exits = new Map<string, PaperTrade[]>();
  for (const t of closed) exits.set(t.exit_reason ?? "other", [...(exits.get(t.exit_reason ?? "other") ?? []), t]);

  return {
    closed: closed.length,
    opened_total: trades.length,
    days,
    series,
    win_rate: rate(cw, ct),
    net_inr: ci,
    net_r: cr,
    by_side: (["LONG", "SHORT"] as const).map((s) => split(s === "LONG" ? "Buy" : "Short", closed.filter((t) => t.side === s))).filter((s) => s.trades > 0),
    by_exit: [...exits.entries()].map(([k, list]) => split(EXIT_WORDS[k] ?? "Other", list)).sort((a, b) => b.trades - a.trades),
    open_now: { count: open.length, unrealized_inr: open.reduce((a, t) => a + (t.unrealized_inr ?? 0), 0) },
  };
}
