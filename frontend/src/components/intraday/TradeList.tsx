import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { PathSpark } from "@/components/intraday/PathSpark";
import { sideWord, signedInr, signedR, statusLabel, tone, type DayRecord, type IntradayTrade } from "@/lib/intraday";
import { cn } from "@/lib/utils";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const price = (n: number) => n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function TradeRow({ t }: { t: IntradayTrade }) {
  const [open, setOpen] = useState(false);
  const good = tone(t.pnl_inr);
  const ifRight = t.qty * Math.abs(t.target - t.entry);
  const ifWrong = t.qty * t.risk_per_share;
  return (
    <li className="rounded-md border border-border">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full min-h-14 px-3 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-left rounded-md hover:bg-muted/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 transition-colors duration-150 ease-out motion-reduce:transition-none"
      >
        <span className="min-w-[8.5rem]">
          <span className="text-sm font-semibold">{t.symbol}</span>{" "}
          <span className={cn("text-xs font-semibold", t.side === "long" ? "text-emerald-400" : "text-rose-400")}>{sideWord(t.side)}</span>
          <span className="block font-mono text-xs text-muted-foreground">decided {t.decided_at}</span>
        </span>
        <span className="font-mono text-xs text-muted-foreground flex-1 min-w-[12rem]">
          in {price(t.entry)} → take-profit {price(t.target)} / cut-loss {price(t.stop)}
          <span className="block text-foreground/80">{statusLabel(t)}</span>
        </span>
        <PathSpark trade={t} />
        <span className="font-mono text-sm text-right min-w-[6.5rem]">
          <span className={cn("block", good === "good" && "text-emerald-400", good === "bad" && "text-rose-400")}>{signedInr(t.pnl_inr)}</span>
          <span className="block text-xs text-muted-foreground">{signedR(t.r_net)}</span>
        </span>
        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform duration-150 motion-reduce:transition-none", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <dl className="px-3 pb-3 grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-xs">
          <Fact label="Shares" value={`${inr.format(t.qty)}`} />
          <Fact label="If right / if wrong" value={`+₹${inr.format(ifRight)} / −₹${inr.format(ifWrong)}`} />
          <Fact label="Cut-loss distance" value={`${t.risk_pct.toFixed(2)}% of the price`} />
          <Fact label="Reward : risk" value={`${t.reward_risk}`} />
          <Fact label="Best / worst on the way" value={`${signedR(t.mfe_r)} / ${signedR(t.mae_r)}`} />
          <Fact label="Result before costs" value={signedR(t.r_gross)} />
          <Fact label="Opening range" value={`${price(t.range.low)} – ${price(t.range.high)}`} />
          <Fact label="Volume at the break" value={`${t.volume_x.toFixed(1)}× the range`} />
        </dl>
      )}
    </li>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}

/** Trades in the order they were decided, then what was planned but did not trade. */
export function TradeList({ rec }: { rec: DayRecord }) {
  return (
    <div className="space-y-4">
      {rec.trades.length === 0 ? (
        <p className="text-sm text-muted-foreground rounded-md bg-muted/50 p-3">
          {rec.status === "planned"
            ? "Planned at the open. No symbol has broken its opening range yet; trades appear here as they trigger."
            : "No symbol broke its opening range in a way these rules would trade on this day."}
        </p>
      ) : (
        <ul className="space-y-2">{rec.trades.map((t) => <TradeRow key={t.id} t={t} />)}</ul>
      )}

      {rec.skipped.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer min-h-11 flex items-center">Skipped by the rules ({rec.skipped.length})</summary>
          <ul className="text-xs text-muted-foreground space-y-1 pb-2">
            {rec.skipped.slice(0, 40).map((s) => (
              <li key={`${s.symbol}-${s.side}-${s.decided_at}`}>
                <span className="font-mono text-foreground">{s.symbol}</span> {sideWord(s.side).toLowerCase()} at {s.decided_at}: {s.reason}
              </li>
            ))}
            {rec.skipped.length > 40 && <li>…and {rec.skipped.length - 40} more</li>}
          </ul>
        </details>
      )}

      <details className="text-sm">
        <summary className="cursor-pointer min-h-11 flex items-center">
          Planned for the day ({rec.planned.length}){rec.not_triggered.length ? `, ${rec.not_triggered.length} never triggered` : ""}
        </summary>
        <div className="overflow-x-auto pb-2">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground text-left">
              <tr><th className="py-1 pr-3 font-normal">Symbol</th><th className="pr-3 font-normal">Index</th><th className="pr-3 font-normal text-right">Last close</th><th className="pr-3 font-normal text-right">Traded a day</th><th className="font-normal">Yesterday's chart</th></tr>
            </thead>
            <tbody className="font-mono">
              {rec.planned.map((p) => (
                <tr key={p.symbol} className="border-t border-border/60">
                  <td className="py-1 pr-3 font-semibold">{p.symbol}</td>
                  <td className="pr-3 text-muted-foreground">{p.tags.map((t) => (t === "nifty50" ? "N50" : t === "niftybank" ? "Bank" : t)).join(" ") || "—"}</td>
                  <td className="pr-3 text-right">{price(p.price)}</td>
                  <td className="pr-3 text-right">₹{p.traded_value_cr.toFixed(0)} cr</td>
                  <td className="font-sans text-muted-foreground">{p.setups.length ? p.setups.join(", ").replace(/_/g, " ") : "nothing special"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
