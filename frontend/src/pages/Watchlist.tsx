import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Pencil, Plus, Search, SlidersHorizontal, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useQuotes } from "@/hooks/useQuotes";
import { fmtChange, fmtPrice, tone, watchlistApi, type WatchList } from "@/lib/watchlistApi";

/** The home screen: five watchlists with prices (live from Kite on the owner's desk). */
export function Watchlist() {
  const navigate = useNavigate();
  const [lists, setLists] = useState<WatchList[] | null>(null);
  const [max, setMax] = useState(250);
  const [active, setActive] = useState(() => {
    const n = Number(sessionStorage.getItem("wl-tab"));
    return Number.isInteger(n) && n >= 0 && n < 5 ? n : 0;
  });
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    watchlistApi
      .lists()
      .then((r) => {
        setLists(r.lists);
        setMax(r.max_items);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load your watchlists"));
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem("wl-tab", String(active));
    } catch {
      // fine
    }
  }, [active]);

  const items = useMemo(() => lists?.[active]?.items ?? [], [lists, active]);
  const { data } = useQuotes(items, false);

  const act = async (fn: () => Promise<{ lists: WatchList[] }>) => {
    try {
      setLists((await fn()).lists);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work");
    }
  };

  return (
    <main className="max-w-3xl mx-auto pb-24 md:pb-8">
      <div role="tablist" aria-label="Watchlists" className="flex gap-6 overflow-x-auto px-4 pt-2 no-scrollbar">
        {(lists ?? Array.from({ length: 5 }, (_, i) => ({ name: `Watchlist ${i + 1}`, items: [] }))).map((l, i) => (
          <button
            key={i}
            role="tab"
            type="button"
            aria-selected={i === active}
            onClick={() => setActive(i)}
            className={cn(
              "shrink-0 min-h-11 pb-1 text-[15px] font-semibold border-b-2 transition-colors",
              i === active ? "text-primary border-primary" : "text-foreground/80 border-transparent hover:text-foreground",
            )}
          >
            {l.name}
          </button>
        ))}
      </div>

      <div className="px-3 pt-3">
        <AddBar
          count={items.length}
          max={max}
          editing={editing}
          onToggleEdit={() => setEditing((e) => !e)}
          onAdd={(sym) => act(() => watchlistApi.add(active, sym))}
          has={items}
        />
      </div>

      <PriceSource live={data?.live} at={data?.at} asOf={items.length ? data?.quotes[items[0]]?.as_of : undefined} />
      {error && <p role="alert" className="mx-4 my-2 text-sm text-danger">{error}</p>}
      {editing && lists && (
        <RenameRow key={active} name={lists[active].name} onSave={(n) => act(() => watchlistApi.rename(active, n))} />
      )}

      <section aria-label={lists?.[active]?.name ?? "Watchlist"} className="mt-1 bg-card md:rounded-lg md:border md:border-border">
        {lists && items.length === 0 && (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">
            Nothing here yet. Search above to add stocks, like RELIANCE or LT.
          </p>
        )}
        <ul>
          {items.map((sym) => {
            const q = data?.quotes[sym];
            return (
              <li key={sym} className="border-b border-border last:border-b-0">
                <div className="flex items-center">
                  <button
                    type="button"
                    onClick={() => navigate(`/dashboard?tab=chart&symbol=${encodeURIComponent(sym)}`)}
                    className="flex-1 min-w-0 flex items-center justify-between gap-3 px-4 py-3.5 text-start hover:bg-muted/50"
                  >
                    <span className="min-w-0">
                      <span className="block text-[15px] text-foreground truncate">{sym}</span>
                      <span className="block text-xs text-muted-foreground mt-1">NSE</span>
                    </span>
                    <span className="text-end tabular-nums">
                      <span className={cn("block text-[15px]", tone(q?.change))}>{fmtPrice(q?.ltp)}</span>
                      <span className="block text-xs text-foreground/70 mt-1">{q ? fmtChange(q) : ""}</span>
                    </span>
                  </button>
                  {editing && (
                    <button
                      type="button"
                      aria-label={`Remove ${sym}`}
                      onClick={() => act(() => watchlistApi.remove(active, sym))}
                      className="h-11 w-11 me-2 grid place-items-center rounded-full text-danger hover:bg-danger/10"
                    >
                      <X className="h-5 w-5" aria-hidden />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}

function PriceSource({ live, at, asOf }: { live?: boolean; at?: number; asOf?: string }) {
  if (live === undefined) return null;
  const time = at ? new Date(at * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
  return (
    <p className="px-4 pt-2 text-xs text-muted-foreground flex items-center gap-1.5" aria-live="polite">
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", live ? "bg-success animate-pulse motion-reduce:animate-none" : "bg-muted-foreground")} aria-hidden />
      {live ? `Live from Kite · ${time}` : `End-of-day prices${asOf ? ` · ${asOf}` : ""}`}
    </p>
  );
}

function AddBar({
  count, max, editing, onToggleEdit, onAdd, has,
}: {
  count: number;
  max: number;
  editing: boolean;
  onToggleEdit: () => void;
  onAdd: (symbol: string) => Promise<void>;
  has: string[];
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setResults([]);
      return;
    }
    const t = window.setTimeout(() => {
      watchlistApi
        .search(term)
        .then((r) => setResults(r.results.map((x) => x.symbol)))
        .catch(() => setResults([]));
    }, 150);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setResults([]);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const add = async (sym: string) => {
    await onAdd(sym);
    setQ("");
    setResults([]);
  };

  return (
    <div ref={box} className="relative">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const pick = results[0] ?? q.trim().toUpperCase();
          if (pick) void add(pick);
        }}
        className="flex items-center gap-3 rounded-lg bg-card border border-border shadow-sm px-4 h-14"
      >
        <Search className="h-5 w-5 text-muted-foreground shrink-0" aria-hidden />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search & add"
          aria-label="Search and add a stock"
          autoCapitalize="characters"
          autoCorrect="off"
          className="flex-1 min-w-0 bg-transparent outline-none text-[15px] placeholder:text-muted-foreground"
        />
        <span className="text-sm text-muted-foreground tabular-nums">{count}/{max}</span>
        <button
          type="button"
          onClick={onToggleEdit}
          aria-pressed={editing}
          aria-label={editing ? "Done editing" : "Edit this watchlist"}
          className={cn("h-10 w-10 -me-2 grid place-items-center rounded-md", editing ? "text-primary" : "text-foreground/80 hover:bg-muted")}
        >
          {editing ? <Check className="h-5 w-5" aria-hidden /> : <SlidersHorizontal className="h-5 w-5" aria-hidden />}
        </button>
      </form>
      {results.length > 0 && (
        <ul role="listbox" aria-label="Matching stocks" className="absolute z-20 inset-x-0 mt-1 max-h-80 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg">
          {results.map((sym) => {
            const added = has.includes(sym);
            return (
              <li key={sym}>
                <button
                  type="button"
                  disabled={added}
                  onClick={() => void add(sym)}
                  className="w-full flex items-center justify-between px-4 min-h-12 text-start hover:bg-muted disabled:opacity-60"
                >
                  <span>
                    {sym} <span className="text-xs text-muted-foreground ms-1">NSE</span>
                  </span>
                  {added ? <Check className="h-4 w-4 text-success" aria-label="Added" /> : <Plus className="h-4 w-4 text-primary" aria-label="Add" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function RenameRow({ name, onSave }: { name: string; onSave: (name: string) => Promise<void> }) {
  const [v, setV] = useState(name);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (v.trim() && v.trim() !== name) void onSave(v.trim());
      }}
      className="mx-4 mt-2 flex items-center gap-2"
    >
      <Pencil className="h-4 w-4 text-muted-foreground" aria-hidden />
      <input
        value={v}
        maxLength={24}
        onChange={(e) => setV(e.target.value)}
        aria-label="Watchlist name"
        className="flex-1 min-h-10 rounded-md border border-border bg-card px-3 text-sm"
      />
      <button type="submit" className="min-h-10 px-3 rounded-md bg-primary text-primary-foreground text-sm font-medium">Rename</button>
    </form>
  );
}
