import { describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { normalizePrefs } from "@/lib/deskPrefs";
import { setThemeMode } from "@/hooks/useDarkMode";

const savePrefs = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/session", async (orig) => ({
  ...(await orig<typeof import("@/lib/session")>()),
  useSession: () => ({
    me: { name: "Sid", username: "sid", role: "owner", is_owner: true, signed_in_with: "password" },
    prefs: normalizePrefs(null),
    allowed: ["watchlist", "stocks", "intraday", "portfolio", "users"],
    savePrefs,
    refresh: vi.fn(),
  }),
}));

import { Account } from "@/pages/Account";

describe("Account settings", () => {
  it("dark mode switch applies at once and is saved to the account", async () => {
    act(() => setThemeMode("system"));
    render(<MemoryRouter><Account /></MemoryRouter>);
    await userEvent.click(screen.getByRole("radio", { name: /Dark/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(screen.getByRole("radio", { name: /Dark/ })).toHaveAttribute("aria-checked", "true");
    expect(savePrefs).toHaveBeenCalledWith({ theme: "dark" });
    await userEvent.click(screen.getByRole("radio", { name: /Light/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("lists the other pages", () => {
    render(<MemoryRouter><Account /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /Intraday/ })).toHaveAttribute("href", "/intraday");
    expect(screen.getByRole("link", { name: /Users/ })).toBeInTheDocument();
  });
});
