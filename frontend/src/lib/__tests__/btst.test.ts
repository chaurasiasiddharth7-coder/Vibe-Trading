import { orderSteps, outcomeText, qtyFor, rupeesFor, scanLine, signedInr, signedPct, trustSentence, type BtstRecord, type BtstStats } from "@/lib/btst";

const empty = { trades: 0 };
const stats = (n: number, avg: number): BtstStats => ({
  kind: "live", days: n, settled_days: n, skipped_days: 0, ambiguous: 0, exact: 0, by_year: [], first: "2024-01-02", last: "2026-11-17",
  picks: { trades: n, avg_net_pct: avg }, picks_target_first: empty, picks_no_stop: { trades: n, avg_net_pct: avg },
  candidates: { settled: 0, by_warning_count: [], by_warning: [] },
});

describe("btst helpers", () => {
  it("formats signed numbers with a real minus sign", () => {
    expect(signedPct(0.75)).toBe("+0.75%");
    expect(signedPct(-2.25)).toBe("−2.25%");
    expect(signedPct(null)).toBe("—");
    expect(signedInr(-1234.4)).toBe("−₹1,234");
  });

  it("sizes the trade and turns % into rupees", () => {
    expect(qtyFor(100000, 333.3)).toBe(300);
    expect(qtyFor(0, 10)).toBe(0);
    expect(rupeesFor(0.75, 300, 333.3)).toBeCloseTo(749.9, 1);
    expect(rupeesFor(null, 300, 333.3)).toBeNull();
  });

  it("writes the broker steps with and without a stop", () => {
    const pick = { symbol: "ABC", entry: 250, target: 252.5, stop: 245, gain_pct: 6.1 };
    const s = orderSteps(pick, 400);
    expect(s[0]).toContain("Buy 400 ABC");
    expect(s[1]).toContain("GTT OCO") && expect(s[1]).toContain("₹252.50") && expect(s[1]).toContain("₹245.00");
    expect(orderSteps({ ...pick, stop: null }, 1)[1]).toContain("GTT sell");
  });

  it("says plainly what happened", () => {
    expect(outcomeText(null)).toMatch(/Waiting/);
    expect(outcomeText({ reason: "target", exit: 252.5 })).toBe("Take-profit filled at ₹252.50");
    expect(outcomeText({ data_check: true, note: "split" })).toBe("split");
  });

  it("does not trust a short live record", () => {
    expect(trustSentence(stats(0, 0), stats(900, 0.3))).toMatch(/No live trades yet.*hypothesis/);
    expect(trustSentence(stats(4, 0.5), null)).toMatch(/Too early to trust: 4 of 30/);
    expect(trustSentence(stats(40, -0.2), null)).toMatch(/losing money live/);
    expect(trustSentence(stats(40, 0.25), null)).toMatch(/Small edge/);
  });

  it("summarises a scan in one line", () => {
    const rec = { scanned_at: "15:20", market: { breadth_pct: 54.2, liquid: 812 }, coverage: { sources: ["nse"], universe: 1100, covered: 990 } } as unknown as BtstRecord;
    expect(scanLine(rec)).toBe("Scanned 15:20 · 812 liquid stocks, 54% up · prices: nse · covered 990 of 1100");
  });
});
