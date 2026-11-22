import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const added: Array<{ kind: string; opts: Record<string, unknown>; data: unknown[] }> = [];
const markers: unknown[][] = [];
vi.mock("lightweight-charts", () => {
  const series = (kind: string, opts: Record<string, unknown>) => {
    const rec = { kind, opts, data: [] as unknown[] };
    added.push(rec);
    return {
      setData: (d: unknown[]) => (rec.data = d),
      priceScale: () => ({ applyOptions: () => undefined }),
      createPriceLine: () => undefined,
    };
  };
  return {
    CandlestickSeries: "candle",
    HistogramSeries: "hist",
    LineSeries: "line",
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0 },
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2, LargeDashed: 3, SparseDotted: 4 },
    PriceScaleMode: { Normal: 0, Logarithmic: 1 },
    createSeriesMarkers: (_s: unknown, m: unknown[]) => markers.push(m),
    createChart: () => ({
      addSeries: (kind: string, opts: Record<string, unknown>) => series(kind, opts),
      timeScale: () => ({ setVisibleLogicalRange: () => undefined, subscribeVisibleLogicalRangeChange: () => undefined }),
      subscribeCrosshairMove: () => undefined,
      subscribeClick: () => undefined,
      remove: () => undefined,
    }),
  };
});

const bars = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 9, 1 + i)).toISOString().slice(0, 10);
  return { time: d, open: 100 + i, high: 102 + i, low: 99 + i, close: 101 + i, volume: 1000 };
});

describe("DeskChart strategy overlays", () => {
  beforeEach(() => {
    added.length = 0;
    markers.length = 0;
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("lets you tick several strategies and draws each one's predictions as dotted lines", async () => {
    const api = await import("@/lib/chartSignalsApi");
    vi.spyOn(api.chartSignalsApi, "options").mockResolvedValue({
      ok: true,
      data: [
        { id: "signal_board", label: "Signal board", color: "#2563eb", kind: "trades" },
        { id: "kronos", label: "Kronos forecast", color: "#f59e0b", kind: "forecast" },
      ],
    });
    const sig = vi.spyOn(api.chartSignalsApi, "signals").mockResolvedValue({
      ok: true,
      data: {
        symbol: "RBA",
        years: 3,
        strategies: [
          {
            id: "signal_board", label: "Signal board", color: "#2563eb", kind: "trades", error: null,
            summary: { signals: 1, closed: 1, target_hit_pct: 100, avg_pct: 4.1, net_pct: 4.1 },
            items: [{ source: "replay", side: "LONG", signal_date: bars[5].time, entry_date: bars[5].time, entry: 105, stop: 100, target: 115,
              exit_date: bars[12].time, exit: 115, outcome: "target", r: 2, pct: 4.1, off: false }],
          },
          {
            id: "kronos", label: "Kronos forecast", color: "#f59e0b", kind: "forecast", error: null,
            summary: { forecasts: 1, judged: 1, direction_right_pct: 100 },
            items: [{ source: "backfill", as_of: bars[10].time, start: 111, path: [{ time: bars[11].time, close: 112 }, { time: bars[12].time, close: 114 }],
              predicted_pct: 2.7, actual_pct: 2.5, direction_right: true }],
          },
        ],
      },
    });
    const { DeskChart } = await import("@/components/desk/DeskChart");
    render(<DeskChart historical={bars} symbol="RBA" showVolume />);
    await userEvent.click(await screen.findByRole("button", { name: /Strategies/ }));
    const list = screen.getByRole("listbox", { name: "Strategies to show" });
    await userEvent.click(within(list).getByRole("option", { name: /Signal board/ }));
    await userEvent.click(within(list).getByRole("option", { name: /Kronos forecast/ }));
    expect(sig).toHaveBeenLastCalledWith("RBA", ["signal_board", "kronos"]);
    expect(await screen.findByText("On this stock (replays + live, after costs)")).toBeInTheDocument();
    const lastBuild = added.slice(added.map((a) => a.kind).lastIndexOf("candle")); // the chart is rebuilt as lines arrive
    const dotted = lastBuild.filter((a) => a.kind === "line" && a.opts.lineStyle === 1);
    expect(dotted.length).toBe(2); // board entry→target, Kronos path
    expect(lastBuild.some((a) => a.kind === "line" && a.opts.lineStyle === 4)).toBe(true); // board entry→stop
    expect(markers.at(-1)?.length).toBeGreaterThanOrEqual(3);
    expect(JSON.parse(localStorage.getItem("desk.chart.strategies") ?? "[]")).toEqual(["signal_board", "kronos"]);
    await userEvent.click(screen.getByRole("button", { name: "Hide Kronos forecast" }));
    expect(sig).toHaveBeenLastCalledWith("RBA", ["signal_board"]);
  });

  it("has no strategy picker without a symbol, and a full-screen toggle", async () => {
    const { DeskChart } = await import("@/components/desk/DeskChart");
    render(<DeskChart historical={bars} />);
    expect(screen.queryByRole("button", { name: /Strategies/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByRole("dialog", { name: "Full-screen chart" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
