import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PaperTrade } from "@/lib/api";
import { DEFAULT_PREFS, DeskPrefsProvider, type DeskPrefs } from "@/lib/deskPrefs";
import { PracticeTrades } from "../PracticeTrades";

const apiMock = vi.hoisted(() => ({ getMe: vi.fn(), putMyPrefs: vi.fn(), getAlphadeskAnalysis: vi.fn(), getKronosForecast: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/components/desk/DeskChart", () => ({ DeskChart: () => <div data-testid="chart" /> }));

const trade: PaperTrade = {
  id: "t1", symbol: "ACME", side: "LONG", entry: 100, stop: 97, target: 106, qty: 10, risk_inr: 30,
  opened_at: "2026-10-13T00:00:00Z", opened_bar: "2026-10-13", status: "OPEN",
  current_price: 103, unrealized_inr: 30, stop_now: 97, plan: "fixed",
  suggestion: { action: "hold", text: "Nothing to change." },
};

async function show(prefs: Partial<DeskPrefs>) {
  apiMock.getMe.mockResolvedValue({ ok: true, data: { id: "ayushi", name: "Ayushi", is_owner: false, prefs: { ...DEFAULT_PREFS, ...prefs } } });
  render(
    <DeskPrefsProvider>
      <PracticeTrades trades={[trade]} busy={false} planBusy={null} onUpdate={vi.fn()} onChangePlan={vi.fn()} onMoveStop={vi.fn()} onExit={vi.fn()} />
    </DeskPrefsProvider>,
  );
  await screen.findByText("ACME");
  await new Promise((r) => setTimeout(r, 20));
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe("PracticeTrades with a saved view", () => {
  it("shows every row and filter by default", async () => {
    await show({});
    for (const l of ["Bought at", "Target", "Cut-loss", "Qty"]) expect(screen.getByText(l)).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Filter trades" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Filter by date invested" })).toBeInTheDocument();
  });

  it("hides the information rows that were unticked", async () => {
    await show({ hidden_rows: ["price_target", "price_cutloss", "footnote", "trades_summary"] });
    expect(screen.queryByText("Target")).not.toBeInTheDocument();
    expect(screen.queryByText("Cut-loss")).not.toBeInTheDocument();
    expect(screen.getByText("Bought at")).toBeInTheDocument();
    expect(screen.queryByText(/Open results are on paper/)).not.toBeInTheDocument();
    expect(screen.queryByText("Open trades so far")).not.toBeInTheDocument();
  });

  it("hides the filters that were unticked", async () => {
    await show({ hidden_filters: ["dates", "status", "day_report", "group_by_date", "expand_all"] });
    expect(screen.queryByRole("group", { name: "Filter by date invested" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^All \(/ })).not.toBeInTheDocument();
    expect(screen.getByText("ACME")).toBeInTheDocument();
  });
});
