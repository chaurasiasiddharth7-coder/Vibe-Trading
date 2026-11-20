import { renderHook, act } from "@testing-library/react";
import { setThemeMode, useDarkMode } from "../useDarkMode";

describe("useDarkMode (shared Light / Dark / System setting)", () => {
  beforeEach(() => {
    act(() => setThemeMode("system"));
    localStorage.clear();
  });

  it("follows the system (light in tests) by default", () => {
    const { result } = renderHook(() => useDarkMode());
    expect(result.current.mode).toBe("system");
    expect(result.current.dark).toBe(false);
  });

  it("dark mode sets the class and remembers it", () => {
    const { result } = renderHook(() => useDarkMode());
    act(() => result.current.setMode("dark"));
    expect(result.current.dark).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("qa-theme")).toBe("dark");
    act(() => result.current.setMode("light"));
    expect(result.current.dark).toBe(false);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("toggle flips between light and dark", () => {
    const { result } = renderHook(() => useDarkMode());
    act(() => result.current.toggle());
    expect(result.current.dark).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.dark).toBe(false);
    expect(localStorage.getItem("qa-theme")).toBe("light");
  });

  it("every user of the hook sees the same change", () => {
    const a = renderHook(() => useDarkMode());
    const b = renderHook(() => useDarkMode());
    act(() => a.result.current.setMode("dark"));
    expect(b.result.current.dark).toBe(true);
  });
});
