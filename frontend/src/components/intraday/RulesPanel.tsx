import { useEffect, useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { dayLabel, type IntradaySettings, type ScheduleStatus } from "@/lib/intraday";
import { friendlyError } from "@/lib/pollJob";

const STOPS = [
  ["or-mid", "Middle of the opening range"],
  ["or-edge", "Far edge of the opening range"],
  ["atr:1.5", "1.5 × the usual 5-minute swing"],
  ["pct:0.8", "0.8% from the entry"],
];
const TARGETS = ["1R", "1.5R", "2R", "3R"];

const field = "min-h-11 w-full rounded-md border border-border bg-background px-2 text-sm";

function When({ e }: { e: { date: string; at: string; ok: boolean; note: string } | null | undefined }) {
  if (!e) return <span className="text-muted-foreground">not yet</span>;
  return (
    <span>
      {dayLabel(e.date)} {e.at.slice(11, 16)} {e.ok ? "" : <span className="text-amber-400">(failed: {e.note})</span>}
    </span>
  );
}

/** Rules, schedule and data source in one closed-by-default block. Saving changes only later runs. */
export function RulesPanel({ settings, universes, schedule, onSaved }: { settings: IntradaySettings | null; universes: Record<string, string>; schedule: ScheduleStatus | null; onSaved: (s: IntradaySettings) => void }) {
  const [form, setForm] = useState<IntradaySettings | null>(settings);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => setForm(settings), [settings]);
  if (!form) return null;
  const set = <K extends keyof IntradaySettings>(k: K, v: IntradaySettings[K]) => setForm({ ...form, [k]: v });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const { data } = await api.intradaySaveSettings(form);
      onSaved(data);
      setMsg("Saved. Days already filed keep the rules they were run with.");
    } catch (err) {
      setMsg(friendlyError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="rounded-md border border-border">
      <summary className="cursor-pointer min-h-11 px-3 flex items-center text-sm font-semibold">Schedule and rules</summary>
      <div className="px-3 pb-4 space-y-5">
        <section aria-label="Schedule" className="text-sm space-y-1">
          <p>
            Opening scan <span className="font-mono">{schedule?.open_time ?? form.open_time}</span>, closing settle <span className="font-mono">{schedule?.close_time ?? form.close_time}</span> IST, on NSE trading days.{" "}
            {schedule?.enabled ? <span className="text-emerald-400">On.</span> : <span className="text-amber-400">Off.</span>}
          </p>
          {schedule && (
            <p className="text-xs text-muted-foreground">
              Last opening scan: <When e={schedule.last.open} /> · last close: <When e={schedule.last.close} /> · next: {schedule.next ? `${schedule.next.kind} at ${schedule.next.at.replace("T", " ")}` : "—"}
            </p>
          )}
          {schedule && !schedule.enabled && (
            <>
              <p className="text-xs text-muted-foreground">{schedule.how_to_enable} Or paste these two lines into <code>crontab -e</code>:</p>
              <pre className="text-xs font-mono bg-muted/50 rounded-md p-2 overflow-x-auto whitespace-pre-wrap break-all">{schedule.cron}</pre>
            </>
          )}
        </section>

        <form onSubmit={save} className="grid sm:grid-cols-2 gap-3 text-sm">
          <label className="space-y-1"><span className="text-xs text-muted-foreground">What to trade</span>
            <select className={field} value={form.universe} onChange={(e) => set("universe", e.target.value)}>
              {Object.entries(universes).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Buy, short or both</span>
            <select className={field} value={form.sides} onChange={(e) => set("sides", e.target.value as IntradaySettings["sides"])}>
              <option value="both">Both</option><option value="long">Buy only</option><option value="short">Short only</option>
            </select></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Most trades a day</span>
            <input className={field} type="number" min={1} max={20} value={form.max_trades} onChange={(e) => set("max_trades", Number(e.target.value))} /></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Most you would lose per trade (₹)</span>
            <input className={field} type="number" min={100} step={100} value={form.risk_inr} onChange={(e) => set("risk_inr", Number(e.target.value))} /></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Opening range</span>
            <select className={field} value={form.or_minutes} onChange={(e) => set("or_minutes", Number(e.target.value))}>
              <option value={15}>First 15 minutes</option><option value={30}>First 30 minutes</option>
            </select></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Cut-loss</span>
            <select className={field} value={form.stop} onChange={(e) => set("stop", e.target.value)}>
              {STOPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Take-profit (times the risk)</span>
            <select className={field} value={form.target} onChange={(e) => set("target", e.target.value)}>
              {TARGETS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select></label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Volume needed at the break</span>
            <select className={field} value={form.min_vol_x ?? ""} onChange={(e) => set("min_vol_x", e.target.value === "" ? null : Number(e.target.value))}>
              <option value="">Any</option><option value={1}>At least the range's average</option><option value={1.5}>1.5× the range's average</option>
            </select></label>
          <label className="flex items-start gap-2 min-h-11 sm:col-span-2">
            <input type="checkbox" className="mt-1 h-4 w-4" checked={form.market_filter === "with_nifty"} onChange={(e) => set("market_filter", e.target.checked ? "with_nifty" : "off")} />
            <span>Only trade the way the Nifty has broken its own opening range<span className="block text-xs text-muted-foreground">Skips buys while the Nifty is below its range and shorts while it is above. Optional; compare days with and without it.</span></span>
          </label>
          <label className="space-y-1"><span className="text-xs text-muted-foreground">Refresh prices on its own every (minutes, 0 = only when I ask)</span>
            <input className={field} type="number" min={0} max={60} value={form.live_refresh_minutes} onChange={(e) => set("live_refresh_minutes", Number(e.target.value))} /></label>
          <div className="flex items-end gap-3 sm:col-span-2">
            <button type="submit" disabled={busy} className="min-h-11 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60 inline-flex items-center gap-2">
              {busy && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}Save rules
            </button>
            {msg && <span role="status" className="text-xs text-muted-foreground">{msg}</span>}
          </div>
        </form>

        <p className="text-xs text-muted-foreground">
          Prices come from Yahoo (free, delayed a little). Zerodha Kite can replace that source later without changing any of these rules.
          Everything on this page is a paper trade simulated from 5-minute prices, net of NSE intraday charges. Nothing here places an order.
        </p>
      </div>
    </details>
  );
}
