import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeskPrefs } from "@/lib/deskPrefs";
import { DEFAULT_PREFS } from "@/lib/deskPrefs";
import { Dashboard } from "../Dashboard";

const apiMock = vi.hoisted(() => ({ getMe: vi.fn(), putMyPrefs: vi.fn(), getUniverses: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/pages/Guide", async () => {
  const React = await import("react");
  return {
    Guide: ({ view, onTradesChange }: { view: string; onTradesChange: (t: unknown[], tr: unknown) => void }) => {
      React.useEffect(() => {
        onTradesChange(
          [
            { id: "1", symbol: "AAA", status: "OPEN", qty: 1, entry: 100, unrealized_inr: 500, opened_at: "2026-01-01T00:00:00Z" },
            { id: "2", symbol: "BBB", status: "CLOSED", qty: 1, entry: 50, pnl_inr: -200, opened_at: "2026-01-01T00:00:00Z" },
          ],
          null,
        );
      }, [onTradesChange]);
      return <div data-testid="guide">guide:{view}</div>;
    },
  };
});
vi.mock("@/components/desk/ApiKeyGate", () => ({ ApiKeyGate: () => null }));
vi.mock("@/pages/Desk", () => ({ Desk: () => <div data-testid="desk">desk</div> }));

const me = (name: string, owner: boolean, prefs: Partial<DeskPrefs> = {}) => ({
  ok: true,
  data: { id: owner ? null : name.toLowerCase(), name, is_owner: owner, prefs: { ...DEFAULT_PREFS, ...prefs } },
});

const renderDash = () =>
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Dashboard />
    </MemoryRouter>,
  );

beforeEach(() => {
  apiMock.putMyPrefs.mockResolvedValue({ ok: true });
  apiMock.getUniverses.mockResolvedValue({ ok: true, data: [] });
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe("Dashboard customization", () => {
  it("shows the owner the standard view with no sign-in line", async () => {
    apiMock.getMe.mockResolvedValue(me("Owner", true));
    renderDash();
    expect(await screen.findByRole("button", { name: "Customize" })).toBeInTheDocument();
    expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.queryByText("Win rate")).not.toBeInTheDocument();
  });

  it("applies a saved view: name, chosen tiles in order, hidden tab, one page", async () => {
    apiMock.getMe.mockResolvedValue(me("Ayushi", false, { tiles: ["worst", "win_rate"], hidden_tabs: ["chart"], layout: "stacked" }));
    renderDash();
    expect(await screen.findByText("Ayushi")).toBeInTheDocument();
    expect(screen.getByText(/Signed in as/)).toBeInTheDocument();
    const labels = screen.getAllByRole("term").map((t) => t.textContent);
    expect(labels).toEqual(["Worst trade", "Win rate"]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByTestId("guide")).toHaveTextContent("guide:all");
    expect(screen.queryByTestId("desk")).not.toBeInTheDocument();
  });

  it("lets a tab be hidden from the Sections dropdown and saves it", async () => {
    apiMock.getMe.mockResolvedValue(me("Ayushi", false));
    renderDash();
    await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
    await userEvent.click(screen.getByRole("button", { name: /^Sections/ }));
    await userEvent.click(within(screen.getByRole("group", { name: "Sections" })).getByRole("checkbox", { name: /Chart and tools/ }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    await waitFor(() => expect(apiMock.putMyPrefs).toHaveBeenCalled(), { timeout: 2000 });
    expect(apiMock.putMyPrefs.mock.calls.at(-1)?.[0].hidden_tabs).toEqual(["chart"]);
  });

  it("offers every filter and information row with tick boxes", async () => {
    apiMock.getMe.mockResolvedValue(me("Ayushi", false));
    renderDash();
    await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    const filters = within(screen.getByRole("group", { name: "Filters" })).getAllByRole("checkbox");
    expect(filters).toHaveLength(5);
    expect(filters.every((c) => (c as HTMLInputElement).checked)).toBe(true);
    await userEvent.click(filters[1]);
    expect(screen.getByRole("button", { name: /Filters/ })).toHaveTextContent("4 of 5 shown");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Filters" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Information rows/ }));
    expect(within(screen.getByRole("group", { name: "Information rows" })).getAllByRole("checkbox").length).toBeGreaterThanOrEqual(10);
  });

  it("keeps working when the server has no profile route", async () => {
    apiMock.getMe.mockRejectedValue(new Error("404"));
    renderDash();
    expect(await screen.findByRole("button", { name: "Customize" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
  });
});
