import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScoreBadge } from "../ScoreBadge";
import { AnalysisPanel } from "../AnalysisPanel";
import { scoreIdea } from "../score";

vi.mock("@/lib/api", async () => ({ ...(await vi.importActual<object>("@/lib/api")), api: {} }));
afterEach(cleanup);

describe("ScoreBadge", () => {
  it("shows the number, a meter for screen readers and the verdict word", () => {
    render(<ScoreBadge score={7.5} tone="good" verdict="Good" />);
    expect(screen.getByText("7.5")).toBeInTheDocument();
    expect(screen.getByText("Good")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Trade score" })).toHaveAttribute("aria-valuetext", "7.5 out of 10, Good");
  });
});

describe("AnalysisPanel score", () => {
  const metrics = { ema_20: 105, ema_50: 100, ema_200: 90, rsi_14: 60, macd_status: "Bullish Crossover", supertrend: "BULLISH" };
  const candles = [{ time: "d1", open: 100, high: 105, low: 99, close: 104 }];

  it("shows today's chart score next to the score at entry", () => {
    const entry = scoreIdea({ side: "LONG", votes: { trend: 1, structure: 1, momentum: 1, forecast: 1 }, rsi: 55 });
    render(<AnalysisPanel price={110} metrics={metrics} candles={candles} scoreSide="LONG" entryScore={entry} />);
    const box = screen.getByTestId("now-score");
    expect(box).toHaveTextContent("10.0");
    expect(box).toHaveTextContent("At entry: 7.8/10 Good");
    expect(box).toHaveTextContent("Looking better than when you opened it.");
    expect(screen.getByRole("meter", { name: "Chart score today" })).toBeInTheDocument();
  });

  it("omits the score when there is no direction", () => {
    render(<AnalysisPanel price={110} metrics={metrics} candles={candles} />);
    expect(screen.queryByTestId("now-score")).toBeNull();
  });
});
