import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, isAuthRequiredError } from "@/lib/api";
import { normalizePrefs, type DeskMe, type DeskPrefs } from "@/lib/deskPrefs";

/** Every page of the Kite-style app, in the default menu order. `id` matches the server's page names. */
export const APP_PAGES = [
  { id: "watchlist", to: "/watchlist", label: "Watchlist" },
  { id: "stocks", to: "/dashboard", label: "Stocks" },
  { id: "intraday", to: "/intraday", label: "Intraday" },
  { id: "btst", to: "/btst", label: "Overnight" },
  { id: "fno", to: "/fno", label: "F&O" },
  { id: "wealth", to: "/wealth", label: "Gold · ETF · MF" },
  { id: "portfolio", to: "/portfolio", label: "Portfolio" },
  { id: "suggestions", to: "/suggestions", label: "Suggestions" },
  { id: "strategies", to: "/strategies", label: "Strategies" },
  { id: "users", to: "/users", label: "Users" },
] as const;

export type PageId = (typeof APP_PAGES)[number]["id"];

export function pageForPath(path: string): PageId | null {
  const hit = APP_PAGES.find((p) => path === p.to || path.startsWith(p.to + "/"));
  return hit ? hit.id : null;
}

/** The pages this person sees, in their order, without the ones they hid. Never empty. */
export function menuPages(allowed: string[], prefs: Pick<DeskPrefs, "pages_order" | "hidden_pages">) {
  const allow = new Set(allowed);
  const order = [...prefs.pages_order.filter((id) => allow.has(id)), ...APP_PAGES.map((p) => p.id).filter((id) => allow.has(id))];
  const unique = [...new Set(order)];
  const hidden = new Set(prefs.hidden_pages);
  const shown = unique.filter((id) => !hidden.has(id) || id === "users");
  const ids = shown.length ? shown : unique.slice(0, 1);
  return ids.map((id) => APP_PAGES.find((p) => p.id === id)!).filter(Boolean);
}

export type SessionState = "loading" | "signed_out" | "must_change" | "ready" | "offline";

interface SessionApi {
  state: SessionState;
  me: DeskMe | null;
  prefs: DeskPrefs;
  allowed: string[];
  refresh: () => Promise<void>;
  savePrefs: (patch: Partial<DeskPrefs>) => Promise<void>;
}

const Ctx = createContext<SessionApi | null>(null);

export function useSession(): SessionApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

/** Optional variant for components that also render outside the hosted shell. */
export function useMaybeSession(): SessionApi | null {
  return useContext(Ctx);
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>("loading");
  const [me, setMe] = useState<DeskMe | null>(null);
  const [prefs, setPrefs] = useState<DeskPrefs>(() => normalizePrefs(null));

  const refresh = useCallback(async () => {
    try {
      const res = await api.getMe();
      setMe(res.data);
      setPrefs(normalizePrefs(res.data.prefs));
      setState(res.data.must_change ? "must_change" : "ready");
    } catch (e) {
      setState(isAuthRequiredError(e) ? "signed_out" : "offline");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const savePrefs = useCallback(
    async (patch: Partial<DeskPrefs>) => {
      const next = normalizePrefs({ ...prefs, ...patch });
      setPrefs(next);
      const res = await api.putMyPrefs(next);
      setPrefs(normalizePrefs(res.data));
    },
    [prefs],
  );

  // The watchlist is everyone's home screen (own lists; Kite prices for the owner only), whatever pages they were given.
  const allowed = useMemo(() => {
    const base = me?.pages ?? (me?.is_owner ? APP_PAGES.map((p) => p.id) : APP_PAGES.map((p) => p.id).filter((p) => p !== "users"));
    return base.includes("watchlist") ? base : ["watchlist", ...base];
  }, [me]);
  const value = useMemo(() => ({ state, me, prefs, allowed, refresh, savePrefs }), [state, me, prefs, allowed, refresh, savePrefs]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
