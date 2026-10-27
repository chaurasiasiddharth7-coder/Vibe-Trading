import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Intraday } from "../Intraday";
import { shiftWeekday, todayIst, lastWeekday } from "@/lib/intraday";

const apiMock = vi.hoisted(() => ({
  intradaySettings: vi.fn(),
  intradaySchedule: vi.fn(),
  intradayDay: vi.fn(),
  intradayDays: vi.fn(),
  intradayStats: vi.fn(),
  intradayRun: vi.fn(),
  intradayBackfill: vi.fn(),
  intradaySaveSettings: vi.fn(),
  getUniverses: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

const day = lastWeekday(todayIst());
const trade = {
  id: `${day}-TCS-long`, symbol: "TCS", side: "long", tags: ["nifty50"], decided_at: "09:35", entry: 2530, stop: 2510, target: 2570,
  risk_per_share: 20, risk_pct: 0.79, reward_risk: 2, qty: 50, status: "target", exit_time: "10:20", last_price: 2570, last_time: "10:20",
  r_gross: 2, r_net: 1.8, pnl_inr: 1800, mfe_r: 2.1, mae_r: -0.3, range: { high: 2528, low: 2512 }, volume_x: 2.3,
  path: [[575, 0], [580, 0.8], [585, -0.2], [620, 2]],
};
const record = {
  date: day, weekday: "Monday", status: "settled", settings: {}, universe: { key: "nifty50", label: "Nifty 50", eligible: 50, covered: 50, charts_read: 50, liquid: 50, data_source: "nse_store" },
  planned: [{ symbol: "TCS", price: 2530, traded_value_cr: 900, setups: ["nr7"], volume_x: 1.1, tags: ["nifty50"] }],
  trades: [trade], skipped: [{ symbol: "INFY", side: "short", decided_at: "09:40", reason: "the day's cap of 5 trades was already used" }],
  not_triggered: ["ITC"], data_gaps: [],
  day: { type: "trend_up", label: "Trend up", rule: "Trend up/down: ...", nifty50: { type: "trend_up", label: "Trend up", metrics: { open_to_close_pct: 0.86, range_pct: 1.49 } }, niftybank: { type: "range", label: "Range day", metrics: { open_to_close_pct: 0.1, range_pct: 0.8 } } },
  summary: { taken: 1, open: 0, wins: 1, losses: 0, flat: 0, win_rate: 1, net_r: 1.8, gross_r: 2, net_inr: 1800, best: { symbol: "TCS", pnl_inr: 1800 }, worst: { symbol: "TCS", pnl_inr: 1800 } },
  as_of: "15:15", runs: [{ kind: "close", at: "2026-10-26T15:40:00", ok: true, note: "settled", source: "scheduler" }], note: "Paper trades simulated.",
};
const row = { date: day, weekday: "Monday", status: "settled", type: "trend_up", label: "Trend up", planned: 50, taken: 1, wins: 1, losses: 0, flat: 0, win_rate: 1, net_r: 1.8, net_inr: 1800, rules_match: true };
const stats = {
  days: 2, mixed_rules: 0, series: [
    { date: shiftWeekday(day, -1), trades: 1, wins: 0, cum_trades: 1, cum_wins: 0, cum_win_rate: 0, cum_net_r: -1, cum_net_inr: -1000 },
    { date: day, trades: 1, wins: 1, cum_trades: 2, cum_wins: 1, cum_win_rate: 0.5, cum_net_r: 0.8, cum_net_inr: 800 }],
  by_type: [{ key: "Trend up", trades: 1, wins: 1, win_rate: 1, avg_r: 1.8, net_inr: 1800, days: 1 }], by_side: [{ key: "long", trades: 1, wins: 1, win_rate: 1, avg_r: 1.8, net_inr: 1800, days: null }], by_index: [],
};
const settings = { universe: "nifty50", sides: "both", max_trades: 5, risk_inr: 1000, or_minutes: 15, stop: "or-mid", target: "2R", min_vol_x: null, market_filter: "off", max_candidates: 60, flat_by: "15:15", open_time: "09:15", close_time: "15:40", live_refresh_minutes: 0 };
const schedule = { enabled: false, thread_alive: false, open_time: "09:15", close_time: "15:40", live_refresh_minutes: 0, timezone: "Asia/Kolkata", market: "closed", last: { open: null, close: null }, next: null, how_to_enable: "Set INTRADAY_SCHEDULER=1", cron: "CRON_TZ=Asia/Kolkata\n15 9 * * 1-5 ..." };

const tradeRow = () => screen.findByRole("button", { name: /^TCS Buy/ });

function mount() {
  return render(<StrictMode><MemoryRouter><Intraday /></MemoryRouter></StrictMode>);
}

describe("Intraday page", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    apiMock.getUniverses.mockResolvedValue({});
    apiMock.intradaySettings.mockResolvedValue({ ok: true, data: settings, universes: { nifty50: "Nifty 50", niftybank: "Nifty Bank" } });
    apiMock.intradaySchedule.mockResolvedValue({ ok: true, data: schedule });
    apiMock.intradayDay.mockResolvedValue({ ok: true, data: record, running: null });
    apiMock.intradayDays.mockResolvedValue({ ok: true, data: [row] });
    apiMock.intradayStats.mockResolvedValue({ ok: true, data: stats });
  });

  it("shows the day, its trades and the history, even under StrictMode's double mount", async () => {
    mount();
    expect(await tradeRow()).toBeInTheDocument();
    expect(screen.getByText("Hit take-profit at 10:20")).toBeInTheDocument();
    expect(screen.getAllByText("+₹1,800").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Trend up").length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: /Cumulative win ratio 50% of 2 trades over 2 days/ })).toBeInTheDocument();
    expect(screen.getByText(/Skipped by the rules \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Opening scan 09:15, closing settle 15:40 IST · off/)).toBeInTheDocument();
  });

  it("expands a trade to the plain numbers behind it", async () => {
    mount();
    const row = await tradeRow();
    expect(row).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("+₹2,000 / −₹1,000")).toBeInTheDocument(); // 50 shares: Rs 40 to take-profit, Rs 20 to cut-loss
  });

  it("opens another day from the day-by-day list", async () => {
    const earlier = shiftWeekday(day, -1);
    apiMock.intradayDays.mockResolvedValue({ ok: true, data: [row, { ...row, date: earlier }] });
    mount();
    await tradeRow();
    apiMock.intradayDay.mockClear();
    const buttons = screen.getAllByRole("button", { pressed: false });
    fireEvent.click(buttons.find((b) => b.className.includes("grid-cols-[6.5rem_1fr_auto]")) as HTMLElement);
    await waitFor(() => expect(apiMock.intradayDay).toHaveBeenCalledWith(earlier));
  });

  it("offers a replay for a past day and plan/refresh/close only for today", async () => {
    mount();
    await tradeRow();
    const today = todayIst();
    if (day === today) {
      expect(screen.getByRole("button", { name: "Plan open" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Previous day" }));
    } else {
      expect(screen.queryByRole("button", { name: "Plan open" })).toBeNull();
    }
    expect(await screen.findByRole("button", { name: "Replay this day" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Close and settle" })).toBeNull();
  });

  it("says what to do when a day has no record, and offers to fill history when there is none", async () => {
    apiMock.intradayDay.mockResolvedValue({ ok: true, data: null, running: null });
    apiMock.intradayDays.mockResolvedValue({ ok: true, data: [] });
    apiMock.intradayStats.mockResolvedValue({ ok: true, data: { days: 0, series: [], by_type: [], by_side: [], by_index: [] } });
    mount();
    expect(await screen.findByText(/No record for this day yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fill the last 30 days/ })).toBeInTheDocument();
    expect(screen.getByText("Result so far").nextSibling).toHaveTextContent("—"); // never a result before data exists
  });

  it("reports a failed load in plain words", async () => {
    apiMock.intradaySettings.mockRejectedValue(new TypeError("Failed to fetch"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Couldn't reach the server/);
  });
});

describe("Intraday page: rule consistency", () => {
  it("warns when days were filed under different rules and offers a refile", async () => {
    vi.resetAllMocks();
    apiMock.getUniverses.mockResolvedValue({});
    apiMock.intradaySettings.mockResolvedValue({ ok: true, data: settings, universes: { nifty50: "Nifty 50" } });
    apiMock.intradaySchedule.mockResolvedValue({ ok: true, data: schedule });
    apiMock.intradayDay.mockResolvedValue({ ok: true, data: record, running: null });
    apiMock.intradayDays.mockResolvedValue({ ok: true, data: [{ ...row, rules_match: false }] });
    apiMock.intradayStats.mockResolvedValue({ ok: true, data: { ...stats, mixed_rules: 1 } });
    apiMock.intradayBackfill.mockResolvedValue({ ok: true, started: "backfill", days: 90 });
    apiMock.intradayDay.mockResolvedValueOnce({ ok: true, data: record, running: null });
    mount();
    expect(await screen.findByText(/1 of 2 days were filed under different rules/)).toBeInTheDocument();
    expect(screen.getByText(/other rules/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refile all days with today's rules" }));
    await waitFor(() => expect(apiMock.intradayBackfill).toHaveBeenCalledWith(90, true));
  });
});
