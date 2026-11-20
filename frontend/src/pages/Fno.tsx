import { useCallback, useEffect, useMemo, useState } from "react";
import { desk, rupees, tone, type ChainData, type FnoData, type FnoRow } from "@/lib/deskApi";
import { useSession } from "@/lib/session";
import { Empty, Note, Page, PageHeader, Tiles } from "@/components/shell/ui";
import { cn } from "@/lib/utils";

const num = (v: string | number | null | undefined) => (v === null || v === undefined || v === "" ? null : Number(v));
const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];

/** Options and futures: day MTM against the profit-lock floor, positions by underlying, and a compact chain. */
export function Fno() {
  const { prefs } = useSession();
  const [data, setData] = useState<FnoData | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await desk.fno();
      setData(res.data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load F&O.");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 15_000);
    return () => window.clearInterval(t);
  }, [load]);

  const byUnderlying = useMemo(() => {
    const m = new Map<string, FnoRow[]>();
    for (const r of data?.rows ?? []) m.set(r.underlying || r.symbol, [...(m.get(r.underlying || r.symbol) ?? []), r]);
    return [...m.entries()];
  }, [data]);

  const floor = num(data?.floor ?? null);
  const peak = num(data?.peak ?? null);

  return (
    <Page>
      <PageHeader title="F&O" sub="Options and futures, with the exit plan for each position. Practice only: no orders are sent." />
      <Tiles
        chosen={prefs.tiles_by_page.fno}
        tiles={[
          { id: "mtm", label: "Day MTM", value: rupees(data?.day_mtm ?? null, 0, true), tone: tone(data?.day_mtm) },
          { id: "floor", label: "Profit lock floor", value: floor === null ? "Not armed" : rupees(floor, 0, true), hint: "Close everything if the day falls back to this" },
          { id: "margin", label: "Margin used", value: rupees(data?.margin_used ?? null) },
          {
            id: "expiry",
            label: "Nearest expiry",
            value: data?.nearest_expiry_days == null ? "—" : data.nearest_expiry_days === 0 ? "Today" : `${data.nearest_expiry_days} days`,
            tone: data?.expiry_today ? "text-amber-600" : undefined,
          },
          { id: "peak", label: "Day peak", value: rupees(peak, 0, true) },
          { id: "open", label: "Open positions", value: String(data?.rows.length ?? 0) },
        ]}
      />
      {data?.expiry_today && (
        <Note tone="warn">Expiry day: option positions are closed at 14:30 by the exit rules, before the last-hour swings.</Note>
      )}
      {data?.note && <Note>{data.note}</Note>}
      {error && <Note tone="bad">{error}</Note>}

      {!data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : byUnderlying.length === 0 ? (
        <Empty>No open F&amp;O positions.</Empty>
      ) : (
        byUnderlying.map(([und, rows]) => (
          <section key={und} className="space-y-2">
            <h2 className="text-sm font-semibold flex gap-3">
              {und}
              <span className={cn("font-mono", tone(rows.reduce((a, r) => a + (r.pnl ?? 0), 0)))}>
                {rupees(rows.reduce((a, r) => a + (r.pnl ?? 0), 0), 0, true)}
              </span>
            </h2>
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="text-start px-3 py-2">Contract</th>
                    <th className="text-end px-3 py-2">Qty</th>
                    <th className="text-end px-3 py-2">Entry</th>
                    <th className="text-end px-3 py-2">LTP</th>
                    <th className="text-end px-3 py-2">Stop now</th>
                    <th className="text-end px-3 py-2">Target</th>
                    <th className="text-end px-3 py-2">Locked</th>
                    <th className="text-end px-3 py-2">R</th>
                    <th className="text-start px-3 py-2">State</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((r) => (
                    <tr key={r.symbol}>
                      <td className="px-3 py-2">
                        <span className="font-medium">{r.symbol}</span>
                        <span className={cn("ms-2 text-xs font-semibold", r.side === "LONG" ? "text-sky-600" : "text-orange-600")} title={r.side === "LONG" ? "Long" : "Short"}>
                          {r.side === "LONG" ? "B" : "S"}
                        </span>
                        <div className="text-xs text-muted-foreground">{r.source === "paper" ? "practice plan" : "Zerodha"}{r.expiry ? ` · exp ${r.expiry}` : ""}</div>
                      </td>
                      <td className="px-3 py-2 text-end font-mono">{r.qty}</td>
                      <td className="px-3 py-2 text-end font-mono">{rupees(r.entry, 2)}</td>
                      <td className="px-3 py-2 text-end font-mono">{rupees(r.ltp, 2)}</td>
                      <td className="px-3 py-2 text-end font-mono text-rose-600 dark:text-rose-400">{rupees(r.stop, 2)}</td>
                      <td className="px-3 py-2 text-end font-mono text-emerald-600 dark:text-emerald-400">{rupees(r.target, 2)}</td>
                      <td className={cn("px-3 py-2 text-end font-mono", tone(r.locked))}>{rupees(r.locked, 0, true)}</td>
                      <td className={cn("px-3 py-2 text-end font-mono", tone(r.r_now))}>{r.r_now == null ? "—" : `${r.r_now > 0 ? "+" : ""}${r.r_now}R`}</td>
                      <td className="px-3 py-2 text-xs">{r.state ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}

      <OptionChain held={new Set((data?.rows ?? []).map((r) => r.symbol))} sides={Object.fromEntries((data?.rows ?? []).map((r) => [r.symbol, r.side]))} />
    </Page>
  );
}

function OptionChain({ held, sides }: { held: Set<string>; sides: Record<string, string> }) {
  const [und, setUnd] = useState("NIFTY");
  const [expiry, setExpiry] = useState<string | undefined>(undefined);
  const [chain, setChain] = useState<ChainData | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    desk
      .chain(und, expiry)
      .then((r) => alive && (setChain(r.data), setError("")))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Couldn't load the chain."));
    return () => {
      alive = false;
    };
  }, [und, expiry]);

  const mark = (sym?: string) =>
    sym && held.has(sym) ? (
      <span className={cn("ms-1 text-[10px] font-bold", sides[sym] === "SHORT" ? "text-orange-600" : "text-sky-600")} title="Your position">
        P
      </span>
    ) : null;

  return (
    <section className="space-y-2" aria-label="Option chain">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold me-2">Option chain</h2>
        <select className="min-h-9 rounded-md border border-border bg-background px-2 text-sm" value={und} onChange={(e) => { setUnd(e.target.value); setExpiry(undefined); }} aria-label="Underlying">
          {UNDERLYINGS.map((u) => <option key={u}>{u}</option>)}
        </select>
        {chain?.expiries && (
          <select className="min-h-9 rounded-md border border-border bg-background px-2 text-sm" value={chain.expiry} onChange={(e) => setExpiry(e.target.value)} aria-label="Expiry">
            {chain.expiries.map((x) => <option key={x}>{x}</option>)}
          </select>
        )}
      </div>
      {error && <Note tone="bad">{error}</Note>}
      {chain && !chain.available ? (
        <Empty>{chain.reason}</Empty>
      ) : chain?.rows ? (
        <>
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-xs font-mono">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th className="px-2 py-1.5 text-end">Call OI</th>
                  <th className="px-2 py-1.5 text-end" title="Open interest added since today's low">OI +low</th>
                  <th className="px-2 py-1.5 text-end">Call LTP</th>
                  <th className="px-2 py-1.5 text-center">Strike</th>
                  <th className="px-2 py-1.5 text-start">Put LTP</th>
                  <th className="px-2 py-1.5 text-start" title="Open interest added since today's low">OI +low</th>
                  <th className="px-2 py-1.5 text-start">Put OI</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {chain.rows.map((r) => {
                  const atm = r.strike === chain.atm;
                  const itmCall = chain.spot ? r.strike < chain.spot : false;
                  return (
                    <tr key={r.strike} className={cn(atm && "bg-amber-50 dark:bg-amber-950/30 font-semibold")} aria-current={atm ? "true" : undefined}>
                      <td className={cn("px-2 py-1 text-end", itmCall && "bg-muted/40")}>{r.ce?.oi?.toLocaleString("en-IN") ?? "—"}</td>
                      <td className={cn("px-2 py-1 text-end", tone(r.ce?.oi_change))}>{r.ce?.oi_change?.toLocaleString("en-IN") ?? "—"}</td>
                      <td className={cn("px-2 py-1 text-end", itmCall && "bg-muted/40")}>{r.ce?.ltp?.toFixed(2) ?? "—"}{mark(r.ce?.symbol)}</td>
                      <td className="px-2 py-1 text-center">{r.strike}{atm ? " ATM" : ""}</td>
                      <td className={cn("px-2 py-1", !itmCall && "bg-muted/40")}>{r.pe?.ltp?.toFixed(2) ?? "—"}{mark(r.pe?.symbol)}</td>
                      <td className={cn("px-2 py-1", tone(r.pe?.oi_change))}>{r.pe?.oi_change?.toLocaleString("en-IN") ?? "—"}</td>
                      <td className={cn("px-2 py-1", !itmCall && "bg-muted/40")}>{r.pe?.oi?.toLocaleString("en-IN") ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground font-mono">
            Spot {chain.spot ?? "—"} · PCR {chain.pcr ?? "—"} · Max pain {chain.max_pain ?? "—"} · shaded = in the money · OI +low = open interest added since today’s low · P = your position (blue long, orange short)
          </p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
    </section>
  );
}
