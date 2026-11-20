import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { StockLink } from "@/components/desk/StockSheet";
import { Trash2 } from "lucide-react";
import { desk, rupees, tone, ASSET_LABEL, type GroupsData, type Holding } from "@/lib/deskApi";
import { useSession } from "@/lib/session";
import { Chip, Empty, Note, Page, PageHeader, Tiles } from "@/components/shell/ui";
import { cn } from "@/lib/utils";

const DOT: Record<string, string> = {
  emerald: "bg-emerald-500", sky: "bg-sky-500", violet: "bg-violet-500", amber: "bg-amber-500",
  rose: "bg-rose-500", slate: "bg-slate-500", teal: "bg-teal-500", indigo: "bg-indigo-500",
};
const field = "min-h-10 rounded-md border border-border bg-background px-3 text-sm";

/** Holdings filtered by custom groups. A group holds a quantity of a stock, so one holding can be split. */
export function Portfolio() {
  const { prefs, me } = useSession();
  const readOnly = me?.role === "viewer";
  const [data, setData] = useState<GroupsData | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const [adding, setAdding] = useState(false);
  const [assign, setAssign] = useState<Holding | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await desk.groups();
      setData(res.data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load your portfolio.");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(t);
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    }
  };

  const rows = useMemo(() => data?.holdings ?? [], [data]);
  const groupOf = useMemo(() => {
    const m: Record<string, Array<{ name: string; colour: string; qty: number }>> = {};
    for (const g of data?.groups ?? []) for (const l of g.lines) (m[l.holding_id] ??= []).push({ name: g.name, colour: g.colour, qty: l.qty });
    return m;
  }, [data]);
  const ungrouped = new Set((data?.ungrouped ?? []).map((u) => u.holding_id));
  const active = data?.groups.find((g) => g.id === filter);
  const shown = rows.filter((h) =>
    filter === "all" ? true : filter === "ungrouped" ? ungrouped.has(h.id) : (groupOf[h.id] ?? []).some((x) => x.name === active?.name),
  );

  const total = rows.filter((r) => r.side !== "SHORT").reduce((a, r) => a + r.value, 0);
  const invested = rows.filter((r) => r.side !== "SHORT").reduce((a, r) => a + r.invested, 0);
  const day = rows.reduce((a, r) => a + (r.day_change ?? 0), 0);
  const scope = active ?? null;

  return (
    <Page>
      <PageHeader title="Portfolio" sub="Your holdings, sorted into your own groups. Put part of a holding in one group and the rest in another." />
      <Tiles
        chosen={prefs.tiles_by_page.portfolio}
        tiles={[
          { id: "value", label: scope ? `${scope.name} value` : "Current value", value: rupees(scope ? scope.value : total) },
          { id: "day", label: "Day change", value: rupees(scope ? scope.day_change : day, 0, true), tone: tone(scope ? scope.day_change : day) },
          { id: "pnl", label: "Total P&L", value: rupees(scope ? scope.pnl : total - invested, 0, true), tone: tone(scope ? scope.pnl : total - invested) },
          { id: "ungrouped", label: "Not in a group", value: data?.ungrouped_pct == null ? "—" : `${data.ungrouped_pct}%` },
          { id: "invested", label: "Invested", value: rupees(scope ? scope.invested : invested) },
          { id: "groups", label: "Groups", value: String(data?.groups.length ?? 0) },
        ]}
      />
      {data?.note && <Note>{data.note}</Note>}
      {data?.notes.map((n) => <Note key={n} tone="warn">{n}</Note>)}
      {(data?.groups ?? []).some((g) => (g.waiting ?? []).length > 0) && (
        <Note>
          Some group holdings couldn't be read right now (usually Kite is logged out). They stay in their groups and come back
          when you log in: {(data?.groups ?? []).flatMap((g) => (g.waiting ?? []).map((w) => `${w.symbol} ${w.qty} in ${g.name}`)).join(", ")}.
        </Note>
      )}
      {error && <Note tone="bad">{error}</Note>}

      <div className="flex items-center gap-2 overflow-x-auto pb-1 [&>*]:shrink-0" role="toolbar" aria-label="Filter by group">
        <Chip active={filter === "all"} onClick={() => setFilter("all")}>All</Chip>
        {data?.groups.map((g) => (
          <Chip key={g.id} active={filter === g.id} onClick={() => setFilter(g.id)}>
            <span className={cn("inline-block h-2 w-2 rounded-full me-1.5", DOT[g.colour] ?? DOT.slate)} aria-hidden />
            {g.name}
          </Chip>
        ))}
        <Chip active={filter === "ungrouped"} onClick={() => setFilter("ungrouped")}>Ungrouped</Chip>
        {!readOnly && (
          <button type="button" onClick={() => setAdding((a) => !a)} className="min-h-9 px-3 rounded-full border border-dashed border-border text-sm whitespace-nowrap">
            + New group
          </button>
        )}
      </div>

      {adding && <NewGroup colours={data?.colours ?? []} onDone={async (body) => { await act(() => desk.addGroup(body)); setAdding(false); }} />}

      {scope && (
        <section className="rounded-md border border-border p-3 flex flex-wrap gap-4 items-center text-sm">
          <span className="font-semibold">{scope.name}</span>
          <span className="text-muted-foreground">Holds: {scope.asset_types.map((t) => ASSET_LABEL[t]).join(", ")}</span>
          {scope.goal_amount && (
            <span className="flex items-center gap-2">
              Goal <span className="font-mono">{rupees(scope.goal_amount)}</span>{scope.goal_date ? ` by ${scope.goal_date}` : ""}
              <span className="inline-block w-28 h-2 rounded bg-muted overflow-hidden" aria-label={`${scope.goal_progress_pct}% of goal`}>
                <span className="block h-full bg-emerald-500" style={{ width: `${Math.min(100, scope.goal_progress_pct ?? 0)}%` }} />
              </span>
              <span className="font-mono">{scope.goal_progress_pct}%</span>
            </span>
          )}
          {!readOnly && (
            <button
              type="button"
              onClick={() => { if (window.confirm(`Delete the group "${scope.name}"? Holdings stay; only the group goes.`)) void act(async () => { await desk.deleteGroup(scope.id); setFilter("all"); }); }}
              className="ms-auto min-h-9 px-2 rounded border border-border text-xs inline-flex items-center gap-1"
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete group
            </button>
          )}
        </section>
      )}

      {!data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : shown.length === 0 ? (
        <Empty>{filter === "all" ? "Nothing held yet. Practice trades and (for the owner) Zerodha holdings show here." : "Nothing in this group yet."}</Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="text-start px-3 py-2">Instrument</th>
                <th className="text-end px-3 py-2">Qty</th>
                <th className="text-end px-3 py-2">Avg</th>
                <th className="text-end px-3 py-2">LTP</th>
                <th className="text-end px-3 py-2">Day</th>
                <th className="text-end px-3 py-2">P&amp;L</th>
                <th className="text-start px-3 py-2">Groups</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {shown.map((h) => (
                <tr key={h.id}>
                  <td className="px-3 py-2">
                    <div className="font-medium">{h.asset_type === "mf" || h.asset_type === "fno" ? h.symbol : <StockLink symbol={h.symbol} />}</div>
                    <div className="text-xs text-muted-foreground">
                      {ASSET_LABEL[h.asset_type]} · {h.source === "paper" ? `practice ${h.side === "SHORT" ? "short" : "buy"}` : "Zerodha"}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-end font-mono">{h.qty}</td>
                  <td className="px-3 py-2 text-end font-mono">{rupees(h.avg, 2)}</td>
                  <td className="px-3 py-2 text-end font-mono">{rupees(h.ltp, 2)}</td>
                  <td className={cn("px-3 py-2 text-end font-mono", tone(h.day_change_pct))}>
                    {h.day_change_pct == null ? "—" : `${h.day_change_pct > 0 ? "+" : ""}${h.day_change_pct}%`}
                  </td>
                  <td className={cn("px-3 py-2 text-end font-mono", tone(h.pnl))}>{rupees(h.pnl, 0, true)}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1 items-center">
                      {(groupOf[h.id] ?? []).map((g) => (
                        <span key={g.name} className="text-xs rounded border border-border px-1.5 py-0.5 inline-flex items-center gap-1">
                          <span className={cn("h-2 w-2 rounded-full", DOT[g.colour] ?? DOT.slate)} aria-hidden />
                          {g.name} <span className="font-mono">{g.qty}</span>
                        </span>
                      ))}
                      {!readOnly && h.side !== "SHORT" && (data.groups.length > 0) && (
                        <button type="button" onClick={() => setAssign(h)} className="text-xs underline text-muted-foreground">
                          Set groups
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {assign && data && (
        <AssignPanel
          holding={assign}
          data={data}
          onClose={() => setAssign(null)}
          onSave={(gid, qty) => act(() => desk.allocate(gid, assign.id, qty))}
        />
      )}
    </Page>
  );
}

function NewGroup({ colours, onDone }: { colours: string[]; onDone: (body: Record<string, unknown>) => void }) {
  const [name, setName] = useState("");
  const [colour, setColour] = useState(colours[0] ?? "emerald");
  const [goal, setGoal] = useState("");
  const [date, setDate] = useState("");
  const [types, setTypes] = useState<string[]>(Object.keys(ASSET_LABEL));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onDone({ name, colour, goal_amount: goal ? Number(goal) : null, goal_date: date || null, asset_types: types });
  };
  return (
    <form onSubmit={submit} className="rounded-md border border-border p-3 space-y-3" aria-label="New group">
      <div className="flex flex-wrap gap-2">
        <input className={field} placeholder="Group name, e.g. Retirement" value={name} onChange={(e) => setName(e.target.value)} required maxLength={40} />
        <select className={field} value={colour} onChange={(e) => setColour(e.target.value)} aria-label="Colour">
          {colours.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <input className={cn(field, "w-36")} type="number" min="0" placeholder="Goal ₹ (optional)" value={goal} onChange={(e) => setGoal(e.target.value)} />
        <input className={field} type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Goal date" />
      </div>
      <div className="flex flex-wrap gap-3 text-sm">
        <span className="text-muted-foreground">Can hold:</span>
        {Object.entries(ASSET_LABEL).map(([k, label]) => (
          <label key={k} className="flex items-center gap-1">
            <input type="checkbox" checked={types.includes(k)} onChange={() => setTypes((t) => (t.includes(k) ? t.filter((x) => x !== k) : [...t, k]))} />
            {label}
          </label>
        ))}
      </div>
      <button type="submit" className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold">Create group</button>
    </form>
  );
}

function AssignPanel({ holding, data, onClose, onSave }: { holding: Holding; data: GroupsData; onClose: () => void; onSave: (gid: string, qty: number) => Promise<void> }) {
  const current = (gid: string) => data.groups.find((g) => g.id === gid)?.lines.find((l) => l.holding_id === holding.id)?.qty ?? 0;
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(data.groups.map((g) => [g.id, String(current(g.id) || "")])));
  const sum = Object.values(vals).reduce((a, v) => a + (Number(v) || 0), 0);
  const over = sum > holding.qty + 1e-9;
  const save = async () => {
    for (const g of data.groups) {
      const want = Number(vals[g.id]) || 0;
      if (want !== current(g.id) && want < current(g.id)) await onSave(g.id, want);
    }
    for (const g of data.groups) {
      const want = Number(vals[g.id]) || 0;
      if (want !== current(g.id) && want > current(g.id)) await onSave(g.id, want);
    }
    onClose();
  };
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" role="dialog" aria-modal="true" aria-label={`Groups for ${holding.symbol}`}>
      <div className="w-full max-w-sm h-full bg-card border-s border-border p-5 space-y-3 overflow-y-auto">
        <h2 className="text-lg font-semibold">{holding.symbol}: split across groups</h2>
        <p className="text-sm text-muted-foreground">You hold <span className="font-mono">{holding.qty}</span>. Groups can't add up to more.</p>
        {data.groups.map((g) => {
          const fits = g.asset_types.includes(holding.asset_type);
          return (
            <label key={g.id} className="flex items-center justify-between gap-2 text-sm">
              <span className={cn(!fits && "text-muted-foreground")}>{g.name}{!fits && " (doesn't take this type)"}</span>
              <input
                type="number"
                min="0"
                max={holding.qty}
                step="any"
                disabled={!fits}
                className={cn(field, "w-28 text-end font-mono")}
                value={vals[g.id] ?? ""}
                onChange={(e) => setVals((v) => ({ ...v, [g.id]: e.target.value }))}
              />
            </label>
          );
        })}
        <p className={cn("text-sm font-mono", over && "text-rose-600")}>
          {sum} of {holding.qty} in groups{over ? ": too many" : ""}
        </p>
        <div className="flex gap-2">
          <button type="button" disabled={over} onClick={() => void save()} className="flex-1 min-h-10 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50">
            Save
          </button>
          <button type="button" onClick={onClose} className="min-h-10 px-4 rounded-md border border-border text-sm">Cancel</button>
        </div>
      </div>
    </div>
  );
}
