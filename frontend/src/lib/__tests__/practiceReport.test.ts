import { describe, expect, it } from "vitest";
import type { PaperTrade } from "@/lib/api";
import { buildPracticeReport, closedDate } from "../practiceReport";

const mk = (id: string, o: Partial<PaperTrade>): PaperTrade => ({
  id, symbol: id.toUpperCase(), side: "LONG", entry: 100, stop: 97, target: 106, qty: 10, risk_inr: 30,
  opened_at: "2026-10-13T05:00:00Z", opened_bar: "2026-10-13", status: "CLOSED", ...o,
});

const ledger: PaperTrade[] = [
  mk("a", { exit_date: "2026-10-14", exit_reason: "target", pnl_inr: 60, r_multiple: 2 }),
  mk("b", { exit_date: "2026-10-14", exit_reason: "stop", pnl_inr: -30, r_multiple: -1, side: "SHORT" }),
  mk("c", { opened_at: "2026-10-14T05:00:00Z", exit_date: "2026-10-16", exit_reason: "stop", pnl_inr: -30, r_multiple: -1 }),
  mk("d", { opened_at: "2026-10-16T05:00:00Z", status: "OPEN", unrealized_inr: 12 }),
];

describe("practice report", () => {
  it("puts results on the close date and openings on the invested date", () => {
    const r = buildPracticeReport(ledger);
    expect(r.days.map((d) => d.date)).toEqual(["2026-10-16", "2026-10-14", "2026-10-13"]);
    const d13 = r.days[2], d14 = r.days[1], d16 = r.days[0];
    expect(d13).toMatchObject({ opened: 2, closed: 0, net_inr: 0, win_rate: null });
    expect(d14).toMatchObject({ opened: 1, closed: 2, wins: 1, losses: 1, net_inr: 30, win_rate: 0.5 });
    expect(d16).toMatchObject({ opened: 1, closed: 1, wins: 0, losses: 1, net_inr: -30, win_rate: 0 });
  });

  it("builds the cumulative line over closing days only, oldest first", () => {
    const r = buildPracticeReport(ledger);
    expect(r.series.map((s) => [s.date, s.cum_trades, s.cum_wins])).toEqual([["2026-10-14", 2, 1], ["2026-10-16", 3, 1]]);
    expect(r.win_rate).toBeCloseTo(1 / 3);
    expect(r.net_inr).toBe(0);
    expect(r.net_r).toBe(0);
    expect(r.closed).toBe(3);
  });

  it("splits by side and by how the trade ended; open trades stay out of the totals", () => {
    const r = buildPracticeReport(ledger);
    expect(r.by_side.map((s) => [s.key, s.trades, s.net_inr])).toEqual([["Buy", 2, 30], ["Short", 1, -30]]);
    expect(r.by_exit.map((s) => [s.key, s.trades])).toEqual([["Hit cut-loss", 2], ["Hit take-profit", 1]]);
    expect(r.open_now).toEqual({ count: 1, unrealized_inr: 12 });
  });

  it("treats a zero result as flat, not a win", () => {
    const r = buildPracticeReport([mk("z", { exit_date: "2026-10-14", pnl_inr: 0, r_multiple: 0, exit_reason: "manual" })]);
    expect(r.days.find((d) => d.date === "2026-10-14")).toMatchObject({ wins: 0, losses: 0, flat: 1 });
    expect(r.win_rate).toBe(0);
  });

  it("falls back to the close time in India when the exit bar date is missing", () => {
    expect(closedDate({ exit_date: "2026-10-14T00:00:00", closed_at: undefined })).toBe("2026-10-14");
    expect(closedDate({ exit_date: undefined, closed_at: "2026-10-14T19:00:00Z" })).toBe("2026-10-15"); // 00:30 IST
    expect(closedDate({ exit_date: undefined, closed_at: undefined })).toBe("");
  });

  it("is empty-safe", () => {
    const r = buildPracticeReport([]);
    expect(r).toMatchObject({ closed: 0, days: [], series: [], win_rate: null });
  });
});
