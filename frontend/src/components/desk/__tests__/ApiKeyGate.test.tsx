import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { ApiKeyGate } from "../ApiKeyGate";

const apiMock = vi.hoisted(() => ({ getUniverses: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

describe("ApiKeyGate", () => {
  it("renders nothing when the server accepts the request", async () => {
    apiMock.getUniverses.mockResolvedValue({ ok: true, data: [] });
    const { container } = render(<ApiKeyGate />);
    await waitFor(() => expect(apiMock.getUniverses).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("asks for the key on 401 and stores it", async () => {
    apiMock.getUniverses.mockRejectedValue(new ApiError("nope", 401));
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<ApiKeyGate />);
    const input = await screen.findByLabelText("Server key");
    const save = screen.getByRole("button", { name: "Save and reload" });
    expect(save).toBeDisabled();
    await userEvent.type(input, "  abc123 ");
    await userEvent.click(save);
    expect(localStorage.getItem("vibe_trading_api_auth_key")).toBe("abc123");
    expect(reload).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
