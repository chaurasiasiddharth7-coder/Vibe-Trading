import { useState } from "react";
import { Loader2 } from "lucide-react";
import { pctRate, signedPct, tone, type BtstStats, type BtstSummary } from "@/lib/btst";
import { cn } from "@/lib/utils";

type Kind = "live" | "replay";

function Stat({ label, value, sub, kind }: { label: string; value: string; sub?: string; kind?: "good" | "bad" }) {
  return (
    <div className="rounded-md bg-muted/50 p-3 min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-mono text-base", kind === "good" && "text-emerald-400", kind === "bad" && "text-rose-400")}>{value}</dd>
      {sub && <dd className="text-xs text-muted-foreground">{sub}</dd>}
    </div>
  );
}

function Summary({ s, title, targetFirst, noStop }: { s: BtstSummary; title?: string; targetFirst?: BtstSummary; noStop?: BtstSummary }) {
  // With the stop on, a daily bar that touched both levels cannot say which filled first: show both ends.
  const range = targetFirst && targetFirst.avg_net_pct !== s.avg_net_pct ? targetFirst : undefined;
  return (
    <dl className="grid grid-cols-2 gap-3" aria-label={title}>
      <Stat label="Trades" value={String(s.trades)}
        sub={s.trades ? `take-profit hit ${range ? `${pctRate(s.hit_rate)}–${pctRate(range.hit_rate)}` : pctRate(s.hit_rate)}` : undefined} />
      <Stat label="Per trade, after costs" value={range ? `${signedPct(s.avg_net_pct)} to ${signedPct(range.avg_net_pct)}` : signedPct(s.avg_net_pct)}
        sub={range ? "if the stop came first … if the take-profit did" : s.trades ? `median ${signedPct(s.median_net_pct)}` : undefined}
        kind={range ? undefined : tone(s.avg_net_pct)} />
      <Stat label="Lost 5% or more" value={s.trades ? pctRate(s.big_loss_rate) : "—"} sub={s.trades ? `worst ${signedPct(s.worst_pct)}` : undefined} />
      {noStop && noStop.trades > 0
        ? <Stat label="Same picks, no stop" value={signedPct(noStop.avg_net_pct)} sub={`${pctRate(noStop.big_loss_rate)} lost 5%+, worst ${signedPct(noStop.worst_pct)}`} kind={tone(noStop.avg_net_pct)} />
        : <Stat label="Added up" value={signedPct(s.total_pct, 1)} sub={s.trades ? `best ${signedPct(s.best_pct)}` : undefined} kind={tone(s.total_pct)} />}
    </dl>
  );
}

/** Live and replayed records side by side, never blended. */
export function TrackRecord({ stats, onReplay, busy, replayNote }: {
  stats: { live: BtstStats; replay: BtstStats } | null; onReplay: () => void; busy: boolean; replayNote?: string;
}) {
  const [kind, setKind] = useState<Kind>("live");
  const s = stats?.[kind];
  const tabs: Array<[Kind, string]> = [["live", "Live days"], ["replay", "Replayed from stored prices"]];
  return (
    <section aria-label="Track record" className="space-y-3 min-w-0">
      <h2 className="text-sm font-semibold">Track record</h2>
      <div role="tablist" aria-label="Which record" className="flex gap-1 rounded-md bg-muted/50 p-1 w-fit"
        onKeyDown={(e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") setKind(kind === "live" ? "replay" : "live"); }}>
        {tabs.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={kind === k} tabIndex={kind === k ? 0 : -1} onClick={() => setKind(k)}
            className={cn("min-h-11 px-3 rounded text-sm transition-colors duration-150 ease-out motion-reduce:transition-none", kind === k ? "bg-background font-semibold" : "text-muted-foreground")}>
            {label}
          </button>
        ))}
      </div>
      {!s ? (
        <div className="h-40 rounded-md bg-muted/50" aria-busy />
      ) : s.picks.trades === 0 ? (
        <div className="rounded-md bg-muted/50 p-4 text-sm space-y-3">
          {kind === "live"
            ? <p>No settled live trades yet. Each scanned day settles after the next session closes.</p>
            : (
              <>
                <p>No replay yet. It rebuilds about 3 years of days from the NSE store on this server (a few minutes) and files them apart from live days.</p>
                <button type="button" onClick={onReplay} disabled={busy}
                  className="min-h-11 px-3 rounded-md border border-border bg-background text-sm inline-flex items-center gap-2 disabled:opacity-60">
                  {busy && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                  {busy ? "Working…" : "Build the replay"}
                </button>
                {replayNote && <p className="text-xs text-muted-foreground">{replayNote}</p>}
              </>
            )}
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-muted-foreground">
            {s.first} to {s.last} · {s.settled_days} days settled, {s.skipped_days} skipped (every candidate had a warning).
          </p>
          <Summary s={s.picks} title="With the rules as set" targetFirst={s.ambiguous > 0 ? s.picks_target_first : undefined} noStop={s.picks_no_stop} />
          {s.ambiguous > 0 && (
            <p className="text-xs text-muted-foreground">
              On {s.ambiguous} days the next day's prices touched both the 2% stop and the take-profit, and a daily bar cannot say which came first,
              so the result is a range. Checked minute by minute, the truth sits much nearer the take-profit-first end.
              {s.exact > 0 ? ` ${s.exact} trades were settled exactly from 5-minute prices.` : ""}
            </p>
          )}
          {s.by_year.length > 0 && (
            <table className="w-full text-sm">
              <caption className="text-left text-xs text-muted-foreground pb-1">By year, per trade after costs</caption>
              <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">Year</th><th className="text-right font-normal">Trades</th><th className="text-right font-normal">With the stop</th><th className="text-right font-normal">No stop</th></tr></thead>
              <tbody>
                {s.by_year.map((y) => {
                  const tf = y.avg_target_first_pct;
                  const withStop = tf !== undefined && tf !== y.avg_net_pct ? `${signedPct(y.avg_net_pct)} to ${signedPct(tf)}` : signedPct(y.avg_net_pct);
                  return (
                    <tr key={y.year} className="border-t border-border">
                      <td className="py-1 font-mono">{y.year}</td>
                      <td className="text-right font-mono">{y.trades}</td>
                      <td className="text-right font-mono">{withStop}</td>
                      <td className={cn("text-right font-mono", tone(y.avg_no_stop_pct) === "good" ? "text-emerald-400" : tone(y.avg_no_stop_pct) === "bad" ? "text-rose-400" : "")}>{signedPct(y.avg_no_stop_pct)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {s.candidates.settled > 0 && (
            <table className="w-full text-sm">
              <caption className="text-left text-xs text-muted-foreground pb-1">How the warnings did on all {s.candidates.settled} candidates (no stop)</caption>
              <thead><tr className="text-xs text-muted-foreground"><th className="text-left font-normal">Warning</th><th className="text-right font-normal">Lost 5%+ with it</th><th className="text-right font-normal">without</th><th className="text-right font-normal">Per trade with it</th></tr></thead>
              <tbody>
                {s.candidates.by_warning.map((w) => (
                  <tr key={w.key} className="border-t border-border">
                    <td className="py-1">{w.label}</td>
                    <td className="text-right font-mono">{w.flagged.trades ? pctRate(w.flagged.big_loss_rate) : "—"}</td>
                    <td className="text-right font-mono">{w.clean.trades ? pctRate(w.clean.big_loss_rate) : "—"}</td>
                    <td className="text-right font-mono">{w.flagged.trades ? signedPct(w.flagged.avg_net_pct) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
