import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { menuPages, pageForPath } from "@/lib/session";
import { normalizePrefs } from "@/lib/deskPrefs";

describe("menuPages", () => {
  const base = normalizePrefs(null);

  it("shows only allowed pages, in the user's order, without hidden ones", () => {
    const prefs = { ...base, pages_order: ["portfolio", "stocks"], hidden_pages: ["suggestions"] };
    const ids = menuPages(["stocks", "portfolio", "suggestions", "fno"], prefs).map((p) => p.id);
    expect(ids).toEqual(["portfolio", "stocks", "fno"]);
  });

  it("two users see different menus", () => {
    const a = menuPages(["stocks", "intraday"], base).map((p) => p.id);
    const b = menuPages(["stocks", "wealth", "suggestions"], base).map((p) => p.id);
    expect(a).not.toEqual(b);
  });

  it("never ends up empty", () => {
    const prefs = { ...base, hidden_pages: ["stocks"] };
    expect(menuPages(["stocks"], prefs).map((p) => p.id)).toEqual(["stocks"]);
  });

  it("ignores pages the server did not allow even if stored in prefs", () => {
    const prefs = { ...base, pages_order: ["users", "fno"] };
    expect(menuPages(["stocks"], prefs).map((p) => p.id)).toEqual(["stocks"]);
  });

  it("maps paths to pages", () => {
    expect(pageForPath("/portfolio")).toBe("portfolio");
    expect(pageForPath("/dashboard")).toBe("stocks");
    expect(pageForPath("/layout")).toBeNull();
  });
});

describe("normalizePrefs app layout", () => {
  it("keeps valid layout fields and caps tiles at 4", () => {
    const p = normalizePrefs({
      privacy_mode: true,
      landing_page: "portfolio",
      tiles_by_page: { fno: ["a", "b", "c", "d", "e"], "Bad Key": ["x"] },
      pages_order: ["fno", "Bad", "fno"],
    } as never);
    expect(p.privacy_mode).toBe(true);
    expect(p.landing_page).toBe("portfolio");
    expect(p.tiles_by_page).toEqual({ fno: ["a", "b", "c", "d"] });
    expect(p.pages_order).toEqual(["fno"]);
  });
});

describe("Login", () => {
  it("shows the server's message on a wrong password", async () => {
    const { ApiError } = await import("@/lib/api");
    const deskApi = await import("@/lib/deskApi");
    vi.spyOn(deskApi.desk, "login").mockRejectedValue(new ApiError("Too many wrong tries. This account is locked for 15 minutes.", 401));
    const { Login } = await import("@/pages/Login");
    render(<Login onDone={() => {}} />);
    await userEvent.type(screen.getByLabelText("Username"), "ayushi");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password-1");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("locked for 15 minutes");
  });
});
