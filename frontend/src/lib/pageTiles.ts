/** The tiles each new page can show. The first four are the default; people pick up to four in My layout. */
export const PAGE_TILES: Record<string, Array<{ id: string; label: string }>> = {
  portfolio: [
    { id: "value", label: "Current value" },
    { id: "day", label: "Day change" },
    { id: "pnl", label: "Total P&L" },
    { id: "ungrouped", label: "Ungrouped share" },
    { id: "invested", label: "Invested" },
    { id: "groups", label: "Groups" },
  ],
  fno: [
    { id: "mtm", label: "Day MTM" },
    { id: "floor", label: "Profit lock floor" },
    { id: "margin", label: "Margin used" },
    { id: "expiry", label: "Nearest expiry" },
    { id: "peak", label: "Day peak" },
    { id: "open", label: "Open positions" },
  ],
  wealth: [
    { id: "value", label: "Total value" },
    { id: "return", label: "Return" },
    { id: "sips", label: "SIPs due this week" },
    { id: "alloc", label: "Allocation" },
    { id: "pnl", label: "Total P&L" },
  ],
  suggestions: [
    { id: "buy", label: "Buy ideas" },
    { id: "short", label: "Short ideas" },
    { id: "avoid", label: "Avoid" },
    { id: "asof", label: "Prices as of" },
  ],
  strategies: [
    { id: "tracked", label: "Strategies tracked" },
    { id: "paper", label: "Paper trading on" },
    { id: "gate", label: "Passed the backtest gate" },
    { id: "best", label: "Best paper record" },
    { id: "live", label: "Live trading" },
  ],
  users: [
    { id: "active", label: "Active users" },
    { id: "today", label: "Signed in today" },
    { id: "locked", label: "Locked" },
  ],
};
