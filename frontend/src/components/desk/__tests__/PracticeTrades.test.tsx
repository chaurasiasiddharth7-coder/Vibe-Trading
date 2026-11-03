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


describe("PracticeTrades date invested", () => {
  const mk = (id: string, symbol: string, opened_at: string, extra: Partial<PaperTrade> = {}): PaperTrade => ({
    ...base, id, symbol, opened_at, opened_bar: opened_at.slice(0, 10), ...extra,
  });
  const three = [
    mk("a", "AAA", "2026-10-13T05:00:00Z"),
    mk("b", "BBB", "2026-10-14T05:00:00Z"),
    mk("c", "CCC", "2026-10-14T06:00:00Z", { status: "CLOSED", exit_price: 106, exit_reason: "target", r_multiple: 2, pnl_inr: 60, closed_at: "2026-10-16T05:00:00Z" }),
  ];
  const symbols = () => screen.queryAllByRole("button").map((b) => b.getAttribute("aria-label") ?? "").filter((l) => l.endsWith("at a glance")).map((l) => l.split(" ")[0]);
  const from = () => screen.getByLabelText("Invested from") as HTMLInputElement;
  const to = () => screen.getByLabelText("to") as HTMLInputElement;

  it("lists each date under its own heading with that date's result", () => {
    setup(three);
    expect(screen.getByRole("region", { name: /Invested .*14/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /Invested .*13/ })).toBeInTheDocument();
    const day14 = screen.getByRole("region", { name: /Invested .*14/ });
    expect(day14).toHaveTextContent("2 trades");
    expect(symbols()).toEqual(["BBB", "CCC", "AAA"]); // newest date first
  });

  it("filters by the date invested and says how many are shown", () => {
    setup(three);
    fireEvent.change(from(), { target: { value: "2026-10-14" } });
    expect(symbols()).toEqual(["BBB", "CCC"]);
    expect(screen.getByText(/Showing 2 of 3 trades invested from/)).toBeInTheDocument();
    fireEvent.change(to(), { target: { value: "2026-10-14" } });
    expect(symbols()).toEqual(["BBB", "CCC"]);
    fireEvent.change(from(), { target: { value: "2026-10-15" } });
    fireEvent.change(to(), { target: { value: "" } });
    expect(symbols()).toEqual([]);
    expect(screen.getByText(/No practice trades were invested in those dates/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear dates" }));
    expect(symbols()).toEqual(["BBB", "CCC", "AAA"]);
  });

  it("combines with the open / finished filter", () => {
    setup(three);
    fireEvent.change(from(), { target: { value: "2026-10-14" } });
    fireEvent.click(screen.getByRole("button", { name: /Finished/ }));
    expect(symbols()).toEqual(["CCC"]);
  });

  it("ignores a backwards range and says so", () => {
    setup(three);
    fireEvent.change(from(), { target: { value: "2026-10-15" } });
    fireEvent.change(to(), { target: { value: "2026-10-13" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/after the "to" date/);
    expect(symbols()).toHaveLength(3);
  });

  it("can go back to one plain list", () => {
    setup(three);
    fireEvent.click(screen.getByLabelText("List by date invested"));
    expect(screen.queryByRole("region", { name: /Invested/ })).toBeNull();
    expect(symbols()).toHaveLength(3);
  });
});

describe("practice report", () => {
  const closed = (id: string, openedAt: string, exit: string, pnl: number): PaperTrade => ({
    ...base, id, symbol: id.toUpperCase(), opened_at: openedAt, status: "CLOSED",
    exit_date: exit, exit_reason: pnl > 0 ? "target" : "stop", pnl_inr: pnl, r_multiple: pnl / 30,
  });
  const ledger = [
    closed("won", "2026-10-13T05:00:00Z", "2026-10-14", 60),
    closed("lost", "2026-10-14T05:00:00Z", "2026-10-16", -30),
  ];

  it("shows the win rate with its count and the day by day list", () => {
    setup(ledger);
    expect(screen.getByText("50%", { selector: "span.font-mono" })).toBeInTheDocument();
    expect(screen.getByText(/won, of 2 finished trades over 2 days/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fri, 16 Oct|Fri 16 Oct/ })).toBeInTheDocument();
    expect(screen.getByText(/judge this on at least 30 finished trades/)).toBeInTheDocument();
  });

  it("pressing a day lists the trades invested that day, and pressing it again clears", async () => {
    setup(ledger);
    const day13 = screen.getByRole("button", { name: /13 Oct/ });
    await userEvent.click(day13);
    expect(screen.getByText(/Showing 1 of 2 trades invested/)).toBeInTheDocument();
    expect(day13).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(day13);
    expect(screen.queryByText(/Showing 1 of 2 trades invested/)).not.toBeInTheDocument();
  });

  it("says so when nothing has finished yet", () => {
    setup([base]);
    expect(screen.getByText(/No finished trades yet/)).toBeInTheDocument();
  });
});
