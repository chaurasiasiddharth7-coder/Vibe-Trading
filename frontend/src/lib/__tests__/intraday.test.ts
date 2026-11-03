import { describe, expect, it } from "vitest";
import {
  EMPTY_FILTERS, backwardsRange, filtersActive, searchQuery,
  dayLabel, lastWeekday, rateText, shiftWeekday, signedInr, signedR, sparkGeometry, statusLabel, todayIst, waitForIdle,
  winRatioGeometry, type SeriesPoint,
} from "@/lib/intraday";

describe("numbers and words", () => {
  it("signs money with a real minus and groups the Indian way", () => {
    expect(signedInr(12345.6)).toBe("+₹12,346");
    expect(signedInr(-1234567)).toBe("−₹12,34,567");
    expect(signedInr(0.2)).toBe("₹0");
  });
  it("signs R and treats tiny values as flat", () => {
    expect(signedR(1.234)).toBe("+1.23R");
    expect(signedR(-0.5)).toBe("−0.50R");
    expect(signedR(0.001)).toBe("0.00R");
  });
  it("never shows a win rate without the trade count", () => {
    expect(rateText(0.4, 25)).toBe("40% of 25");
    expect(rateText(null, 0)).toBe("—");
    expect(rateText(0.5, 0)).toBe("—");
  });
  it("explains each way a trade can end in plain words", () => {
    const base = { exit_time: "10:20", last_time: "10:20", last_price: 101.5 };
    expect(statusLabel({ ...base, status: "target" })).toBe("Hit take-profit at 10:20");
    expect(statusLabel({ ...base, status: "stop" })).toBe("Cut-loss hit at 10:20");
    expect(statusLabel({ ...base, status: "stop_gap" })).toContain("Gapped through");
    expect(statusLabel({ ...base, status: "flat", exit_time: "15:15" })).toBe("Closed at 15:15 (end of day)");
    expect(statusLabel({ ...base, status: "open", exit_time: null })).toBe("Open · last 101.50 at 10:20");
  });
});

describe("dates are India time and skip weekends", () => {
  it("reads today in IST whatever the browser zone", () => {
    expect(todayIst(new Date("2026-10-27T20:00:00Z"))).toBe("2026-10-28"); // 01:30 IST next day
    expect(todayIst(new Date("2026-10-27T05:00:00Z"))).toBe("2026-10-27");
  });
  it("steps over Saturday and Sunday", () => {
    expect(shiftWeekday("2026-10-23", 1)).toBe("2026-10-26"); // Fri -> Mon
    expect(shiftWeekday("2026-10-26", -1)).toBe("2026-10-23"); // Mon -> Fri
    expect(shiftWeekday("2026-10-27", 1)).toBe("2026-10-28");
  });
  it("lands on the latest weekday", () => {
    expect(lastWeekday("2026-10-24")).toBe("2026-10-23"); // Sat
    expect(lastWeekday("2026-10-25")).toBe("2026-10-23"); // Sun
    expect(lastWeekday("2026-10-26")).toBe("2026-10-26");
  });
});

describe("chart geometry", () => {
  it("draws a path with its zero line inside the box", () => {
    const g = sparkGeometry([[575, 0], [580, 1], [585, -0.5], [590, 2]], 100, 20)!;
    expect(g.points.split(" ")).toHaveLength(4);
    expect(g.zeroY).toBeGreaterThan(0);
    expect(g.zeroY).toBeLessThan(20);
    expect(g.endX).toBeCloseTo(98, 0);
  });
  it("has nothing to draw for a single point", () => {
    expect(sparkGeometry([[575, 0]], 100, 20)).toBeNull();
  });
  it("spans the win ratio axis to the data, rounded out to 10%, and marks 50% only when it is inside", () => {
    const pt = (i: number, r: number): SeriesPoint => ({ date: `d${i}`, trades: 1, wins: 0, cum_trades: i, cum_wins: 0, cum_win_rate: r, cum_net_r: 0, cum_net_inr: 0 });
    const g = winRatioGeometry([pt(1, 0.4), pt(2, 0.5), pt(3, 0.45)], 100, 100, 0)!;
    expect(g.lo).toBeCloseTo(0.3, 5);
    expect(g.hi).toBeCloseTo(0.6, 5);
    expect(g.baseline).toBeCloseTo(100 * (1 - (0.5 - 0.3) / 0.3), 3);
    expect(g.points.split(" ")[0]).toBe("0.0,66.7");
    const low = winRatioGeometry([pt(1, 0.1), pt(2, 0.2)], 100, 100, 0)!;
    expect(low.baseline).toBeNull();
    expect(winRatioGeometry([pt(1, 0.5)], 100, 100)).toBeNull();
  });
  it("labels days without a stray comma", () => {
    expect(dayLabel("2026-10-27", false)).toBe("Tue, 27 Oct");
    expect(dayLabel("2026-10-27")).toBe("Tue, 27 Oct 2026");
  });
});

describe("waiting for a job", () => {
  const instant = () => Promise.resolve();
  it("returns when the server says nothing is running", async () => {
    const states = [{ running: "close" }, { running: "close" }, { running: null }];
    let i = 0;
    const seen: Array<string | null> = [];
    const r = await waitForIdle(() => Promise.resolve(states[i++]), (s) => seen.push(s.running), () => true, instant);
    expect(r?.running).toBeNull();
    expect(seen).toEqual(["close", "close", null]);
  });
  it("stops quietly when the page is gone", async () => {
    const r = await waitForIdle(() => Promise.resolve({ running: "open" }), () => undefined, () => false, instant);
    expect(r).toBeNull();
  });
  it("retries a few dropped reads, then gives up", async () => {
    let calls = 0;
    const flaky = () => (++calls < 3 ? Promise.reject(new TypeError("net")) : Promise.resolve({ running: null }));
    expect((await waitForIdle(flaky, () => undefined, () => true, instant))?.running).toBeNull();
    await expect(waitForIdle(() => Promise.reject(new TypeError("down")), () => undefined, () => true, instant, 2)).rejects.toThrow("down");
  });
});


describe("search filters", () => {
  it("leaves empty filters out and tidies the symbol", () => {
    expect(searchQuery(EMPTY_FILTERS)).toBe("");
    expect(filtersActive(EMPTY_FILTERS)).toBe(false);
    const q = searchQuery({ ...EMPTY_FILTERS, from: "2026-10-01", to: "2026-10-31", symbol: " tcs ", side: "long", outcome: "won", kind: "trend_up", index: "niftybank" });
    expect(q).toBe("from=2026-10-01&to=2026-10-31&symbol=TCS&side=long&outcome=won&kind=trend_up&index=niftybank");
    expect(filtersActive({ ...EMPTY_FILTERS, symbol: "  " })).toBe(false); // blanks are not a filter
  });
  it("spots a backwards date range", () => {
    expect(backwardsRange({ from: "2026-10-31", to: "2026-10-01" })).toBe(true);
    expect(backwardsRange({ from: "2026-10-01", to: "2026-10-01" })).toBe(false);
    expect(backwardsRange({ from: "", to: "2026-10-01" })).toBe(false);
  });
});
