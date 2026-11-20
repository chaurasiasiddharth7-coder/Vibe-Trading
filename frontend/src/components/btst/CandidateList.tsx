import { useState } from "react";
import { AlertTriangle, Check, ChevronDown } from "lucide-react";
import { outcomeText, signedPct, tone, type BtstCandidate, type BtstRecord } from "@/lib/btst";
import { cn } from "@/lib/utils";

const fmt = (n: number | null, suffix = "", d = 1) => (n === null ? "—" : `${n.toFixed(d)}${suffix}`);

function Row({ c, open, onToggle }: { c: BtstCandidate; open: boolean; onToggle: () => void }) {
  const o = c.outcome;
  return (
    <li className="border-b border-border last:border-0">
      <button type="button" onClick={onToggle} aria-expanded={open}
        className={cn("w-full min-h-11 py-2 px-2 grid grid-cols-[2rem_minmax(0,1fr)_auto] sm:grid-cols-[2rem_9rem_5rem_minmax(0,1fr)_auto] items-center gap-x-3 text-left text-sm rounded-md hover:bg-muted/50 transition-colors duration-150 ease-out motion-reduce:transition-none", c.chosen && "bg-muted/40")}>
        <span className="font-mono text-xs text-muted-foreground">{c.rank}</span>
        <span className="font-semibold truncate">{c.symbol}{c.chosen && <span className="ml-2 text-xs font-normal text-emerald-400">Pick</span>}</span>
        <span className="font-mono hidden sm:block">{signedPct(c.gain_pct)}</span>
        <span className="hidden sm:flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {c.warnings.length === 0
            ? <span className="inline-flex items-center gap-1 text-muted-foreground"><Check className="h-3.5 w-3.5" aria-hidden />No warnings</span>
            : c.warnings.map((w) => <span key={w.key} className="inline-flex items-center gap-1 text-amber-400"><AlertTriangle className="h-3.5 w-3.5" aria-hidden />{w.label}</span>)}
        </span>
        <span className="flex items-center gap-2">
          {o && !o.data_check && <span className={cn("font-mono text-xs", tone(o.net_pct) === "good" ? "text-emerald-400" : tone(o.net_pct) === "bad" ? "text-rose-400" : "")}>{signedPct(o.net_pct)}</span>}
          <span className="sm:hidden font-mono text-xs">{signedPct(c.gain_pct)}{c.warnings.length ? ` · ⚠${c.warnings.length}` : ""}</span>
          <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform duration-150 ease-out motion-reduce:transition-none", open && "rotate-180")} aria-hidden />
        </span>
      </button>
      {open && (
        <div className="px-2 pb-3 pl-12 space-y-2 text-xs">
          {c.warnings.length > 0 ? (
            <ul className="space-y-1">
              {c.warnings.map((w) => (
                <li key={w.key}><span className="text-amber-400 font-semibold">⚠ {w.label}.</span> {w.detail} <span className="text-muted-foreground">In the 2023–26 test, {w.big_loss_flagged_pct}% of stocks with this warning lost 5% or more overnight, against {w.big_loss_clean_pct}% without it.</span></li>
              ))}
            </ul>
          ) : <p>No warning applies. The rules pick the highest gainer like this.</p>}
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1 font-mono text-muted-foreground">
            <div><dt className="inline font-sans">Price </dt><dd className="inline">₹{fmt(c.last, "", 2)}</dd></div>
            <div><dt className="inline font-sans">Traded </dt><dd className="inline">₹{fmt(c.traded_cr)} cr ({fmt(c.turn_x, "x")})</dd></div>
            <div><dt className="inline font-sans">5 / 20 days </dt><dd className="inline">{signedPct(c.ret5_pct, 1)} / {signedPct(c.ret20_pct, 1)}</dd></div>
            <div><dt className="inline font-sans">From 1-yr high </dt><dd className="inline">{signedPct(c.from_high_pct, 1)}</dd></div>
            <div><dt className="inline font-sans">Opened </dt><dd className="inline">{signedPct(c.gap_pct, 1)}</dd></div>
            <div><dt className="inline font-sans">Off today's high </dt><dd className="inline">{signedPct(c.off_high_pct, 1)}</dd></div>
            <div><dt className="inline font-sans">Daily swing </dt><dd className="inline">{fmt(c.vol20_pct, "%")}</dd></div>
            {o && <div className="col-span-2"><dt className="inline font-sans">Next day </dt><dd className="inline">{outcomeText(o)}</dd></div>}
          </dl>
        </div>
      )}
    </li>
  );
}

/** Every candidate, best gainer first, with its warnings. The pick is the first one with none. */
export function CandidateList({ rec }: { rec: BtstRecord | null }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!rec || rec.candidates.length === 0) return null;
  const lo = rec.left_out ?? {};
  const skipped = [
    lo.upper_band ? `${lo.upper_band} stuck at the upper price band` : null,
    lo.illiquid_today ? `${lo.illiquid_today} traded under ₹${rec.rules.min_traded_cr} cr` : null,
    lo.thin_history ? `${lo.thin_history} usually too quiet` : null,
    lo.cheap ? `${lo.cheap} under ₹${rec.rules.min_price}` : null,
  ].filter(Boolean);
  return (
    <section aria-label="Candidates" className="space-y-2">
      <h3 className="text-sm font-semibold">Top {rec.candidates.length} gainers that qualified</h3>
      <ul className="rounded-md border border-border px-1">
        {rec.candidates.map((c) => (
          <Row key={c.symbol} c={c} open={open === c.symbol} onToggle={() => setOpen(open === c.symbol ? null : c.symbol)} />
        ))}
      </ul>
      {skipped.length > 0 && <p className="text-xs text-muted-foreground">Left out before ranking: {skipped.join(", ")}.</p>}
    </section>
  );
}
