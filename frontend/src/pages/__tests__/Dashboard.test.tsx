import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../Dashboard";

vi.mock("@/pages/Guide", async () => {
  const React = await import("react");
  return {
    Guide: ({ view, onTradesChange }: { view: string; onTradesChange: (t: unknown[], tr: unknown) => void }) => {
      React.useEffect(() => {
        onTradesChange(
          [
            { id: "1", symbol: "AAA", status: "OPEN", qty: 1, entry: 100, unrealized_inr: 500, suggestion: { action: "exit", text: "x" } },
            { id: "2", symbol: "BBB", status: "CLOSED", qty: 1, entry: 50, pnl_inr: -200 },
          ],
          { closed_trades: 1, win_rate: 0, expectancy_r: -1, verdict: "too_few_trades" },
        );
      }, [onTradesChange]);
      return <div data-testid="guide">guide:{view}</div>;
    },
  };
});
vi.mock("@/components/desk/ApiKeyGate", () => ({ ApiKeyGate: () => null }));
vi.mock("@/pages/Desk", () => ({ Desk: () => <div data-testid="desk">desk</div> }));

function Where() {
  const l = useLocation();
  return <span data-testid="where">{l.pathname + l.search}</span>;
}

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Dashboard />
      <Where />
    </MemoryRouter>,
  );

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("Dashboard", () => {
  it("summarises trades and flags the ones that need a look", async () => {
    renderAt("/dashboard");
    expect(await screen.findByText("+₹500")).toBeInTheDocument();
    expect(screen.getByText("−₹200")).toBeInTheDocument();
    expect(screen.getByText(/1 open trade could use a look: AAA/)).toBeInTheDocument();
  });

  it("switches tabs, keeps the tab in the URL and mounts Desk only when visited", async () => {
    renderAt("/dashboard");
    expect(screen.queryByTestId("desk")).toBeNull();
    await userEvent.click(screen.getByRole("tab", { name: /My practice trades/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/dashboard?tab=trades");
    expect(screen.getByTestId("guide")).toHaveTextContent("guide:trades");
    await userEvent.click(screen.getByRole("tab", { name: "Chart and tools" }));
    expect(await screen.findByTestId("desk")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Chart and tools" })).toHaveAttribute("aria-selected", "true");
  });

  it("supports arrow keys and the banner jumps to trades", async () => {
    renderAt("/dashboard");
    screen.getByRole("tab", { name: "Today's ideas" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /My practice trades/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Today's ideas" }));
    await userEvent.click(screen.getByRole("button", { name: /could use a look/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("tab=trades");
  });

  it("remembers that the welcome card was dismissed", async () => {
    renderAt("/dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Hide this guide" }));
    expect(screen.queryByText("New here? Three steps")).toBeNull();
    cleanup();
    renderAt("/dashboard");
    expect(screen.queryByText("New here? Three steps")).toBeNull();
  });
});
