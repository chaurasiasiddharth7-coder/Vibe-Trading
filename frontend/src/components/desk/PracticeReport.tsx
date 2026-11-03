import { useState } from "react";
import { dayLabel, rateText, signedInr, signedR, tone, winRatioGeometry } from "@/lib/intraday";
import { buildPracticeReport, type PracticeSplit } from "@/lib/practiceReport";
import type { PaperTrade } from "@/lib/api";
import { cn } from "@/lib/utils";

const W = 320;
const H = 96;
const PAGE = 30;
const ENOUGH = 30;

const toneClass = (n: number) => cn(tone(n) === "good" && "text-emerald-400", tone(n) === "bad" && "text-rose-400");

function Split({ title, rows }: { title: string; rows: PracticeSplit[] }) {
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <h4 className="text-sm font-semibold mb-1">{title}</h4>
      <table className="w-full text-xs">
        <thead className="text-muted-foreground text-left">
          <tr>
            <th className="font-normal py-1 pr-2">{title === "Buy vs short" ? "Side" : "How it ended"}</th>
            <th className="font-normal text-right pr-2">Won</th>
            <th className="font-normal text-right pr-2">Avg</th>
            <th className="font-normal text-right">Net</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border/60">
              <td className="py-1 pr-2 font-sans">{r.key}</td>
              <td className="text-right pr-2">{rateText(r.win_rate, r.trades)}</td>
              <td className={cn("text-right pr-2", toneClass(r.net_r))}>{signedR(r.net_r / r.trades)}</td>
              <td className={cn("text-right", toneClass(r.net_inr))}>{signedInr(r.net_inr)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The fake-money ledger as a report: cumulative win ratio, then day by day. A result belongs to the
 * day a trade closed, an opening to the day it was invested. Pressing a day filters the list below.
 */
export function PracticeReport({ trades, selected, onPickDay }: { trades: PaperTrade[]; selected: string; onPickDay: (date: string) => void }) {
  const r = buildPracticeReport(trades);
  const [shown, setShown] = useState(PAGE);
  if (r.opened_total === 0) return null;

  const g = winRatioGeometry(r.series, W, H);
  const pct = r.win_rate === null ? null : Math.round(r.win_rate * 100);
  const closingDays = r.series.length;
  const summary = pct === null ? "" : `Cumulative win ratio ${pct}% of ${r.closed} finished trades over ${closingDays} day${closingDays === 1 ? "" : "s"}`;

  return (
    <details open className="rounded-md border border-border" aria-label="Practice report">
      <summary className="cursor-pointer select-none min-h-11 px-3 flex items-center text-sm font-semibold">Report, day by day</summary>
      <div className="px-3 pb-4 space-y-5">
        <section aria-label="Win ratio" className="space-y-1">
          {pct === null ? (
            <p className="text-sm text-muted-foreground">No finished trades yet. Open trades are listed under "Open now".</p>
          ) : (
            <>
              <p className="text-sm">
                <span className="font-mono text-base">{pct}%</span>{" "}
                <span className="text-muted-foreground">
                  won, of {r.closed} finished trade{r.closed === 1 ? "" : "s"} over {closingDays} day{closingDays === 1 ? "" : "s"} · {signedR(r.net_r)} · {signedInr(r.net_inr)}
                </span>
              </p>
              {g ? (
                <svg role="img" aria-label={summary} viewBox={`0 0 ${W} ${H}`} className="w-full max-w-md h-24">
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
              ) : (
                <p className="text-xs text-muted-foreground">The line needs trades finishing on at least two different days.</p>
              )}
            </>
          )}
          {r.closed < ENOUGH && <p className="text-xs text-muted-foreground">Fake money. Small samples swing a lot: judge this on at least {ENOUGH} finished trades.</p>}
        </section>

        <section aria-label="Day by day" className="space-y-1">
          <h3 className="text-sm font-semibold">Day by day</h3>
          <p className="text-xs text-muted-foreground">Results sit on the day a trade finished. Press a day to list the trades invested that day, below.</p>
          <ul className="space-y-1">
            {r.days.slice(0, shown).map((d) => (
              <li key={d.date}>
                <button
                  type="button"
                  aria-pressed={d.date === selected}
                  onClick={() => onPickDay(d.date)}
                  className={cn(
                    "w-full min-h-11 px-2 py-1 grid grid-cols-[6.5rem_1fr_auto] items-center gap-x-2 text-left rounded-md text-xs transition-colors duration-150 ease-out motion-reduce:transition-none hover:bg-muted/50 focus-visible:outline focus-visible:outline-2",
                    d.date === selected && "bg-muted",
                  )}
                >
                  <span className="font-mono">{dayLabel(d.date, false)}</span>
                  <span className="truncate text-muted-foreground">
                    opened {d.opened} · finished {d.closed}{d.closed > 0 ? ` · ${rateText(d.win_rate, d.closed)}` : ""}
                  </span>
                  <span className={cn("font-mono text-right", toneClass(d.net_inr))}>{d.closed > 0 ? signedInr(d.net_inr) : "—"}</span>
                </button>
              </li>
            ))}
          </ul>
          {r.days.length > shown && (
            <button type="button" onClick={() => setShown((n) => n + PAGE)} className="min-h-11 px-3 text-sm rounded-md border border-border hover:bg-muted/50">
              Show {Math.min(PAGE, r.days.length - shown)} more days
            </button>
          )}
        </section>

        {r.closed > 0 && (
          <section aria-label="Splits" className="grid sm:grid-cols-2 gap-5">
            <Split title="Buy vs short" rows={r.by_side} />
            <Split title="How trades ended" rows={r.by_exit} />
          </section>
        )}

        {r.open_now.count > 0 && (
          <p className="text-xs text-muted-foreground">
            Open now: {r.open_now.count} trade{r.open_now.count === 1 ? "" : "s"}, {" "}
            <span className={cn("font-mono", toneClass(r.open_now.unrealized_inr))}>{signedInr(r.open_now.unrealized_inr)}</span> so far on paper. Not counted above until they finish.
          </p>
        )}
      </div>
    </details>
  );
}
