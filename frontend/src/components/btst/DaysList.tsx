import { REASON_TEXT, signedPct, tone, type BtstDayRow } from "@/lib/btst";
import { cn } from "@/lib/utils";

/** Filed live days, newest first. Each row opens that day. */
export function DaysList({ rows, selected, onPick }: { rows: BtstDayRow[]; selected: string | null; onPick: (d: string) => void }) {
  if (rows.length === 0) return null;
  return (
    <section aria-label="Live days" className="space-y-2">
      <h2 className="text-sm font-semibold">Live days</h2>
      <ul className="rounded-md border border-border px-1 max-h-96 overflow-auto">
        {rows.map((r) => (
          <li key={r.date} className="border-b border-border last:border-0">
            <button type="button" onClick={() => onPick(r.date)} aria-pressed={selected === r.date}
              className={cn("w-full min-h-11 px-2 grid grid-cols-[6.5rem_minmax(0,1fr)_auto] items-center gap-3 text-left text-sm rounded-md hover:bg-muted/50 transition-colors duration-150 ease-out motion-reduce:transition-none", selected === r.date && "bg-muted/60")}>
              <span className="font-mono text-xs">{r.date}</span>
              <span className="truncate">{r.symbol ?? <span className="text-muted-foreground">No trade</span>}{r.reason && <span className="text-xs text-muted-foreground"> · {REASON_TEXT[r.reason]}</span>}</span>
              <span className={cn("font-mono text-xs", tone(r.net_pct) === "good" ? "text-emerald-400" : tone(r.net_pct) === "bad" ? "text-rose-400" : "text-muted-foreground")}>
                {r.net_pct !== null ? signedPct(r.net_pct) : r.symbol ? (r.status === "settled" ? "—" : "open") : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
