import { useState } from "react";
import { dayLabel, rateText, signedInr, signedR, tone, winRatioGeometry, type DayRow, type GroupStat, type IntradayStats } from "@/lib/intraday";
import { cn } from "@/lib/utils";

const W = 320;
const H = 96;
const PAGE = 30;

function WinRatioChart({ stats }: { stats: IntradayStats }) {
  const g = winRatioGeometry(stats.series, W, H);
  const last = stats.series[stats.series.length - 1];
  if (!last || last.cum_trades === 0) return <p className="text-sm text-muted-foreground">No finished trades yet.</p>;
  const summary = `Cumulative win ratio ${Math.round((last.cum_win_rate ?? 0) * 100)}% of ${last.cum_trades} trades over ${stats.days} days`;
  return (
    <div className="space-y-2">
      <p className="text-sm">
        <span className="font-mono text-base">{Math.round((last.cum_win_rate ?? 0) * 100)}%</span>{" "}
        <span className="text-muted-foreground">won, of {last.cum_trades} trades over {stats.days} days · {signedR(last.cum_net_r)} · {signedInr(last.cum_net_inr)}</span>
      </p>
      {g ? (
        <svg role="img" aria-label={summary} viewBox={`0 0 ${W} ${H}`} className="w-full h-24">
          {g.baseline !== null && (
            <>
              <line x1={0} x2={W} y1={g.baseline} y2={g.baseline} className="stroke-border" strokeDasharray="3 4" />
              <text x={W - 2} y={g.baseline - 3} textAnchor="end" className="fill-muted-foreground" fontSize={9}>50%</text>
            </>
          )}
          <text x={2} y={10} className="fill-muted-foreground" fontSize={9}>{Math.round(g.hi * 100)}%</text>
          <text x={2} y={H - 3} className="fill-muted-foreground" fontSize={9}>{Math.round(g.lo * 100)}%</text>
          <polyline points={g.points} fill="none" strokeWidth={1.6} className="stroke-foreground/80" />
          <circle cx={g.endX} cy={g.endY} r={2.6} className="fill-foreground" />
        </svg>
      ) : null}
      <details className="text-xs">
        <summary className="cursor-pointer min-h-11 flex items-center text-muted-foreground">Show the numbers</summary>
        <div className="max-h-56 overflow-auto">
          <table className="w-full font-mono">
            <thead className="text-muted-foreground text-left font-sans"><tr><th className="font-normal">Through</th><th className="font-normal text-right">Trades</th><th className="font-normal text-right">Win %</th><th className="font-normal text-right">Net</th></tr></thead>
            <tbody>
              {[...stats.series].reverse().map((s) => (
                <tr key={s.date} className="border-t border-border/60">
                  <td>{s.date}</td><td className="text-right">{s.cum_trades}</td>
                  <td className="text-right">{s.cum_win_rate === null ? "—" : `${Math.round(s.cum_win_rate * 100)}%`}</td>
                  <td className="text-right">{signedInr(s.cum_net_inr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function Groups({ title, rows, keyLabel }: { title: string; rows: GroupStat[]; keyLabel: string }) {
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <h3 className="text-sm font-semibold mb-1">{title}</h3>
      <table className="w-full text-xs">
        <thead className="text-muted-foreground text-left"><tr>
          <th className="font-normal py-1 pr-2">{keyLabel}</th>{rows[0].days !== null && <th className="font-normal text-right pr-2">Days</th>}
          <th className="font-normal text-right pr-2">Won</th><th className="font-normal text-right pr-2">Avg</th><th className="font-normal text-right">Net</th>
        </tr></thead>
        <tbody className="font-mono">
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border/60">
              <td className="py-1 pr-2 font-sans">{r.key.replace("nifty50", "Nifty 50").replace("niftybank", "Nifty Bank")}</td>
              {r.days !== null && <td className="text-right pr-2">{r.days}</td>}
              <td className="text-right pr-2">{rateText(r.win_rate, r.trades)}</td>
              <td className={cn("text-right pr-2", r.avg_r > 0 ? "text-emerald-400" : r.avg_r < 0 ? "text-rose-400" : "")}>{signedR(r.avg_r)}</td>
              <td className={cn("text-right", tone(r.net_inr) === "good" && "text-emerald-400", tone(r.net_inr) === "bad" && "text-rose-400")}>{signedInr(r.net_inr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Right column: how the rules have done across all filed days, and a way into any single day. */
export function DaysPanel({ days, stats, selected, onPick, onRefile, busy }: { days: DayRow[]; stats: IntradayStats | null; selected: string; onPick: (date: string) => void; onRefile: () => void; busy: boolean }) {
  const [shown, setShown] = useState(PAGE);
  return (
    <div className="space-y-5">
      <section aria-label="Cumulative win ratio" className="space-y-1">
        <h2 className="text-sm font-semibold">All days</h2>
        {stats ? <WinRatioChart stats={stats} /> : <p className="text-sm text-muted-foreground">—</p>}
        {stats && stats.mixed_rules > 0 && (
          <div role="status" className="rounded-md bg-muted/50 p-2 text-xs space-y-2">
            <p className="text-amber-400">{stats.mixed_rules} of {stats.days} days were filed under different rules than your current ones, so these totals mix two rule sets.</p>
            <button type="button" disabled={busy} onClick={onRefile} className="min-h-11 px-3 rounded-md border border-border text-sm hover:bg-muted/50 disabled:opacity-60">Refile all days with today's rules</button>
          </div>
        )}
      </section>

      <section aria-label="Day by day" className="space-y-1">
        <h2 className="text-sm font-semibold">Day by day</h2>
        {days.length === 0 ? (
          <p className="text-sm text-muted-foreground">No days filed yet.</p>
        ) : (
          <ul className="space-y-1">
            {days.slice(0, shown).map((d) => (
              <li key={d.date}>
                <button
                  type="button"
                  aria-pressed={d.date === selected}
                  onClick={() => onPick(d.date)}
                  className={cn(
                    "w-full min-h-11 px-2 py-1 grid grid-cols-[6.5rem_1fr_auto] items-center gap-x-2 text-left rounded-md text-xs transition-colors duration-150 ease-out motion-reduce:transition-none hover:bg-muted/50 focus-visible:outline focus-visible:outline-2",
                    d.date === selected && "bg-muted",
                  )}
                >
                  <span className="font-mono">{dayLabel(d.date, false)}</span>
                  <span className="truncate text-muted-foreground">
                    {d.label ?? (d.status === "planned" ? "Planned" : "—")} · {d.taken ? rateText(d.win_rate, d.wins + d.losses + d.flat) : "no trades"}
                    {!d.rules_match && <span className="text-amber-400"> · other rules</span>}
                  </span>
                  <span className={cn("font-mono text-right", tone(d.net_inr) === "good" && "text-emerald-400", tone(d.net_inr) === "bad" && "text-rose-400")}>{d.taken ? signedInr(d.net_inr) : "—"}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {days.length > shown && (
          <button type="button" onClick={() => setShown((n) => n + PAGE)} className="min-h-11 px-3 text-sm rounded-md border border-border hover:bg-muted/50">
            Show {Math.min(PAGE, days.length - shown)} more days
          </button>
        )}
      </section>

      {stats && stats.days > 0 && (
        <section aria-label="How each kind of day did" className="space-y-4">
          <Groups title="How each kind of day did" rows={stats.by_type} keyLabel="Kind of day" />
          <Groups title="Buy vs short" rows={stats.by_side.map((r) => ({ ...r, key: r.key === "long" ? "Buy" : "Short" }))} keyLabel="Side" />
          <Groups title="By index" rows={stats.by_index} keyLabel="Index" />
        </section>
      )}
    </div>
  );
}
