import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";

/** How one person wants the dashboard: stored on the server per profile, so it follows them across devices. */
export interface DeskPrefs {
  version: number;
  layout: "tabs" | "stacked";
  density: "comfortable" | "compact";
  tile_style: "cards" | "strip";
  /** Ordered tiles to show. `null` = the default set. */
  tiles: string[] | null;
  hidden_tabs: string[];
  hidden_filters: string[];
  hidden_rows: string[];
}

export interface DeskMe {
  id: string | null;
  name: string;
  is_owner: boolean;
  prefs: DeskPrefs;
}

export interface CatalogItem {
  id: string;
  label: string;
  hint?: string;
  /** Tiles only: shown when the person has not chosen yet. */
  on?: boolean;
}

export const TILES: CatalogItem[] = [
  { id: "open_pnl", label: "Open trades result", hint: "Paper profit or loss on trades still running", on: true },
  { id: "closed_pnl", label: "Finished trades result", hint: "Paper profit or loss on trades that ended", on: true },
  { id: "winning", label: "Winning now", hint: "How many trades are in profit right now", on: true },
  { id: "money_open", label: "Money in open trades", hint: "Fake money currently put to work", on: true },
  { id: "win_rate", label: "Win rate", hint: "Share of finished trades that made money" },
  { id: "best", label: "Best trade", hint: "Biggest single result so far" },
  { id: "worst", label: "Worst trade", hint: "Biggest single loss so far" },
  { id: "today", label: "Invested today", hint: "Trades started today (India time)" },
];

export const TABS: CatalogItem[] = [
  { id: "ideas", label: "Today's ideas" },
  { id: "trades", label: "My practice trades" },
  { id: "chart", label: "Chart and tools" },
];

export const FILTERS: CatalogItem[] = [
  { id: "status", label: "All / Open / Finished", hint: "Buttons that narrow the list by status" },
  { id: "dates", label: "Date invested range", hint: "From and to dates" },
  { id: "group_by_date", label: "List by date invested", hint: "Group the list under each day" },
  { id: "day_report", label: "Day-by-day report", hint: "One line per day with its result" },
  { id: "expand_all", label: "Show all details button" },
];

export const ROWS: CatalogItem[] = [
  { id: "trades_summary", label: "Summary above the list", hint: "Four totals inside My practice trades" },
  { id: "score_name", label: "Score and company name" },
  { id: "side_qty", label: "Buy or short × quantity" },
  { id: "price_path", label: "Entry → now price" },
  { id: "status_line", label: "Status and exit plan" },
  { id: "result", label: "Profit or loss" },
  { id: "price_bought", label: "Bought at" },
  { id: "price_now", label: "Now / Sold at" },
  { id: "price_target", label: "Target" },
  { id: "price_cutloss", label: "Cut-loss" },
  { id: "price_qty", label: "Qty" },
  { id: "day_totals", label: "Day totals in each date heading" },
  { id: "footnote", label: "Explanation note under the list" },
];

export const DEFAULT_PREFS: DeskPrefs = {
  version: 1,
  layout: "tabs",
  density: "comfortable",
  tile_style: "cards",
  tiles: null,
  hidden_tabs: [],
  hidden_filters: [],
  hidden_rows: [],
};

const KNOWN = {
  tiles: new Set(TILES.map((t) => t.id)),
  tabs: new Set(TABS.map((t) => t.id)),
  filters: new Set(FILTERS.map((t) => t.id)),
  rows: new Set(ROWS.map((t) => t.id)),
};

const only = (xs: unknown, known: Set<string>): string[] =>
  Array.isArray(xs) ? xs.filter((x): x is string => typeof x === "string" && known.has(x)) : [];

/** Make any stored value safe to use: unknown ids are dropped, and at least one tab always stays. */
export function normalizePrefs(raw: Partial<DeskPrefs> | null | undefined): DeskPrefs {
  const r = raw ?? {};
  const hiddenTabs = only(r.hidden_tabs, KNOWN.tabs);
  return {
    version: 1,
    layout: r.layout === "stacked" ? "stacked" : "tabs",
    density: r.density === "compact" ? "compact" : "comfortable",
    tile_style: r.tile_style === "strip" ? "strip" : "cards",
    tiles: Array.isArray(r.tiles) ? [...new Set(only(r.tiles, KNOWN.tiles))] : null,
    hidden_tabs: hiddenTabs.length >= TABS.length ? [] : hiddenTabs,
    hidden_filters: only(r.hidden_filters, KNOWN.filters),
    hidden_rows: only(r.hidden_rows, KNOWN.rows),
  };
}

/** The tiles to draw, in order. */
export function visibleTiles(p: DeskPrefs): string[] {
  return p.tiles ?? TILES.filter((t) => t.on).map((t) => t.id);
}

export interface DeskPrefsApi {
  me: DeskMe | null;
  prefs: DeskPrefs;
  ready: boolean;
  saveState: "idle" | "saving" | "saved" | "failed";
  update: (patch: Partial<DeskPrefs>) => void;
  reset: () => void;
  showFilter: (id: string) => boolean;
  showRow: (id: string) => boolean;
  compact: boolean;
}

function build(partial: Omit<DeskPrefsApi, "showFilter" | "showRow" | "compact">): DeskPrefsApi {
  const hf = new Set(partial.prefs.hidden_filters);
  const hr = new Set(partial.prefs.hidden_rows);
  return { ...partial, showFilter: (id) => !hf.has(id), showRow: (id) => !hr.has(id), compact: partial.prefs.density === "compact" };
}

/** Outside a provider (tests, other pages) everything is shown, as before. */
const Ctx = createContext<DeskPrefsApi>(
  build({ me: null, prefs: DEFAULT_PREFS, ready: true, saveState: "idle", update: () => {}, reset: () => {} }),
);

export const useDeskPrefs = () => useContext(Ctx);

const SAVE_DELAY_MS = 500;

export function DeskPrefsProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<DeskMe | null>(null);
  const [prefs, setPrefs] = useState<DeskPrefs>(DEFAULT_PREFS);
  const [ready, setReady] = useState(false);
  const [saveState, setSaveState] = useState<DeskPrefsApi["saveState"]>("idle");
  const timer = useRef<number | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true; // StrictMode runs the cleanup once before the real mount
    (async () => {
      try {
        const res = await api.getMe();
        if (!alive.current) return;
        setMe(res.data);
        setPrefs(normalizePrefs(res.data.prefs));
      } catch {
        // old server, no key yet, or offline: the default view still works
      } finally {
        if (alive.current) setReady(true);
      }
    })();
    return () => {
      alive.current = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  const persist = useCallback((next: DeskPrefs) => {
    setSaveState("saving");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        await api.putMyPrefs(next);
        if (alive.current) setSaveState("saved");
      } catch {
        if (alive.current) setSaveState("failed");
      }
    }, SAVE_DELAY_MS);
  }, []);

  const update = useCallback(
    (patch: Partial<DeskPrefs>) => {
      setPrefs((cur) => {
        const next = normalizePrefs({ ...cur, ...patch });
        persist(next);
        return next;
      });
    },
    [persist],
  );

  const reset = useCallback(() => {
    setPrefs(DEFAULT_PREFS);
    persist(DEFAULT_PREFS);
  }, [persist]);

  const value = useMemo(() => build({ me, prefs, ready, saveState, update, reset }), [me, prefs, ready, saveState, update, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
