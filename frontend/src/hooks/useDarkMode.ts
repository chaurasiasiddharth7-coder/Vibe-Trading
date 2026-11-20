import { useSyncExternalStore } from "react";

/** Light, dark, or follow the phone/computer. One shared value, so Settings changes every chart at once. */
export type ThemeMode = "light" | "dark" | "system";

const KEY = "qa-theme";
const listeners = new Set<() => void>();

function readMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // storage blocked: follow the system
  }
  return "system";
}

let mode: ThemeMode = typeof window === "undefined" ? "system" : readMode();

function systemDark(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

function isDark(): boolean {
  return mode === "dark" || (mode === "system" && systemDark());
}

function apply() {
  if (typeof document === "undefined") return;
  const dark = isDark();
  document.documentElement.classList.toggle("dark", dark);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#121212" : "#f2f3f5");
}

export function setThemeMode(next: ThemeMode) {
  if (next === mode) return;
  mode = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // keep it for this visit only
  }
  apply();
  listeners.forEach((l) => l());
}

export function getThemeMode(): ThemeMode {
  return mode;
}

if (typeof window !== "undefined") {
  apply();
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (mode !== "system") return;
    apply();
    listeners.forEach((l) => l());
  });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useDarkMode() {
  const dark = useSyncExternalStore(subscribe, isDark, () => false);
  const current = useSyncExternalStore(subscribe, getThemeMode, () => "system" as ThemeMode);
  return {
    dark,
    mode: current,
    setMode: setThemeMode,
    toggle: () => setThemeMode(dark ? "light" : "dark"),
  };
}
