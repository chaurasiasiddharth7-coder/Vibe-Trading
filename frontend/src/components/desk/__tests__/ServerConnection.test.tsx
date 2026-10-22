import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { ServerConnection } from "../ServerConnection";

const apiMock = vi.hoisted(() => ({ getUniverses: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("ServerConnection", () => {
  it("shows a working connection with the key masked", async () => {
    localStorage.setItem("vibe_trading_api_auth_key", "supersecret1234");
    apiMock.getUniverses.mockResolvedValue({ ok: true, data: [] });
    render(<ServerConnection />);
    expect(await screen.findByText("Connected. Your key works.")).toBeInTheDocument();
    expect(screen.getByText("••••1234")).toBeInTheDocument();
    expect(screen.queryByText("supersecret1234")).toBeNull();
  });

  it("says when the server refuses the key", async () => {
    localStorage.setItem("vibe_trading_api_auth_key", "old");
    apiMock.getUniverses.mockRejectedValue(new ApiError("no", 401));
    render(<ServerConnection />);
    expect(await screen.findByText("The server refused the saved key.")).toBeInTheDocument();
  });

  it("forgets the key and reloads", async () => {
    localStorage.setItem("vibe_trading_api_auth_key", "abcd");
    apiMock.getUniverses.mockResolvedValue({ ok: true, data: [] });
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<ServerConnection />);
    await userEvent.click(await screen.findByRole("button", { name: "Forget key on this device" }));
    expect(localStorage.getItem("vibe_trading_api_auth_key")).toBeNull();
    expect(reload).toHaveBeenCalled();
  });

  it("replaces the key from the change form", async () => {
    apiMock.getUniverses.mockResolvedValue({ ok: true, data: [] });
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<ServerConnection />);
    await userEvent.click(await screen.findByRole("button", { name: "Change key" }));
    await userEvent.type(screen.getByLabelText("New server key"), " newkey ");
    await userEvent.click(screen.getByRole("button", { name: "Save and reload" }));
    expect(localStorage.getItem("vibe_trading_api_auth_key")).toBe("newkey");
    expect(reload).toHaveBeenCalled();
  });
});
