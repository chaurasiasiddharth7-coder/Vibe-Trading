import "@testing-library/jest-dom/vitest";
// Initialize i18n so `useTranslation()` resolves real strings in tests.
// With no localStorage entry under jsdom this falls back to English, keeping
// the suite's English assertions stable.
import "../i18n";

// ── Global mocks for jsdom ───────────────────────────────────

// Node 25 ships its own global localStorage that shadows jsdom's and has no
// working clear()/removeItem() unless --localstorage-file is set. Use an
// in-memory Storage so tests behave the same on every Node version.
if (typeof localStorage === "undefined" || typeof localStorage.clear !== "function") {
  const mem = new Map<string, string>();
  const storage = {
    get length() {
      return mem.size;
    },
    key: (i: number) => Array.from(mem.keys())[i] ?? null,
    getItem: (k: string) => (mem.has(k) ? mem.get(k)! : null),
    setItem: (k: string, v: string) => void mem.set(k, String(v)),
    removeItem: (k: string) => void mem.delete(k),
    clear: () => mem.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
  if (typeof window !== "undefined")
    Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });
}

// jsdom doesn't implement ResizeObserver (ECharts + layout components need it)
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

// jsdom doesn't implement matchMedia
if (typeof window !== "undefined") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
