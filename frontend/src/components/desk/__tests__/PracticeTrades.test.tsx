import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PaperTrade } from "@/lib/api";
import { PracticeTrades } from "../PracticeTrades";

const apiMock = vi.hoisted(() => ({ getAlphadeskAnalysis: vi.fn(), getKronosForecast: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/components/desk/DeskChart", () => ({ DeskChart: () => <div data-testid="chart" /> }));

const base: PaperTrade = {
  id: "t1", symbol: "ACME", side: "LONG", entry: 100, stop: 97, target: 106, qty: 10, risk_inr: 30,
  opened_at: "2026-10-13T00:00:00Z", opened_bar: "2026-10-13", status: "OPEN",
  current_price: 103, unrealized_inr: 30, stop_now: 97, plan: "fixed",
  suggestion: { action: "hold", text: "Nothing to change." },
};

function setup(trades: PaperTrade[] = [base]) {
  const props = {
    trades, busy: false, planBusy: null,
    onUpdate: vi.fn(), onChangePlan: vi.fn(), onMoveStop: vi.fn(), onExit: vi.fn(),
  };
  render(<PracticeTrades {...props} />);
  return props;
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe("PracticeTrades actions", () => {
  it("only offers the menu on open trades", () => {
    setup([base, { ...base, id: "t2", symbol: "DONE", status: "CLOSED", exit_price: 99, pnl_inr: -10 }]);
    expect(screen.getAllByRole("button", { name: /Actions for/ })).toHaveLength(1);
  });

  it("exit now needs a confirmation", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "Actions for ACME" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Exit now/ }));
    expect(p.onExit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Yes, close it" }));
    expect(p.onExit).toHaveBeenCalledWith("t1");
  });

  it("only accepts a cut-loss between the current stop and the price", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "Actions for ACME" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Move cut-loss/ }));
    const input = screen.getByLabelText("New cut-loss price");
    const submit = screen.getByRole("button", { name: "Move it" });
    await userEvent.clear(input);
    await userEvent.type(input, "90");
    expect(submit).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "103.5");
    expect(submit).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "100");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(p.onMoveStop).toHaveBeenCalledWith("t1", 100);
  });

  it("changes exit style and closes on Escape", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "Actions for ACME" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /Trailing cut-loss/ }));
    expect(p.onChangePlan).toHaveBeenCalledWith("t1", "trail");
    await userEvent.click(screen.getByRole("button", { name: "Actions for ACME" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });
});

describe("PracticeTrades glance and review", () => {
  it("opens the glance panel with the suggested exit", async () => {
    apiMock.getAlphadeskAnalysis.mockResolvedValue({
      data: { symbol: "ACME", ticker: "ACME.NS", current_price: 103, change_percent: 1.2, metrics: { rsi_14: 55, atr: 3, ema_50: 99 },
        chart_data: [{ time: "d1", open: 100, high: 105, low: 99, close: 103 }] },
    });
    setup();
    await userEvent.click(screen.getByRole("button", { name: "ACME at a glance" }));
    expect(await screen.findByTestId("chart")).toBeInTheDocument();
    expect(screen.getByText("Suggested exit")).toBeInTheDocument();
    expect(screen.getByText("Nothing to change.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Analysis" })).toBeInTheDocument();
    expect(screen.getByText(/Leans|No clear lean/)).toBeInTheDocument();
    expect(screen.getByText("Candle analysis")).toBeInTheDocument();
    expect(screen.getByText("AI forecast")).toBeInTheDocument();
  });

  it("runs a fresh forecast on demand and reports the expected move", async () => {
    apiMock.getAlphadeskAnalysis.mockResolvedValue({
      data: { symbol: "ACME", ticker: "ACME.NS", current_price: 103, change_percent: 1.2, metrics: { rsi_14: 55 },
        chart_data: [{ time: "d1", open: 100, high: 105, low: 99, close: 103 }] },
    });
    apiMock.getKronosForecast.mockResolvedValue({
      data: { forecast_data: [], kronos_projection: { expected_change_percent: 4.2, projected_high: 110, projected_low: 101, trajectory_bias: "BULLISH", model: "k" } },
    });
    setup();
    await userEvent.click(screen.getByRole("button", { name: "ACME at a glance" }));
    await userEvent.click(await screen.findByRole("button", { name: "Run fresh forecast" }));
    expect(apiMock.getKronosForecast).toHaveBeenCalledWith("ACME", 400, 15, true);
    expect(await screen.findByText(/Expected move over the next 15 trading days: \+4\.2%/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
  });

  it("shows a calm error when the forecast fails", async () => {
    apiMock.getAlphadeskAnalysis.mockResolvedValue({
      data: { symbol: "ACME", ticker: "ACME.NS", current_price: 103, change_percent: 0, metrics: {}, chart_data: [{ time: "d1", open: 100, high: 105, low: 99, close: 103 }] },
    });
    apiMock.getKronosForecast.mockRejectedValue(new Error("model unavailable"));
    setup();
    await userEvent.click(screen.getByRole("button", { name: "ACME at a glance" }));
    await userEvent.click(await screen.findByRole("button", { name: "Run fresh forecast" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("model unavailable");
  });

  it("shows what was learned, the short-selling note and data warnings on a finished trade", async () => {
    setup([{
      ...base, id: "t3", symbol: "LOSER", side: "SHORT", status: "CLOSED", exit_price: 105, pnl_inr: -50,
      exit_reason: "stop", data_warning: { date: "2026-10-16", change_pct: -20.8 },
      review: { best_move_r: 0.6, worst_move_r: 1.1, what_happened: "It reversed.", lesson: "Trail it.",
        if_exit_style: { fixed: -1, breakeven: -1, trail: -0.2, trail_run: -0.5 }, best_style: "trail" },
    }]);
    await userEvent.click(screen.getByRole("button", { name: /LOSER at a glance/ }).parentElement!.querySelectorAll("button")[1] as HTMLElement);
    expect(screen.getByText("What we learned")).toBeInTheDocument();
    expect(screen.getByText(/It reversed\./)).toBeInTheDocument();
    expect(screen.getByText(/must be closed the same day/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("21%");
  });
});
