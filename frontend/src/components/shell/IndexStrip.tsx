import { useQuotes } from "@/hooks/useQuotes";
import { cn } from "@/lib/utils";
import { fmtChange, fmtPrice, tone, type Quote } from "@/lib/watchlistApi";

type IndexRow = Quote & { source?: string; stale?: boolean };

const SHORT: Record<string, string> = { "NIFTY 50": "NIFTY 50", "NIFTY BANK": "BANK NIFTY", "INDIA VIX": "INDIA VIX" };

function today(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
}

function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** NIFTY 50, BANK NIFTY and INDIA VIX on every page, swipeable on a phone. Live from Kite on the owner's desk. */
export function IndexStrip({ className }: { className?: string }) {
  const { data } = useQuotes([], true);
  const rows: IndexRow[] = data?.indices?.length
    ? (data.indices as IndexRow[])
    : (["NIFTY 50", "NIFTY BANK", "INDIA VIX"].map((s) => ({ symbol: s, exchange: "INDEX", ltp: null, prev_close: null, change: null, change_pct: null })) as IndexRow[]);
  const now = today();
  return (
    <div className={cn("flex items-stretch gap-5 overflow-x-auto no-scrollbar px-4 py-2", className)} aria-label="Market indices">
      {data && (
        <span
          className={cn(
            "shrink-0 self-center inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
            data.live ? "bg-success/15 text-success" : "bg-muted text-muted-foreground",
          )}
        >
          <span className={cn("h-1.5 w-1.5 rounded-full", data.live ? "bg-success animate-pulse motion-reduce:animate-none" : "bg-muted-foreground")} aria-hidden />
          {data.live ? "Live" : "Close"}
        </span>
      )}
      {rows.map((q) => {
        const vix = q.symbol === "INDIA VIX";
        const old = q.as_of && q.as_of < now && !data?.live;
        return (
          <div key={q.symbol} className="shrink-0 min-w-0">
            <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide whitespace-nowrap">
              {SHORT[q.symbol] ?? q.symbol}
              {old && q.as_of && (
                <span className={cn("ms-1 normal-case tracking-normal", q.stale ? "text-danger" : "")} title={q.stale ? "Older data: latest close not available" : undefined}>
                  · {shortDate(q.as_of)}
                </span>
              )}
            </p>
            <p className="text-[13px] tabular-nums whitespace-nowrap">
              {/* VIX up = fear up, so it reads the other way round */}
              <span className={cn("font-semibold", vix ? tone(q.change == null ? null : -q.change) : tone(q.change))}>{fmtPrice(q.ltp)}</span>{" "}
              <span className="text-muted-foreground text-xs">{q.change != null ? fmtChange(q) : ""}</span>
            </p>
          </div>
        );
      })}
    </div>
  );
}
