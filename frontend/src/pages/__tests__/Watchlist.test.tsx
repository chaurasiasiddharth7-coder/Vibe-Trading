import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  lists: vi.fn(),
  add: vi.fn(),
  remove: vi.fn(),
  rename: vi.fn(),
  quotes: vi.fn(),
  search: vi.fn(),
}));
const brief = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/watchlistApi", async (orig) => ({ ...(await orig<typeof import("@/lib/watchlistApi")>()), watchlistApi: api, briefApi: brief }));

import { Watchlist } from "@/pages/Watchlist";

const lists = (items: string[]) => [{ name: "Watchlist 1", items }, ...[2, 3, 4, 5].map((n) => ({ name: `Watchlist ${n}`, items: [] }))];

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  sessionStorage.clear();
  sessionStorage.setItem("wl-tab", "0");
  api.lists.mockResolvedValue({ lists: lists(["RBA", "LT"]), max_items: 250 });
  api.quotes.mockResolvedValue({
    live: true, source: "kite", at: 1_700_000_000, indices: [],
    quotes: {
      RBA: { symbol: "RBA", exchange: "NSE", ltp: 89.84, prev_close: 89.38, change: 0.46, change_pct: 0.51 },
      LT: { symbol: "LT", exchange: "NSE", ltp: 4209.1, prev_close: 4211, change: -1.9, change_pct: -0.04 },
    },
  });
  api.search.mockResolvedValue({ results: [{ symbol: "RELIANCE", exchange: "NSE" }] });
  api.add.mockResolvedValue({ lists: lists(["RBA", "LT", "RELIANCE"]) });
});

const renderIt = () => render(<MemoryRouter><Watchlist /></MemoryRouter>);

describe("Watchlist", () => {
  it("shows live prices coloured by direction", async () => {
    renderIt();
    expect(await screen.findByText("89.84")).toHaveClass("text-success");
    expect(screen.getByText("4,209.10")).toHaveClass("text-danger");
    expect(screen.getByText("+0.46 (+0.51%)")).toBeInTheDocument();
    expect(screen.getByText(/Live from Kite/)).toBeInTheDocument();
    expect(screen.getByText("2/250")).toBeInTheDocument();
    expect(api.quotes).toHaveBeenCalledWith(["RBA", "LT"], false);
  });

  it("says when prices are end of day", async () => {
    api.quotes.mockResolvedValue({ live: false, source: "nse_eod", at: 1, indices: [], quotes: { RBA: { symbol: "RBA", exchange: "NSE", ltp: 89, prev_close: 88, change: 1, change_pct: 1.1, as_of: "2026-11-19" } } });
    renderIt();
    expect(await screen.findByText(/End-of-day prices · 2026-11-19/)).toBeInTheDocument();
  });

  it("searches and adds a stock", async () => {
    renderIt();
    await screen.findByText("RBA");
    await userEvent.type(screen.getByLabelText("Search and add a stock"), "REL");
    await userEvent.click(await screen.findByRole("button", { name: /RELIANCE/ }));
    await waitFor(() => expect(api.add).toHaveBeenCalledWith(0, "RELIANCE"));
    expect(await screen.findByText("3/250")).toBeInTheDocument();
  });

  it("edit mode removes a stock", async () => {
    api.remove.mockResolvedValue({ lists: lists(["LT"]) });
    renderIt();
    await screen.findByText("RBA");
    await userEvent.click(screen.getByLabelText("Edit this watchlist"));
    await userEvent.click(screen.getByLabelText("Remove RBA"));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(0, "RBA"));
  });

  it("opens on the Today brief: insight, picks and news", async () => {
    sessionStorage.clear();
    brief.get.mockResolvedValue({
      generated_at: "2026-11-20T10:00:00+05:30",
      status: { state: "open", label: "Market open" },
      live: true,
      indices: [],
      insight: { bullets: ["Nifty 50 is up +1.12% today."], by: "rules" },
      breadth: { source: "kite", as_of: "10:00", universe: "Nifty 500", advancers: 350, decliners: 120, unchanged: 30,
        gainers: [{ symbol: "TCS", ltp: 4000, change_pct: 3.2 }], losers: [{ symbol: "INFY", ltp: 1500, change_pct: -2.1 }],
        sectors_up: [{ sector: "Banks", change_pct: 1.4 }], sectors_down: [{ sector: "IT", change_pct: -0.8 }] },
      picks: { rows: [{ symbol: "LT", score: 4, price: 4209, change_percent: 0.5, entry: 4209, stop: 4100, target: 4400, risk_pct: 2, kronos_change_pct: null, reasons: ["Above 50-day average"] }],
        scanned_at: new Date().toISOString(), universe: "nifty200", longs: 9 },
      news: [{ title: "RBI keeps repo rate unchanged", url: "https://x.test/a", source: "ET", sources: ["ET", "Mint"], ts: Date.now() / 1000 - 600, watchlist: ["LT"], important: true }],
      disclaimer: "Paper-trading ideas from the desk's own signals, not investment advice.",
    });
    renderIt();
    expect(await screen.findByText("Nifty 50 is up +1.12% today.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Today" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Top picks for the day")).toBeInTheDocument();
    expect(screen.getByText("LT")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /RBI keeps repo rate unchanged/ })).toHaveAttribute("href", "https://x.test/a");
    expect(screen.getByText("On your watchlist: LT")).toBeInTheDocument();
    expect(screen.getByText("350 up")).toBeInTheDocument();
    expect(api.quotes).not.toHaveBeenCalledWith(["RBA", "LT"], false);
  });
});
