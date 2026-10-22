/** Turns raw indicator numbers and candles into plain-language signals for the glance panel. */

export type Direction = "bull" | "bear" | "neutral";

export interface SignalRow {
  key: string;
  label: string;
  reading: string;
  direction: Direction;
  /** True for "stretched" readings that warn without taking a side. */
  caution?: boolean;
  meaning: string;
  /** Counts toward the bullish / bearish tally. */
  counts: boolean;
}

export interface Candle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface AnalysisInput {
  price: number;
  metrics: Record<string, unknown>;
  forecastPct?: number | null;
  /** True when the forecast was run just now, false when it is the one saved at entry. */
  forecastFresh?: boolean;
  candles?: Candle[];
}

/** Volume today against its 20-day average, and buying versus selling volume over 10 days. */
export function volumeReading(candles: Candle[]): { ratio: number; upDownRatio: number | null; direction: Direction; reading: string; meaning: string } | null {
  const vols = candles.map((c) => c.volume);
  if (candles.length < 22 || vols.some((v) => v == null || v < 0)) return null;
  const last = candles[candles.length - 1];
  const prev20 = candles.slice(-21, -1).map((c) => c.volume as number);
  const avg = prev20.reduce((a, b) => a + b, 0) / prev20.length;
  if (avg <= 0) return null;
  const ratio = (last.volume as number) / avg;
  let up = 0;
  let down = 0;
  for (let i = candles.length - 10; i < candles.length; i++) {
    if (candles[i].close >= candles[i - 1].close) up += candles[i].volume as number;
    else down += candles[i].volume as number;
  }
  const upDownRatio = down > 0 ? up / down : null;
  const green = last.close >= last.open;
  let direction: Direction = "neutral";
  let meaning: string;
  if (ratio >= 1.5) {
    direction = green ? "bull" : "bear";
    meaning = green
      ? "Unusually heavy trading on a rising day. Real buying behind the move."
      : "Unusually heavy trading on a falling day. Real selling behind the move.";
  } else if (upDownRatio != null && upDownRatio >= 1.3) {
    direction = "bull";
    meaning = "More shares changed hands on up days than down days lately. Buyers are building positions.";
  } else if (upDownRatio != null && upDownRatio <= 1 / 1.3) {
    direction = "bear";
    meaning = "More shares changed hands on down days than up days lately. Sellers are in control.";
  } else if (ratio <= 0.6) {
    meaning = "Quiet trading. Moves on low volume are less reliable.";
  } else {
    meaning = "Normal trading activity. Nothing unusual.";
  }
  return { ratio, upDownRatio, direction, reading: `${ratio.toFixed(1)}× its 20-day average`, meaning };
}

export interface Level {
  price: number;
  touches: number;
  /** Signed percent from the current price (negative = below). */
  distPct: number;
}

export interface SupportResistance {
  support: Level[];
  resistance: Level[];
}

/**
 * Swing highs and lows over the candles, merged when within 1.5% of each other.
 * Levels touched more than once rank first. Returns up to two on each side of the price.
 */
export function supportResistance(candles: Candle[], price: number, window = 5): SupportResistance {
  const highs: number[] = [];
  const lows: number[] = [];
  for (let i = window; i < candles.length - window; i++) {
    const slice = candles.slice(i - window, i + window + 1);
    if (candles[i].high >= Math.max(...slice.map((c) => c.high))) highs.push(candles[i].high);
    if (candles[i].low <= Math.min(...slice.map((c) => c.low))) lows.push(candles[i].low);
  }
  const cluster = (points: number[]): Level[] => {
    const sorted = [...points].sort((a, b) => a - b);
    const groups: number[][] = [];
    for (const p of sorted) {
      const g = groups[groups.length - 1];
      if (g && p <= (g.reduce((a, b) => a + b, 0) / g.length) * 1.015) g.push(p);
      else groups.push([p]);
    }
    return groups.map((g) => {
      const mean = g.reduce((a, b) => a + b, 0) / g.length;
      return { price: Math.round(mean * 100) / 100, touches: g.length, distPct: price > 0 ? (mean / price - 1) * 100 : 0 };
    });
  };
  const all = cluster([...highs, ...lows]);
  const pick = (levels: Level[]) =>
    [...levels]
      .sort((a, b) => Math.abs(a.distPct) - Math.abs(b.distPct))
      .sort((a, b) => Number(b.touches >= 2) - Number(a.touches >= 2))
      .slice(0, 2)
      .sort((a, b) => Math.abs(a.distPct) - Math.abs(b.distPct));
  return {
    support: pick(all.filter((l) => l.price < price * 0.998)),
    resistance: pick(all.filter((l) => l.price > price * 1.002)),
  };
}

