import { rateText, signedInr, signedR, tone, type DayRecord } from "@/lib/intraday";
import { cn } from "@/lib/utils";

function Stat({ label, value, sub, kind }: { label: string; value: string; sub?: string; kind?: "good" | "bad" }) {
  return (
    <div className="rounded-md bg-muted/50 p-3 min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-mono text-base break-words", kind === "good" && "text-emerald-400", kind === "bad" && "text-rose-400")}>{value}</dd>
      {sub && <dd className="font-mono text-xs text-muted-foreground break-words">{sub}</dd>}
    </div>
  );
}

const pct = (n: number | undefined) => (n === undefined ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`);

/** The whole day in six numbers. Nothing shows a result until the day has data. */
export function DayStrip({ rec }: { rec: DayRecord | null }) {
  const s = rec?.summary;
  const has = !!rec && rec.status !== "empty";
  const n50 = rec?.day?.nifty50;
  const bank = rec?.day?.niftybank;
  const decided = (s?.taken ?? 0) - (s?.open ?? 0);
  return (
    <section aria-label="The day at a glance">
      <dl className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Stat
          label="Kind of day"
          value={rec?.day?.label ?? "—"}
          sub={n50 ? `Nifty 50 ${pct(n50.metrics.open_to_close_pct)} · range ${n50.metrics.range_pct?.toFixed(2)}%` : undefined}
        />
        <Stat
          label={rec?.status === "settled" ? "Result for the day" : "Result so far"}
          value={has && s ? signedInr(s.net_inr) : "—"}
          sub={has && s ? signedR(s.net_r) : undefined}
          kind={has && s ? tone(s.net_inr) : undefined}
        />
        <Stat label="Trades taken" value={has && s ? String(s.taken) : "—"} sub={has ? `of ${rec?.planned.length ?? 0} symbols planned` : undefined} />
        <Stat
          label="Won / lost / flat"
          value={has && s ? `${s.wins} / ${s.losses} / ${s.flat}` : "—"}
          sub={has && s ? (s.open ? `${s.open} still open` : rateText(s.win_rate, decided)) : undefined}
        />
        <Stat
          label="Best · worst trade"
          value={has && s?.best ? `${s.best.symbol} ${signedInr(s.best.pnl_inr)}` : "—"}
          sub={has && s?.worst ? `${s.worst.symbol} ${signedInr(s.worst.pnl_inr)}` : undefined}
        />
        <Stat label="Nifty Bank" value={bank?.label ?? "—"} sub={bank ? `${pct(bank.metrics.open_to_close_pct)} · range ${bank.metrics.range_pct?.toFixed(2)}%` : undefined} />
      </dl>
    </section>
  );
}
