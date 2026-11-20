import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { normalizePrefs } from "@/lib/deskPrefs";
import type { Catalogue, StrategyRow } from "@/lib/strategiesApi";

const session = { me: { id: null, name: "Owner", is_owner: true, role: "owner", prefs: normalizePrefs(null) } as unknown, prefs: normalizePrefs(null) };
vi.mock("@/lib/session", () => ({ useSession: () => session }));

const gate = [
  { label: "Paper-only rule (19 Nov) lifted by you in writing", ok: false },
  { label: "Backtest gate passed", ok: true },
];

function row(over: Partial<StrategyRow>): StrategyRow {
  return {
    id: "btst",
    name: "Overnight (BTST) pick",
    kind: "built-in",
    profile: null,
    profile_name: "You (owner)",
    paper: true,
    live: false,
    live_locked: true,
    rules_text: ["One pick at 15:20"],
    paper_stats: { trades: 12, win_rate: 58.3, avg_pct: 0.31, pnl_inr: 2400 },
    gate,
    ...over,
  };
}

const catalogue: Catalogue = {
  strategies: [
    row({}),
    row({ id: "lab_1", name: "RSI dip", kind: "lab", profile: "ayushi", profile_name: "Ayushi", paper: false, paper_stats: { trades: 0, open: 1 }, last_test: null }),
  ],
  live_locked_reason: "Live trading is locked. On 19 Nov 2026 you set the rule.",
  conditions: { rsi_below: { params: { n: [2, 50, 14], level: [1, 99, 30] }, text: "RSI({n}) below {level}" }, breakout_high: { params: { n: [5, 250, 20] }, text: "Close above the highest high of the last {n} days" }, above_sma: { params: { n: [5, 250, 50] }, text: "Close above its {n}-day average" } },
  exit_limits: { stop_pct: [0.2, 30], target_pct: [0.2, 100], stop_atr: [0.3, 10], target_atr: [0.3, 20], trail_atr: [0.5, 10], max_hold_days: [1, 120] },
};

describe("Strategies page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    session.me = { id: null, name: "Owner", is_owner: true, role: "owner", prefs: normalizePrefs(null) };
  });

  it("lists every profile's strategies, shows live as locked, and opens the gate checklist", async () => {
    const api = await import("@/lib/strategiesApi");
    vi.spyOn(api.strategiesApi, "list").mockResolvedValue({ ok: true, data: catalogue });
    const { Strategies } = await import("@/pages/Strategies");
    render(<Strategies />);
    expect(await screen.findByText("RSI dip")).toBeInTheDocument();
    expect(screen.getAllByText(/Ayushi/).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /Live trading for .* is locked/ })).toHaveLength(2);
    expect(screen.getByText(/Live trading is locked\. On 19 Nov/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /Live trading for Overnight/ })[0]);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Paper-only rule \(19 Nov\) lifted/)).toBeInTheDocument();
  });

  it("the paper switch calls the API for that profile's strategy", async () => {
    const api = await import("@/lib/strategiesApi");
    vi.spyOn(api.strategiesApi, "list").mockResolvedValue({ ok: true, data: catalogue });
    const paper = vi.spyOn(api.strategiesApi, "paper").mockResolvedValue({ ok: true, data: { id: "lab_1", paper: true } });
    const { Strategies } = await import("@/pages/Strategies");
    render(<Strategies />);
    const sw = await screen.findByRole("switch", { name: "Paper trading for RSI dip" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sw);
    await waitFor(() => expect(paper).toHaveBeenCalledWith("lab_1", true, "ayushi"));
  });

  it("shows the server's refusal when a lab strategy hasn't been tested", async () => {
    const { ApiError } = await import("@/lib/api");
    const api = await import("@/lib/strategiesApi");
    vi.spyOn(api.strategiesApi, "list").mockResolvedValue({ ok: true, data: catalogue });
    vi.spyOn(api.strategiesApi, "paper").mockRejectedValue(new ApiError("Test it on past prices once before paper trading it.", 400));
    const { Strategies } = await import("@/pages/Strategies");
    render(<Strategies />);
    await userEvent.click(await screen.findByRole("switch", { name: "Paper trading for RSI dip" }));
    expect(await screen.findByText(/Test it on past prices once/)).toBeInTheDocument();
  });

  it("viewers can't flip switches or add strategies", async () => {
    session.me = { id: "v", name: "Vera", is_owner: false, role: "viewer", prefs: normalizePrefs(null) };
    const api = await import("@/lib/strategiesApi");
    vi.spyOn(api.strategiesApi, "list").mockResolvedValue({ ok: true, data: { ...catalogue, strategies: [row({ profile: "v", profile_name: "You" })] } });
    const { Strategies } = await import("@/pages/Strategies");
    render(<Strategies />);
    const sw = await screen.findByRole("switch", { name: /Paper trading for/ });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("button", { name: /New strategy/ })).toBeNull();
  });

  it("the builder drafts from an import but saves only on Save", async () => {
    const api = await import("@/lib/strategiesApi");
    vi.spyOn(api.strategiesApi, "list").mockResolvedValue({ ok: true, data: catalogue });
    const draft = vi.spyOn(api.strategiesApi, "importDraft").mockResolvedValue({
      ok: true,
      data: { name: "Imported dip", rules: { side: "LONG", entry: [{ kind: "rsi_below", n: 2, level: 10 }], exit: { stop_pct: 3, max_hold_days: 5 }, universe: "nifty50", symbols: null }, problem: null, unsupported: ["exit when RSI > 70"], source_url: null, rules_text: [] },
    });
    const create = vi.spyOn(api.strategiesApi, "create").mockResolvedValue({ ok: true, data: { id: "lab_9" } });
    const { Strategies } = await import("@/pages/Strategies");
    render(<Strategies />);
    await userEvent.click(await screen.findByRole("button", { name: /New strategy/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByText("Import one you found online"));
    await userEvent.type(within(dialog).getByPlaceholderText(/paste the strategy/), "Buy when 2-day RSI is under 10, exit on RSI over 70");
    await userEvent.click(within(dialog).getByRole("button", { name: "Draft the rules" }));
    expect(await within(dialog).findByText(/Not covered by the rule builder: exit when RSI > 70/)).toBeInTheDocument();
    expect(draft).toHaveBeenCalled();
    expect(create).not.toHaveBeenCalled();
    expect(within(dialog).getByDisplayValue("Imported dip")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Save strategy" }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    const body = create.mock.calls[0][0];
    expect(body.rules.entry).toEqual([{ kind: "rsi_below", n: 2, level: 10 }]);
  });
});
