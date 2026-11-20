import { useEffect, useMemo, useState } from "react";
import { desk, rupees, ASSET_LABEL, type Suggestion } from "@/lib/deskApi";
import { useSession } from "@/lib/session";
import { Chip, Empty, Note, Page, PageHeader, Tiles } from "@/components/shell/ui";
import { cn } from "@/lib/utils";

const ACTION = {
  buy: { label: "Buy idea", cls: "text-emerald-700 dark:text-emerald-400 border-emerald-700/40" },
  short: { label: "Short idea", cls: "text-rose-700 dark:text-rose-400 border-rose-700/40" },
  avoid: { label: "Avoid", cls: "text-amber-700 dark:text-amber-300 border-amber-700/40" },
} as const;
const PAGE = 10;

/** One ranked list across asset types. Every card carries its tested record, or says it has none. */
export function Suggestions() {
  const { prefs } = useSession();
  const [cards, setCards] = useState<Suggestion[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [disclaimer, setDisclaimer] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [action, setAction] = useState<string>("all");
  const [type, setType] = useState<string>("all");
  const [risky, setRisky] = useState(false);
  const [limit, setLimit] = useState(PAGE);

  useEffect(() => {
    desk
      .suggestions()
      .then((r) => {
        setCards(r.data.cards);
        setAsOf(r.data.as_of);
        setDisclaimer(r.data.disclaimer);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load ideas."))
      .finally(() => setLoaded(true));
  }, []);

  const types = useMemo(() => [...new Set(cards.map((c) => c.asset_type))], [cards]);
  const shown = cards.filter((c) => (action === "all" || c.action === action) && (type === "all" || c.asset_type === type) && (!risky || !c.risk));
  const count = (a: string) => cards.filter((c) => c.action === a).length;

  return (
    <Page>
      <PageHeader title="Suggestions" sub={disclaimer || "Model ideas, not advice."} />
      <Tiles
        chosen={prefs.tiles_by_page.suggestions}
        tiles={[
          { id: "buy", label: "Buy ideas", value: String(count("buy")) },
          { id: "short", label: "Short ideas", value: String(count("short")) },
          { id: "avoid", label: "Avoid", value: String(count("avoid")) },
          { id: "asof", label: "Prices as of", value: asOf ?? "—" },
        ]}
      />
      {error && <Note tone="bad">{error}</Note>}
      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Filters">
        {["all", "buy", "short", "avoid"].map((a) => (
          <Chip key={a} active={action === a} onClick={() => { setAction(a); setLimit(PAGE); }}>
            {a === "all" ? "All" : ACTION[a as keyof typeof ACTION].label}
          </Chip>
        ))}
        <select className="min-h-9 rounded-full border border-border bg-background px-3 text-sm" value={type} onChange={(e) => { setType(e.target.value); setLimit(PAGE); }} aria-label="Asset type">
          <option value="all">All types</option>
          {types.map((t) => <option key={t} value={t}>{ASSET_LABEL[t] ?? t}</option>)}
        </select>
        <Chip active={risky} onClick={() => setRisky((r) => !r)}>No warnings only</Chip>
      </div>

      {!loaded ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : shown.length === 0 ? (
        <Empty>No ideas yet. Run a scan on the Stocks page ("Today's ideas") to fill this list.</Empty>
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {shown.slice(0, limit).map((c) => (
            <li key={c.id} className="rounded-md border border-border p-3 space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="font-semibold">{c.symbol}</span>
                <span className={cn("text-xs rounded border px-1.5 py-0.5", ACTION[c.action].cls)}>{ACTION[c.action].label}</span>
                <span className="text-xs text-muted-foreground">{ASSET_LABEL[c.asset_type] ?? c.asset_type} · {c.strategy}</span>
              </div>
              {c.name !== c.symbol && <p className="text-xs text-muted-foreground">{c.name}</p>}
              <p className="text-sm">{c.reason}</p>
              {c.action !== "avoid" && c.entry != null && (
                <p className="text-xs font-mono">
                  Entry {rupees(c.entry, 2)} · <span className="text-emerald-600 dark:text-emerald-400">target {rupees(c.target, 2)}</span> ·{" "}
                  <span className="text-rose-600 dark:text-rose-400">cut-loss {rupees(c.stop, 2)}</span>
                </p>
              )}
              {c.risk && <p className="text-xs text-amber-700 dark:text-amber-300">Watch out: {c.risk}</p>}
              <p className="text-xs text-muted-foreground">{c.track}</p>
              <p className="text-[11px] text-muted-foreground">Model idea, not advice{c.as_of ? ` · prices ${c.as_of}` : ""}</p>
            </li>
          ))}
        </ul>
      )}
      {shown.length > limit && (
        <button type="button" onClick={() => setLimit((l) => l + PAGE)} className="min-h-10 px-4 rounded-md border border-border text-sm">
          Show more ({shown.length - limit} left)
        </button>
      )}
    </Page>
  );
}
