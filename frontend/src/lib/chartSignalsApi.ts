import { request } from "@/lib/api";

/** A strategy the chart can overlay. */
export interface ChartStrategy {
  id: string;
  label: string;
  color: string;
  kind: "trades" | "forecast";
}

/** One past signal: what the strategy expected (entry → target, stop) and what happened. */
export interface SignalTrade {
  source: "replay" | "live";
  side: "LONG" | "SHORT";
  signal_date: string;
  entry_date: string;
  entry: number | null;
  stop: number | null;
  target: number | null;
  exit_date: string | null;
  exit: number | null;
  outcome: "target" | "stop" | "time" | "open" | "hand-closed" | string;
  r: number | null;
  pct: number | null;
  /** The desk no longer trades this kind of signal (board shorts, BTST): drawn greyed out. */
  off: boolean;
}

/** One saved Kronos forecast, made with data up to ``as_of`` only. */
export interface SignalForecast {
  source: "live" | "backfill";
  as_of: string;
  start: number;
  path: Array<{ time: string; close: number }>;
  predicted_pct: number | null;
  actual_pct: number | null;
  direction_right: boolean | null;
}

export interface StrategySignals extends ChartStrategy {
  items: Array<SignalTrade | SignalForecast>;
  summary: {
    signals?: number;
    closed?: number;
    target_hit_pct?: number | null;
    avg_pct?: number | null;
    net_pct?: number | null;
    forecasts?: number;
    judged?: number;
    direction_right_pct?: number | null;
  };
  error: string | null;
}

type Ok<T> = { ok: boolean; data: T };

export const chartSignalsApi = {
  options: () => request<Ok<ChartStrategy[]>>("/api/desk/chart/strategies"),
  signals: (symbol: string, ids: string[], years = 3) =>
    request<Ok<{ symbol: string; years: number; strategies: StrategySignals[] }>>(
      `/api/desk/chart/signals?symbol=${encodeURIComponent(symbol)}&ids=${encodeURIComponent(ids.join(","))}&years=${years}`,
    ),
};

export const isForecast = (i: SignalTrade | SignalForecast): i is SignalForecast => "as_of" in i;

const KEY = "desk.chart.strategies";

/** The strategies you ticked, remembered on this device. */
export function loadPicked(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function savePicked(ids: string[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(ids));
  } catch {
    /* private mode: the choice just isn't remembered */
  }
}
