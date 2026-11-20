import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { desk, rupees, tone, type GuardStatus } from "@/lib/deskApi";
import { cn } from "@/lib/utils";

const n = (v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? null : Number(v));

/** One line at the top of the Stocks page: is the exit guard watching, and where is today's floor. */
export function GuardStrip() {
  const [g, setG] = useState<GuardStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      desk
        .guard()
        .then((r) => alive && setG(r.data))
        .catch(() => alive && setG(null));
    void load();
    const t = window.setInterval(load, 30_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (!g || (!g.available && !g.kite)) return null;
  const day = g.day ?? null;
  const mtm = n(day?.last_mtm);
  const peak = n(day?.peak_mtm);
  const floor = n(day?.floor);
  const cap = g.capital_inr ? -0.015 * g.capital_inr : null;
  const halted = day?.loss_cap_hit || day?.lock_hit;

  return (
    <section aria-label="Profit Guard" className="rounded-md border border-border px-3 py-2 text-sm flex flex-wrap items-center gap-x-5 gap-y-1">
      <span className="flex items-center gap-1.5 font-medium">
        <ShieldCheck className={cn("h-4 w-4", g.available ? "text-emerald-600" : "text-muted-foreground")} aria-hidden />
        Profit Guard
      </span>
      {!g.available ? (
        <span className="text-muted-foreground">{g.reason ?? "Not running on this server."}</span>
      ) : (
        <>
          <span>Open <span className="font-mono">{g.open?.length ?? 0}</span></span>
          <span>Day <span className={cn("font-mono", tone(mtm))}>{rupees(mtm, 0, true)}</span></span>
          <span>Peak <span className="font-mono">{rupees(peak, 0, true)}</span></span>
          <span title="If the day falls back to this, everything closes">
            Floor <span className="font-mono">{floor === null ? "not armed" : rupees(floor, 0, true)}</span>
          </span>
          {cap !== null && <span title="Day loss cap">Loss cap <span className="font-mono">{rupees(cap)}</span></span>}
          {halted && <span className="text-amber-700 dark:text-amber-300 font-medium">{day?.loss_cap_hit ? "Loss cap hit: no new trades today" : "Profit locked: no new trades today"}</span>}
        </>
      )}
      <span className={cn("ms-auto text-xs", g.kite?.connected ? "text-emerald-600" : "text-amber-700 dark:text-amber-300")}>
        {g.kite?.connected ? `Kite live until ${g.kite.expires_at_ist}` : "Kite not logged in on this server"}
      </span>
      <span className="text-xs text-muted-foreground">Paper only</span>
    </section>
  );
}