/** Plain warnings when a level sits between the price and the trade's target or cut-loss. */
export function levelWarnings(
  sr: SupportResistance,
  trade: { side: "LONG" | "SHORT"; price: number; target?: number | null; stop?: number | null },
): string[] {
  const out: string[] = [];
  const long = trade.side === "LONG";
  const blocking = (long ? sr.resistance : sr.support).find((l) =>
    trade.target == null ? false : long ? l.price < trade.target : l.price > trade.target,
  );
  if (blocking) {
    out.push(
      `A ${long ? "resistance" : "support"} level near ₹${blocking.price.toLocaleString("en-IN")} (${blocking.touches} touch${blocking.touches === 1 ? "" : "es"}) sits before your take-profit. Price may stall there.`,
    );
  }
  const backing = (long ? sr.support : sr.resistance).find((l) =>
    trade.stop == null ? false : long ? l.price > trade.stop && l.price < trade.price : l.price < trade.stop && l.price > trade.price,
  );
  if (backing) {
    out.push(
      `The nearest ${long ? "support" : "resistance"} near ₹${backing.price.toLocaleString("en-IN")} is above your cut-loss. If it breaks, expect the cut-loss to be hit.`,
    );
  }
  return out;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function buildSignals({ price, metrics, forecastPct, forecastFresh, candles }: AnalysisInput): SignalRow[] {
  const rows: SignalRow[] = [];
  const e20 = num(metrics.ema_20);
  const e50 = num(metrics.ema_50);
  const e200 = num(metrics.ema_200);
  const rsi = num(metrics.rsi_14);
  const atr = num(metrics.atr);
  const upper = num(metrics.bb_upper);
  const lower = num(metrics.bb_lower);

  if (e20 != null && e50 != null) {
    const up = price > e20 && e20 > e50;
    const down = price < e20 && e20 < e50;
    rows.push({
      key: "trend",
      label: "Short-term trend",
      reading: up ? "Price above 20 and 50-day averages" : down ? "Price below 20 and 50-day averages" : "Mixed against the averages",
      direction: up ? "bull" : down ? "bear" : "neutral",
      meaning: up ? "Recent prices keep climbing." : down ? "Recent prices keep sliding." : "No clear direction over the last few weeks.",
      counts: true,
    });
  }
  if (e50 != null) {
    const above = price > e50 && (e200 == null || price > e200);
    const below = price < e50 && (e200 == null || price < e200);
    rows.push({
      key: "structure",
      label: "Long-term position",
      reading: above ? "Above its long-term averages" : below ? "Below its long-term averages" : "Between its long-term averages",
      direction: above ? "bull" : below ? "bear" : "neutral",
      meaning: above ? "A healthy sign for buyers." : below ? "A weak sign for buyers." : "Mixed signal.",
      counts: true,
    });
  }
  if (rsi != null) {
    const hot = rsi >= 70;
    const cold = rsi <= 30;
    rows.push({
      key: "rsi",
      label: "Momentum (RSI)",
      reading: `${rsi.toFixed(0)} out of 100`,
      direction: hot || cold ? "neutral" : rsi >= 55 ? "bull" : rsi <= 45 ? "bear" : "neutral",
      caution: hot || cold,
      meaning: hot
        ? "Already risen a lot. Buying now would be chasing."
        : cold
          ? "Already fallen a lot. Shorting now would be late."
          : rsi >= 55
            ? "Buyers have the upper hand."
            : rsi <= 45
              ? "Sellers have the upper hand."
              : "Buyers and sellers are balanced.",
      counts: true,
    });
  }
  if (typeof metrics.macd_status === "string") {
    const bull = metrics.macd_status.startsWith("Bullish");
    rows.push({
      key: "macd",
      label: "MACD",
      reading: bull ? "Bullish crossover" : "Bearish crossover",
      direction: bull ? "bull" : "bear",
      meaning: bull ? "Short-term momentum is above its slower average." : "Short-term momentum is below its slower average.",
      counts: true,
    });
  }
  if (typeof metrics.supertrend === "string") {
    const bull = metrics.supertrend === "BULLISH";
    rows.push({
      key: "supertrend",
      label: "SuperTrend",
      reading: bull ? "Bullish" : "Bearish",
      direction: bull ? "bull" : "bear",
      meaning: bull ? "Price is holding above its trailing support line." : "Price is staying below its trailing resistance line.",
      counts: true,
    });
  }
  if (upper != null && lower != null && upper > lower) {
    const pos = (price - lower) / (upper - lower);
    const top = pos >= 0.9;
    const bottom = pos <= 0.1;
    rows.push({
      key: "bands",
      label: "Price band (Bollinger)",
      reading: top ? "At the top of its usual range" : bottom ? "At the bottom of its usual range" : "Inside its usual range",
      direction: "neutral",
      caution: top || bottom,
      meaning: top ? "Stretched upward. Moves often cool off from here." : bottom ? "Stretched downward. Moves often bounce from here." : "Not stretched either way.",
      counts: false,
    });
  }
  if (atr != null && price > 0) {
    rows.push({
      key: "volatility",
      label: "Usual daily move",
      reading: `${((atr / price) * 100).toFixed(1)}% a day`,
      direction: "neutral",
      meaning: "How much it normally swings. Wider swings need a wider cut-loss.",
      counts: false,
    });
  }
  const vol = candles ? volumeReading(candles) : null;
  if (vol) {
    rows.push({
      key: "volume",
      label: "Volume",
      reading: vol.reading,
      direction: vol.direction,
      meaning: vol.meaning,
      counts: true,
    });
  }
  if (forecastPct != null) {
    const bull = forecastPct >= 1.5;
    const bear = forecastPct <= -1.5;
    rows.push({
      key: "forecast",
      label: forecastFresh ? "AI forecast (just run)" : "AI forecast (earlier check)",
      reading: `${forecastPct >= 0 ? "+" : "−"}${Math.abs(forecastPct).toFixed(1)}% expected`,
      direction: bull ? "bull" : bear ? "bear" : "neutral",
      meaning: bull ? "The forecast model expects a rise." : bear ? "The forecast model expects a fall." : "The forecast model expects little change.",
      counts: true,
    });
  }
  return rows;
}

export interface Tally {
  bull: number;
  bear: number;
  neutral: number;
  verdict: string;
  lean: Direction;
}

export function tally(rows: SignalRow[]): Tally {
  const counted = rows.filter((r) => r.counts);
  const bull = counted.filter((r) => r.direction === "bull").length;
  const bear = counted.filter((r) => r.direction === "bear").length;
  const neutral = counted.length - bull - bear;
  const total = counted.length;
  const gap = bull - bear;
  const lean: Direction = gap >= 2 ? "bull" : gap <= -2 ? "bear" : "neutral";
  const verdict =
    total === 0
      ? "Not enough data for a read."
      : lean === "bull"
        ? `Leans bullish: ${bull} of ${total} signals point up.`
        : lean === "bear"
          ? `Leans bearish: ${bear} of ${total} signals point down.`
          : `No clear lean: ${bull} up, ${bear} down, ${neutral} neutral.`;
  return { bull, bear, neutral, verdict, lean };
}

const PATTERN_MEANING: Record<string, string> = {
  DOJI: "Buyers and sellers cancelled out. A pause that sometimes comes before a turn.",
  HAMMER: "Sold off hard, then bought back up. Can mark a bottom.",
  SHOOTINGSTAR: "Pushed up, then sold back down. Can mark a top.",
  "ENGULFING|Bullish": "Today's green candle swallowed yesterday's red one. Buyers took over.",
  "ENGULFING|Bearish": "Today's red candle swallowed yesterday's green one. Sellers took over.",
  MORNINGSTAR: "Three-candle bottom signal: a drop, a pause, then a strong rise.",
};

export interface PatternRow {
  name: string;
  direction: Direction;
  meaning: string;
}

/** Backend labels look like "HAMMER (Bullish)". */
export function parsePatterns(labels: unknown): PatternRow[] {
  if (!Array.isArray(labels)) return [];
  return labels
    .filter((l): l is string => typeof l === "string")
    .map((l) => {
      const m = l.match(/^([A-Z]+)\s*\((\w+)\)$/);
      const base = m?.[1] ?? l;
      const dir = m?.[2] ?? "Neutral";
      const direction: Direction = dir === "Bullish" ? "bull" : dir === "Bearish" ? "bear" : "neutral";
      const pretty = base.charAt(0) + base.slice(1).toLowerCase();
      return {
        name: base === "MORNINGSTAR" ? "Morning star" : base === "SHOOTINGSTAR" ? "Shooting star" : pretty,
        direction,
        meaning: PATTERN_MEANING[`${base}|${dir}`] ?? PATTERN_MEANING[base] ?? "",
      };
    });
}

export interface CandleRow {
  time: string;
  direction: Direction;
  text: string;
}

/** Plain reading of the last few candles: colour, size of body, wicks and day change. */
export function describeCandles(candles: Candle[], n = 3): CandleRow[] {
  const out: CandleRow[] = [];
  const start = Math.max(1, candles.length - n);
  for (let i = start; i < candles.length; i++) {
    const c = candles[i];
    const prev = candles[i - 1];
    const range = c.high - c.low;
    if (range <= 0) continue;
    const body = Math.abs(c.close - c.open) / range;
    const upper = (c.high - Math.max(c.open, c.close)) / range;
    const lower = (Math.min(c.open, c.close) - c.low) / range;
    const green = c.close >= c.open;
    const change = prev.close > 0 ? ((c.close / prev.close - 1) * 100) : 0;
    const size = body >= 0.6 ? "Strong" : body <= 0.2 ? "Small" : "Medium";
    const parts = [`${size} ${green ? "green" : "red"} candle`];
    if (body >= 0.6) parts.push(green ? "closed near the high, buyers in control" : "closed near the low, sellers in control");
    if (upper >= 0.4 && body < 0.6) parts.push("long upper tail: it was pushed back down from the high");
    if (lower >= 0.4 && body < 0.6) parts.push("long lower tail: buyers stepped in at the low");
    out.push({
      time: c.time,
      direction: body <= 0.2 ? "neutral" : green ? "bull" : "bear",
      text: `${parts.join(", ")} (${change >= 0 ? "+" : "−"}${Math.abs(change).toFixed(1)}% on the day).`,
    });
  }
  return out;
}

export interface EntryCheck {
  label: string;
  atEntry: number;
  now: Direction | null;
}

/** Compares the signals the trade was opened on with the same checks today. */
export function entryVsNow(
  votes: { trend: number; structure: number; momentum: number; forecast: number } | null | undefined,
  rows: SignalRow[],
  forecastFresh = false,
): EntryCheck[] {
  if (!votes) return [];
  const dir = (key: string): Direction | null => rows.find((r) => r.key === key)?.direction ?? null;
  const momentum = (): Direction | null => {
    const a = dir("rsi");
    const b = dir("macd");
    if (a == null && b == null) return null;
    return a === b ? a : a === "neutral" ? b : b === "neutral" ? a : "neutral";
  };
  return [
    { label: "Trend", atEntry: votes.trend, now: dir("trend") },
    { label: "Position", atEntry: votes.structure, now: dir("structure") },
    { label: "Momentum", atEntry: votes.momentum, now: momentum() },
    { label: "AI forecast", atEntry: votes.forecast, now: forecastFresh ? dir("forecast") : null },
  ];
}
