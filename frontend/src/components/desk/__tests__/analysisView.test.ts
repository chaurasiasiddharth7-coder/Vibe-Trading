import { describe, expect, it } from "vitest";
import { buildSignals, describeCandles, entryVsNow, parsePatterns, tally } from "../analysisView";

const bullish = {
  price: 110,
  metrics: { ema_20: 105, ema_50: 100, ema_200: 90, rsi_14: 60, macd_status: "Bullish Crossover", supertrend: "BULLISH", atr: 2.2, bb_upper: 120, bb_lower: 95 },
};

describe("buildSignals / tally", () => {
  it("reads an uptrend as bullish with plain meanings", () => {
    const rows = buildSignals(bullish);
    expect(rows.find((r) => r.key === "trend")?.direction).toBe("bull");
    expect(rows.find((r) => r.key === "structure")?.direction).toBe("bull");
    expect(rows.find((r) => r.key === "volatility")?.reading).toBe("2.0% a day");
    const t = tally(rows);
    expect(t.lean).toBe("bull");
    expect(t.verdict).toMatch(/Leans bullish: 5 of 5/);
  });

  it("flags stretched RSI as a caution without taking a side", () => {
    const rsi = buildSignals({ ...bullish, metrics: { ...bullish.metrics, rsi_14: 78 } }).find((r) => r.key === "rsi")!;
    expect(rsi.caution).toBe(true);
    expect(rsi.direction).toBe("neutral");
    expect(rsi.meaning).toMatch(/chasing/);
  });

  it("calls mixed signals neutral and never counts info-only rows", () => {
    const rows = buildSignals({
      price: 100,
      metrics: { ema_20: 99, ema_50: 101, rsi_14: 50, macd_status: "Bearish Crossover", supertrend: "BULLISH", atr: 1, bb_upper: 120, bb_lower: 80 },
    });
    expect(tally(rows).lean).toBe("neutral");
    expect(rows.filter((r) => r.counts).length).toBe(5);
  });

  it("uses the AI forecast when given", () => {
    const f = buildSignals({ ...bullish, forecastPct: -3 }).find((r) => r.key === "forecast")!;
    expect(f.direction).toBe("bear");
    expect(f.reading).toBe("−3.0% expected");
  });

  it("copes with missing metrics", () => {
    expect(buildSignals({ price: 100, metrics: {} })).toEqual([]);
    expect(tally([]).verdict).toMatch(/Not enough data/);
  });
});

describe("candles and patterns", () => {
  it("parses backend labels", () => {
    expect(parsePatterns(["HAMMER (Bullish)", "SHOOTINGSTAR (Bearish)", "DOJI (Neutral)"]).map((p) => [p.name, p.direction])).toEqual([
      ["Hammer", "bull"], ["Shooting star", "bear"], ["Doji", "neutral"],
    ]);
    expect(parsePatterns(undefined)).toEqual([]);
  });

  it("describes body, wicks and day change", () => {
    const rows = describeCandles([
      { time: "2026-10-20", open: 100, high: 101, low: 99, close: 100 },
      { time: "2026-10-21", open: 100, high: 110, low: 99.5, close: 109.5 },
      { time: "2026-10-22", open: 109, high: 115, low: 108, close: 109.2 },
    ]);
    expect(rows[0].text).toMatch(/Strong green candle, closed near the high/);
    expect(rows[0].text).toContain("+9.5%");
    expect(rows[1].text).toMatch(/Small green candle/);
    expect(rows[1].direction).toBe("neutral");
  });
});

describe("entryVsNow", () => {
  it("compares the entry votes with today's reading", () => {
    const rows = buildSignals({ ...bullish, metrics: { ...bullish.metrics, rsi_14: 40, macd_status: "Bearish Crossover" } });
    const out = entryVsNow({ trend: 1, structure: 1, momentum: 1, forecast: 1 }, rows);
    expect(out.find((e) => e.label === "Trend")?.now).toBe("bull");
    expect(out.find((e) => e.label === "Momentum")?.now).toBe("bear");
    expect(out.find((e) => e.label === "AI forecast")?.now).toBeNull();
    expect(entryVsNow(null, rows)).toEqual([]);
  });
});

import { levelWarnings, supportResistance, volumeReading, type Candle } from "../analysisView";

const mk = (i: number, close: number, volume: number, open = close - 0.5): Candle => ({
  time: `2026-09-${String(i + 1).padStart(2, "0")}`, open, high: close + 1, low: close - 1, close, volume,
});

describe("volumeReading", () => {
  const quiet = Array.from({ length: 30 }, (_, i) => mk(i, 100 + (i % 2), 1000));

  it("calls a heavy rising day bullish", () => {
    const v = volumeReading([...quiet.slice(0, 29), { ...mk(29, 103, 2500), open: 100 }])!;
    expect(v.direction).toBe("bull");
    expect(v.reading).toBe("2.5× its 20-day average");
  });

  it("calls a heavy falling day bearish", () => {
    const v = volumeReading([...quiet.slice(0, 29), { ...mk(29, 97, 2500), open: 101 }])!;
    expect(v.direction).toBe("bear");
  });

  it("is neutral on normal volume and needs enough data", () => {
    expect(volumeReading(quiet)!.direction).toBe("neutral");
    expect(volumeReading(quiet.slice(0, 10))).toBeNull();
    expect(volumeReading(quiet.map((c) => ({ ...c, volume: undefined })))).toBeNull();
  });
});

describe("supportResistance", () => {
  // Price oscillates between ~95 and ~105 so both edges get tested repeatedly.
  const wave: Candle[] = Array.from({ length: 80 }, (_, i) => {
    const c = 100 + 5 * Math.sin(i / 3);
    return { time: `d${i}`, open: c, high: c + 0.4, low: c - 0.4, close: c, volume: 1 };
  });

  it("finds levels on both sides of the price, nearest first, merging close swings", () => {
    const sr = supportResistance(wave, 100);
    expect(sr.resistance.length).toBeGreaterThan(0);
    expect(sr.support.length).toBeGreaterThan(0);
    expect(sr.resistance[0].price).toBeGreaterThan(100);
    expect(sr.support[0].price).toBeLessThan(100);
    expect(sr.resistance.length).toBeLessThanOrEqual(2);
    expect(sr.resistance[0].touches).toBeGreaterThan(1);
    expect(sr.resistance[0].distPct).toBeGreaterThan(0);
  });

  it("returns nothing for a short series", () => {
    expect(supportResistance(wave.slice(0, 6), 100)).toEqual({ support: [], resistance: [] });
  });

  it("warns when a resistance sits before a long's take-profit", () => {
    const sr = { support: [{ price: 98, touches: 3, distPct: -2 }], resistance: [{ price: 104, touches: 2, distPct: 4 }] };
    const w = levelWarnings(sr, { side: "LONG", price: 100, target: 110, stop: 95 });
    expect(w[0]).toMatch(/resistance level near ₹104.*before your take-profit/);
    expect(w[1]).toMatch(/support near ₹98 is above your cut-loss/);
    expect(levelWarnings(sr, { side: "LONG", price: 100, target: 103, stop: 99 })).toEqual([]);
  });

  it("mirrors the warning for shorts", () => {
    const sr = { support: [{ price: 96, touches: 2, distPct: -4 }], resistance: [] };
    const w = levelWarnings(sr, { side: "SHORT", price: 100, target: 90, stop: 105 });
    expect(w[0]).toMatch(/support level near ₹96/);
  });
});
