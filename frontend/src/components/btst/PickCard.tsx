import { CheckCircle2, CircleSlash } from "lucide-react";
import {
  inr, orderSteps, outcomeText, qtyFor, rupeesFor, signedInr, signedPct, tone, type BtstRecord,
} from "@/lib/btst";
import { cn } from "@/lib/utils";

function Level({ label, value, sub, kind }: { label: string; value: string; sub?: string; kind?: "good" | "bad" }) {
  return (
    <div className="rounded-md bg-muted/50 p-3 min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-mono text-base", kind === "good" && "text-emerald-400", kind === "bad" && "text-rose-400")}>{value}</dd>
      {sub && <dd className="font-mono text-xs text-muted-foreground">{sub}</dd>}
    </div>
  );
}

/** The day's one decision: what to buy, the two exit levels, and what it means in rupees. */
export function PickCard({ rec, capital, onCapital }: { rec: BtstRecord | null; capital: number; onCapital: (v: number) => void }) {
  if (!rec) {
    return (
      <div className="rounded-md bg-muted/50 p-4 text-sm">
        No scan yet. The scan runs at 15:20 on trading days when the schedule is on, or press Scan now while the market is open.
      </div>
    );
  }
  const p = rec.pick;
  const costPct = rec.rules.cost_pct * 100;
  const settled = rec.status === "settled";
  if (!p) {
    return (
      <div className="rounded-md border border-border p-4 space-y-1">
        <p className="text-sm font-semibold flex items-center gap-2"><CircleSlash className="h-4 w-4 text-amber-400" aria-hidden />No trade for {rec.date}</p>
        <p className="text-sm text-muted-foreground">{rec.skip_reason}</p>
      </div>
    );
  }
  const qty = qtyFor(capital, p.entry);
  const winPct = rec.rules.target_pct * 100 - costPct;
  const lossPct = p.stop !== null ? -(rec.rules.stop_pct * 100 + costPct) : null;
  const o = p.outcome;
  const result = o && !o.data_check ? rupeesFor(o.net_pct, qty, p.entry) : null;
  return (
    <article className="rounded-md border border-border p-4 space-y-4" aria-label={`Pick for ${rec.date}: ${p.symbol}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-lg font-semibold">{p.symbol}</h3>
        <span className="font-mono text-sm text-emerald-400">Buy · {signedPct(p.gain_pct)} today</span>
        <span className="text-xs text-muted-foreground ml-auto">{rec.kind === "replay" ? "Replayed day" : `Pick for ${rec.date}`}</span>
      </div>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Level label="Buy around" value={`₹${p.entry.toFixed(2)}`} sub={`${qty} shares ≈ ${inr(qty * p.entry)}`} />
        <Level label="Take-profit (sell)" value={`₹${p.target.toFixed(2)}`} sub={`+${(rec.rules.target_pct * 100).toFixed(0)}% · ${signedInr(rupeesFor(winPct, qty, p.entry))} if right`} kind="good" />
        <Level label="Safety stop (sell)" value={p.stop !== null ? `₹${p.stop.toFixed(2)}` : "off"}
          sub={lossPct !== null ? `${MINUS}${(rec.rules.stop_pct * 100).toFixed(0)}% · ${signedInr(rupeesFor(lossPct, qty, p.entry))} if wrong` : "no stop"} kind="bad" />
        <Level label={settled ? "Result" : "Costs"} value={settled ? signedPct(o?.net_pct) : `≈${costPct.toFixed(2)}%`}
          sub={settled ? (result !== null ? signedInr(result) : outcomeText(o)) : "of the trade, both sides"} kind={settled ? tone(o?.net_pct) : undefined} />
      </dl>
      {settled ? (
        <p className="text-sm flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 text-muted-foreground" aria-hidden />
          {outcomeText(o)} on {rec.settled_on}
          {o?.method === "daily" ? " (from the daily bar)" : o?.method === "intraday" ? " (from 5-minute prices)" : ""}
          {o?.ambiguous ? `; that day touched both levels, so the stop was assumed first (${signedPct(o.alt_net_pct)} if the take-profit came first).` : "."}
        </p>
      ) : (
        <ol className="text-sm space-y-1 list-decimal pl-5">
          {orderSteps(p, qty).map((s) => <li key={s}>{s}</li>)}
        </ol>
      )}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <label htmlFor="btst-capital">Money per trade</label>
        <input id="btst-capital" type="number" min={1000} step={1000} value={capital}
          onChange={(e) => { const v = Number(e.target.value); if (v > 0) onCapital(v); }}
          className="min-h-11 w-32 rounded-md border border-border bg-background px-2 font-mono text-sm text-foreground" />
        <span>Results are after about {costPct.toFixed(2)}% delivery costs. You place real orders yourself, in your broker app.</span>
      </div>
    </article>
  );
}

const MINUS = "−";
