import { describe, expect, it } from "vitest";
import type { HistoryStats } from "@/lib/api";
import type { SignalRow } from "../analysisView";
import { nowScore, scoreIdea } from "../score";

const good: HistoryStats = { trades: 10, win_rate: 0.6, avg_r: 0.3, total_r: 3, profit_factor: 1.8 };
const bad: HistoryStats = { trades: 10, win_rate: 0.3, avg_r: -0.3, total_r: -3, profit_factor: 0.6 };
const all = { trend: 1, structure: 1, momentum: 1, forecast: 1 };

describe("scoreIdea", () => {
  it("scores a clean, proven long near the top", () => {
    const s = scoreIdea({ side: "LONG", votes: all, rsi: 55, history: good, sideHistory: good, kronosPct: 3 });
    expect(s.score).toBe(10);
    expect(s.tone).toBe("strong");
    expect(s.positives[0]).toBe("4 of 4 checks agree");
    expect(s.risks).toEqual([]);
  });

  it("marks down chasing, a bad record, warnings and stale prices", () => {
    const s = scoreIdea({
      side: "LONG", votes: { trend: 1, structure: 1, momentum: 1, forecast: -1 }, rsi: 78,
      history: bad, cautions: ["thin volume", "no Kronos forecast"], stale: true,
    });
    expect(s.score).toBeLessThan(5);
    expect(s.risks).toEqual(expect.arrayContaining(["Already risen a lot, so buying now is chasing", "Lost money on this stock before"]));
    expect(s.risks.length).toBeLessThanOrEqual(3);
  });

  it("mirrors the logic for shorts", () => {
    const s = scoreIdea({ side: "SHORT", votes: { trend: -1, structure: -1, momentum: -1, forecast: 0 }, rsi: 25, history: null });
    expect(s.risks).toContain("Already fallen a lot, so shorting now is late");
    expect(s.positives).toContain("3 of 4 checks agree");
  });

  it("never leaves 0 to 10 and bands match the score", () => {
    const worst = scoreIdea({ side: "LONG", votes: { trend: -1, structure: -1, momentum: -1, forecast: -1 }, rsi: 80, history: bad, cautions: ["a", "b", "c", "d"], stale: true });
    expect(worst.score).toBe(0);
    expect(worst.verdict).toBe("Avoid");
    expect(scoreIdea({ side: "LONG", votes: { trend: 1, structure: 1, momentum: 0, forecast: 0 }, rsi: 50, history: null }).verdict).toBe("Weak");
    expect(scoreIdea({ side: "LONG", votes: { trend: 1, structure: 1, momentum: 1, forecast: 0 }, rsi: 50, history: null }).verdict).toBe("Okay");
  });
});

describe("nowScore", () => {
  const row = (direction: SignalRow["direction"]): SignalRow => ({ key: direction, label: "x", reading: "", direction, meaning: "", counts: true });
  it("rates how well today's signals fit the trade", () => {
    const bulls = [row("bull"), row("bull"), row("bull"), row("neutral")];
    expect(nowScore(bulls, "LONG")?.score).toBe(8.8);
    expect(nowScore(bulls, "SHORT")?.verdict).toBe("Avoid");
    expect(nowScore([row("bull")], "LONG")).toBeNull();
  });
});
