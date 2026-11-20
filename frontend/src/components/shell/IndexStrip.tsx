import { useQuotes } from "@/hooks/useQuotes";
import { cn } from "@/lib/utils";
import { fmtChange, fmtPrice, tone } from "@/lib/watchlistApi";

/** NIFTY 50 and NIFTY BANK at the top of every page. Live from Kite on the owner's desk, else last close. */
export function IndexStrip({ className }: { className?: string }) {
  const { data } = useQuotes([], true);
  const rows = data?.indices ?? [];
  return (
    <div className={cn("grid grid-cols-2 gap-4 px-4 py-2", className)} aria-label="Market indices">
      {(rows.length ? rows : [{ symbol: "NIFTY 50" }, { symbol: "NIFTY BANK" }]).map((r) => {
        const q = r as { symbol: string; ltp?: number | null; change?: number | null; change_pct?: number | null; as_of?: string };
        return (
          <div key={q.symbol} className="min-w-0">
            <p className="text-[13px] text-foreground/90 uppercase tracking-wide">
              {q.symbol}
              {!data?.live && q.as_of && <span className="ms-1.5 normal-case tracking-normal text-[11px] text-muted-foreground">close {q.as_of}</span>}
            </p>
            <p className="text-[13px] tabular-nums truncate">
              <span className={tone(q.change)}>{fmtPrice(q.ltp)}</span>{" "}
              <span className="text-muted-foreground">{q.change != null ? fmtChange({ change: q.change, change_pct: q.change_pct ?? null }) : ""}</span>
            </p>
          </div>
        );
      })}
    </div>
  );
}
