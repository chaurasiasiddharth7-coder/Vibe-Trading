import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  DAY_KINDS, EMPTY_FILTERS, backwardsRange, dayLabel, filtersActive, rateText, searchQuery, sideWord, signedInr, statusLabel, tone,
  type SearchFilters, type SearchResult, type SearchTrade,
} from "@/lib/intraday";
import { friendlyError } from "@/lib/pollJob";
import { cn } from "@/lib/utils";

const PAGE = 15;
const field = "min-h-11 w-full rounded-md border border-border bg-background px-2 text-sm";
const price = (n: number) => n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function Row({ t }: { t: SearchTrade }) {
  const good = tone(t.pnl_inr);
  return (
    <li className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 px-3 py-2 text-xs">
      <span className="min-w-[8rem]">
        <span className="text-sm font-semibold">{t.symbol}</span>{" "}
        <span className={cn("font-semibold", t.side === "long" ? "text-emerald-400" : "text-rose-400")}>{sideWord(t.side)}</span>{" "}
        <span className="font-mono text-muted-foreground">{t.decided_at}</span>
      </span>
      <span className="font-mono text-muted-foreground flex-1 min-w-[12rem]">in {price(t.entry)} · {statusLabel(t)}</span>
      <span className={cn("font-mono text-sm", good === "good" && "text-emerald-400", good === "bad" && "text-rose-400")}>{signedInr(t.pnl_inr)}</span>
    </li>
  );
}

/** Find trades across every filed day and read them date by date. */
export function TradeSearch({ onOpenDay }: { onOpenDay: (date: string) => void }) {
  const [f, setF] = useState<SearchFilters>(EMPTY_FILTERS);
  const [res, setRes] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);
  const latest = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const run = useCallback(async (filters: SearchFilters) => {
    const id = ++latest.current; // a slower, older answer must never overwrite a newer one
    setBusy(true);
    setError(null);
    try {
      const r = await api.intradaySearch(searchQuery(filters));
      if (mounted.current && id === latest.current) {
        setRes(r.data);
        setShown(PAGE);
      }
    } catch (e) {
      if (mounted.current && id === latest.current) setError(friendlyError(e));
    } finally {
      if (mounted.current && id === latest.current) setBusy(false);
    }
  }, []);

  useEffect(() => { void run(EMPTY_FILTERS); }, [run]);

  const set = <K extends keyof SearchFilters>(k: K, v: SearchFilters[K]) => setF((p) => ({ ...p, [k]: v }));
  const backwards = backwardsRange(f);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!backwards) void run(f);
  };
  const clear = () => {
    setF(EMPTY_FILTERS);
    void run(EMPTY_FILTERS);
  };

  const t = res?.totals;
  return (
    <section aria-labelledby="find-h" className="space-y-3">
      <h2 id="find-h" className="text-sm font-semibold">Find trades</h2>
      <form onSubmit={submit} className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 text-sm">
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Taken from</span>
          <input className={cn(field, "font-mono")} type="date" value={f.from} onChange={(e) => set("from", e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Taken to</span>
          <input className={cn(field, "font-mono")} type="date" value={f.to} onChange={(e) => set("to", e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Symbol</span>
          <input className={field} type="text" inputMode="text" maxLength={20} placeholder="e.g. TCS" value={f.symbol} onChange={(e) => set("symbol", e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Buy or short</span>
          <select className={field} value={f.side} onChange={(e) => set("side", e.target.value as SearchFilters["side"])}>
            <option value="">Either</option><option value="long">Buy</option><option value="short">Short</option></select></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Result</span>
          <select className={field} value={f.outcome} onChange={(e) => set("outcome", e.target.value as SearchFilters["outcome"])}>
            <option value="">Any</option><option value="won">Won</option><option value="lost">Lost</option><option value="flat">About even</option><option value="open">Still open</option></select></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Kind of day</span>
          <select className={field} value={f.kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">Any</option>{DAY_KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Index</span>
          <select className={field} value={f.index} onChange={(e) => set("index", e.target.value as SearchFilters["index"])}>
            <option value="">Any</option><option value="nifty50">Nifty 50</option><option value="niftybank">Nifty Bank</option></select></label>
        <div className="col-span-2 md:col-span-4 xl:col-span-7 flex flex-wrap items-center gap-2">
          <button type="submit" disabled={backwards} className="min-h-11 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60 inline-flex items-center gap-2">
            {busy && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}Search
          </button>
          <button type="button" onClick={clear} disabled={!filtersActive(f)} className="min-h-11 px-4 rounded-md border border-border text-sm hover:bg-muted/50 disabled:opacity-60">Clear</button>
          {backwards && <span role="alert" className="text-xs text-amber-400">The "taken from" date is after the "taken to" date.</span>}
        </div>
      </form>

      <div aria-live="polite" className="space-y-3">
        {error && <p role="alert" className="text-sm text-amber-400 rounded-md bg-muted/50 p-3">{error}</p>}
        {t && (
          <p className="text-sm">
            <span className="font-mono">{t.trades}</span> trade{t.trades === 1 ? "" : "s"} over <span className="font-mono">{t.days}</span> day{t.days === 1 ? "" : "s"}
            {t.trades > 0 && <> · {rateText(t.win_rate, t.wins + t.losses + t.flat)} won · <span className={cn("font-mono", tone(t.net_inr) === "good" && "text-emerald-400", tone(t.net_inr) === "bad" && "text-rose-400")}>{signedInr(t.net_inr)}</span></>}
            {res?.truncated && <span className="text-xs text-amber-400"> · showing the newest days only; narrow the dates to see the rest</span>}
          </p>
        )}
        {res && res.days.length === 0 && !error && (
          <p className="text-sm text-muted-foreground rounded-md bg-muted/50 p-3">No trades match. {filtersActive(f) ? "Loosen a filter or press Clear." : "Nothing has been filed yet."}</p>
        )}
        {res?.days.slice(0, shown).map((d) => (
          <div key={d.date} className="rounded-md border border-border">
            <button type="button" onClick={() => onOpenDay(d.date)} className="w-full min-h-11 px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-left hover:bg-muted/50 rounded-t-md transition-colors duration-150 ease-out motion-reduce:transition-none focus-visible:outline focus-visible:outline-2">
              <span className="font-mono text-sm">{dayLabel(d.date, false)}</span>
              <span className="text-xs text-muted-foreground flex-1">{d.label ?? "—"} · {d.summary.trades} trade{d.summary.trades === 1 ? "" : "s"}{!d.rules_match && <span className="text-amber-400"> · other rules</span>}</span>
              <span className={cn("font-mono text-sm", tone(d.summary.net_inr) === "good" && "text-emerald-400", tone(d.summary.net_inr) === "bad" && "text-rose-400")}>{signedInr(d.summary.net_inr)}</span>
            </button>
            <ul className="divide-y divide-border/60 border-t border-border/60">{d.trades.map((tr) => <Row key={tr.id} t={tr} />)}</ul>
          </div>
        ))}
        {res && res.days.length > shown && (
          <button type="button" onClick={() => setShown((n) => n + PAGE)} className="min-h-11 px-3 text-sm rounded-md border border-border hover:bg-muted/50">
            Show {Math.min(PAGE, res.days.length - shown)} more days
          </button>
        )}
      </div>
    </section>
  );
}
