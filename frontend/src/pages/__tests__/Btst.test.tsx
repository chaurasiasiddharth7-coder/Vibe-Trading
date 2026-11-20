import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { Btst } from "../Btst";

const apiMock = vi.hoisted(() => ({
  btstToday: vi.fn(), btstDays: vi.fn(), btstStats: vi.fn(), btstDay: vi.fn(), btstJob: vi.fn(),
  btstScan: vi.fn(), btstSettle: vi.fn(), btstReplay: vi.fn(), getUniverses: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

const warn = { key: "too_hot", label: "Up too much today", detail: "Up 14.0% today.", big_loss_flagged_pct: 11, big_loss_clean_pct: 4.1 };
const cand = (symbol: string, rank: number, gain: number, warnings = [] as typeof warn[], chosen = false) => ({
  symbol, rank, gain_pct: gain, last: 250, prev_close: 235, open: 236, high: 252, low: 234, traded_cr: 48, turn_x: 3.1, ret5_pct: 2, ret20_pct: 6,
  from_high_pct: -1.5, vol20_pct: 2.4, gap_pct: 0.4, off_high_pct: -0.8, warnings, chosen,
});
const rules = { target_pct: 0.01, stop_pct: 0.02, use_stop: true, cost_pct: 0.0025, candidates: 20, scan_time: "15:20", min_traded_cr: 10, min_price: 50 };
const record = {
  date: "2026-11-18", kind: "live", rules_version: "btst-v1", rules, status: "picked", scanned_at: "15:20", history_to: "2026-11-17",
  market: { breadth_pct: 58, liquid: 812 }, coverage: { sources: ["nse"], universe: 1100, covered: 990, quotes: 1050, notes: [] },
  left_out: { upper_band: 3, illiquid_today: 40, thin_history: 5, cheap: 2, small_move: 0, not_eq: 0 },
  candidates: [cand("HOTCO", 1, 14, [warn]), cand("ABC", 2, 6.4, [], true)],
  pick: { symbol: "ABC", entry: 250, target: 252.5, stop: 245, gain_pct: 6.4 }, skip_reason: null, notes: [], runs: [],
};
const zero = { trades: 0 };
const live = { kind: "live", days: 0, settled_days: 0, skipped_days: 0, picks: zero, picks_target_first: zero, picks_no_stop: zero, ambiguous: 0, exact: 0, by_year: [], first: null, last: null, candidates: { settled: 0, by_warning_count: [], by_warning: [] } };
const replay = {
  ...live, kind: "replay", days: 900, settled_days: 900, skipped_days: 230, ambiguous: 221, first: "2023-11-01", last: "2026-11-13",
  picks: { trades: 670, hit_rate: 0.56, win_rate: 0.56, avg_net_pct: -0.3, median_net_pct: 0.75, big_loss_rate: 0, worst_pct: -4.7, best_pct: 9, total_pct: -200 },
  picks_target_first: { trades: 670, avg_net_pct: 0.35, hit_rate: 0.78 }, picks_no_stop: { trades: 670, avg_net_pct: 0.2, big_loss_rate: 0.025, worst_pct: -8.1 },
  by_year: [{ year: "2025", trades: 140, avg_net_pct: -0.33, win_rate: 0.7, avg_target_first_pct: 0.1, avg_no_stop_pct: -0.2 }],
  candidates: { settled: 13000, by_warning_count: [], by_warning: [{ key: "too_hot", label: "Up too much today", flagged: { trades: 2000, big_loss_rate: 0.09, avg_net_pct: -0.4 }, clean: { trades: 11000, big_loss_rate: 0.036 } }] },
};
const schedule = { enabled: false, thread_alive: false, scan_time: "15:20", settle_time: "15:40", timezone: "Asia/Kolkata", last: { scan: null, settle: null }, how_to_enable: "x", cron: "CRON_TZ=Asia/Kolkata\n20 15 * * 1-5 ..." };

function mount() {
  return render(<StrictMode><MemoryRouter><Btst /></MemoryRouter></StrictMode>);
}

describe("Overnight (BTST) page", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    try { localStorage.clear(); } catch { /* ignore */ }
    apiMock.getUniverses.mockResolvedValue({});
    apiMock.btstToday.mockResolvedValue({ ok: true, data: record, schedule, job: null, store_last: "2026-11-17" });
    apiMock.btstDays.mockResolvedValue({ ok: true, data: [{ date: "2026-11-18", kind: "live", status: "picked", symbol: "ABC", gain_pct: 6.4, entry: 250, net_pct: null, reason: null, ambiguous: false, candidates: 2, skip_reason: null, breadth_pct: 58 }] });
    apiMock.btstStats.mockResolvedValue({ ok: true, data: { live, replay } });
  });

  it("shows the pick, its exit levels and the broker steps", async () => {
    mount();
    expect(await screen.findByRole("article", { name: /Pick for 2026-11-18: ABC/ })).toBeInTheDocument();
    expect(screen.getByText("₹252.50")).toBeInTheDocument();
    expect(screen.getByText("₹245.00")).toBeInTheDocument();
    expect(screen.getByText(/Buy 400 ABC now/)).toBeInTheDocument();
    expect(screen.getByText(/GTT OCO for tomorrow/)).toBeInTheDocument();
    expect(screen.getByText(/No live trades yet/)).toBeInTheDocument();
    expect(screen.getByText(/Scanned 15:20 · 812 liquid stocks, 58% up/)).toBeInTheDocument();
  });

  it("lists candidates with their warnings and explains one when opened", async () => {
    mount();
    const row = await screen.findByRole("button", { name: /HOTCO/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/11% of stocks with this warning lost 5% or more/)).toBeInTheDocument();
    expect(screen.getByText(/Left out before ranking: 3 stuck at the upper price band/)).toBeInTheDocument();
  });

  it("keeps live and replayed records apart and shows the stop range honestly", async () => {
    mount();
    expect(await screen.findByText(/No settled live trades yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /Replayed/ }));
    expect(await screen.findByText(/On 221 days the next day's prices touched both/)).toBeInTheDocument();
    expect(screen.getByText("\u22120.30% to +0.35%")).toBeInTheDocument();
    expect(screen.getByText("Same picks, no stop")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /By year/ })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /How the warnings did/ })).toBeInTheDocument();
  });

  it("recomputes the shares when the money per trade changes", async () => {
    mount();
    const input = await screen.findByLabelText("Money per trade");
    fireEvent.change(input, { target: { value: "50000" } });
    expect(screen.getByText(/Buy 200 ABC now/)).toBeInTheDocument();
  });

  it("shows a plain message when nothing qualified", async () => {
    apiMock.btstToday.mockResolvedValue({ ok: true, data: { ...record, status: "skipped", pick: null, skip_reason: "Every candidate has at least one warning. The rules skip days like this.", candidates: [cand("HOTCO", 1, 14, [warn])] }, schedule, job: null, store_last: null });
    mount();
    expect(await screen.findByText(/No trade for 2026-11-18/)).toBeInTheDocument();
    expect(screen.getByText(/The rules skip days like this/)).toBeInTheDocument();
  });
});
