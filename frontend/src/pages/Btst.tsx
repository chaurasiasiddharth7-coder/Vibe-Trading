import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Sunset } from "lucide-react";
import { ApiKeyGate } from "@/components/desk/ApiKeyGate";
import { CandidateList } from "@/components/btst/CandidateList";
import { DaysList } from "@/components/btst/DaysList";
import { HowItWorks } from "@/components/btst/HowItWorks";
import { PickCard } from "@/components/btst/PickCard";
import { TrackRecord } from "@/components/btst/TrackRecord";
import { api } from "@/lib/api";
import { todayIst } from "@/lib/intraday";
import {
  loadCapital, saveCapital, scanLine, trustSentence,
  type BtstDayRow, type BtstJob, type BtstRecord, type BtstSchedule, type BtstStats,
} from "@/lib/btst";
import { friendlyError } from "@/lib/pollJob";

const btn = "min-h-11 px-3 rounded-md border border-border text-sm hover:bg-muted/50 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 inline-flex items-center gap-2 transition-colors duration-150 ease-out motion-reduce:transition-none";

export function Btst() {
  const [latest, setLatest] = useState<BtstRecord | null>(null);
  const [shown, setShown] = useState<BtstRecord | null>(null);
  const [rows, setRows] = useState<BtstDayRow[]>([]);
  const [stats, setStats] = useState<{ live: BtstStats; replay: BtstStats } | null>(null);
  const [schedule, setSchedule] = useState<BtstSchedule | null>(null);
  const [storeLast, setStoreLast] = useState<string | null>(null);
  const [job, setJob] = useState<BtstJob | null>(null);
  const [capital, setCapital] = useState(loadCapital);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const load = useCallback(async () => {
    const [t, d, s] = await Promise.all([api.btstToday(), api.btstDays("live"), api.btstStats()]);
    if (!alive.current) return;
    setLatest(t.data);
    setShown((cur) => (cur && t.data && cur.date !== t.data.date ? cur : t.data));
    setSchedule(t.schedule);
    setStoreLast(t.store_last);
    setJob(t.job && !t.job.finished ? t.job : null);
    setRows(d.data);
    setStats(s.data);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await load();
      } catch (e) {
        if (alive.current) setError(friendlyError(e));
      } finally {
        if (alive.current) setLoading(false);
      }
    })();
  }, [load]);

  const waitJob = async () => {
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      if (!alive.current) return;
      const j = (await api.btstJob()).data;
      setJob(j);
      if (!j || j.finished) {
        if (j?.error) setError(j.error);
        return;
      }
    }
  };

  const start = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      setJob({ name: "…", finished: false, done: 0, total: 0, error: null, result: null });
      await waitJob();
      setShown(null);
      await load();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      if (alive.current) setJob(null);
    }
  };

  const settle = async (d: string) => {
    setError(null);
    try {
      await api.btstSettle(d);
      setShown(null);
      await load();
    } catch (e) {
      setError(friendlyError(e));
    }
  };

  const openDay = async (d: string) => {
    setError(null);
    try {
      setShown((await api.btstDay(d, "live")).data);
    } catch (e) {
      setError(friendlyError(e));
    }
  };

  const busy = job !== null;
  // a day settles after the next session closes, so today's scan never offers it
  const unsettled = latest && latest.status !== "settled" && latest.date < todayIst() ? latest.date : null;

  return (
    <main className="min-h-[calc(100vh-2rem)] p-4 sm:p-6 space-y-5 max-w-6xl">
      <header className="space-y-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Sunset className="h-6 w-6 text-emerald-500" aria-hidden />
            Overnight (BTST)
          </h1>
          <p className="text-sm text-muted-foreground max-w-2xl">
            Buy one of today's top gainers near 15:20, sell tomorrow at +1% or at a −2% safety stop. Each candidate shows the warnings that made similar trades fail.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={btn} disabled={busy} onClick={() => start(api.btstScan)}>
            {job?.name === "scan" && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
            {job?.name === "scan" ? "Working…" : "Scan now"}
          </button>
          {unsettled && (
            <button type="button" className={btn} disabled={busy} onClick={() => settle(unsettled)}>Settle {unsettled}</button>
          )}
          <p className="text-xs text-muted-foreground ml-auto" aria-live="polite">
            {schedule ? `Scan ${schedule.scan_time}, settle ${schedule.settle_time} IST · schedule ${schedule.enabled ? "on" : "off"}` : ""}
            {schedule?.last.scan ? ` · last scan ${schedule.last.scan.date}${schedule.last.scan.ok ? "" : " (failed)"}` : ""}
            {storeLast ? ` · stored prices to ${storeLast}` : ""}
          </p>
        </div>
        {busy && (
          <div className="h-0.5 w-full bg-muted overflow-hidden" aria-hidden>
            <div className="h-full w-full bg-emerald-500 origin-left transition-transform duration-300 ease-out motion-reduce:transition-none"
              style={{ transform: `scaleX(${job && job.total ? Math.max(0.05, job.done / job.total) : 0.5})` }} />
          </div>
        )}
      </header>

      <ApiKeyGate />
      {error && <p role="alert" className="text-sm text-amber-400 rounded-md bg-muted/50 p-3">{error}</p>}

      <p className="text-sm rounded-md bg-muted/50 p-3" aria-label="How much to trust it">{trustSentence(stats?.live ?? null, stats?.replay ?? null)}</p>

      <div className="grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6">
        <section aria-label="The pick" className="space-y-4 min-w-0">
          <div className="flex items-baseline gap-2">
            <h2 className="text-sm font-semibold">{shown && latest && shown.date !== latest.date ? `Day ${shown.date}` : "Latest scan"}</h2>
            {shown && latest && shown.date !== latest.date && (
              <button type="button" className="text-xs underline min-h-11" onClick={() => setShown(latest)}>back to latest</button>
            )}
          </div>
          {loading ? (
            <div className="space-y-2" aria-busy><div className="h-40 rounded-md bg-muted/50" /><div className="h-14 rounded-md bg-muted/50" /></div>
          ) : (
            <>
              <PickCard rec={shown} capital={capital} onCapital={(v) => { setCapital(v); saveCapital(v); }} />
              {shown && <p className="text-xs text-muted-foreground">{scanLine(shown)}</p>}
              {shown?.notes?.map((n) => <p key={n} className="text-xs text-amber-400">{n}</p>)}
              <CandidateList rec={shown} />
            </>
          )}
        </section>
        <div className="space-y-6 min-w-0">
          <TrackRecord stats={stats} busy={busy} onReplay={() => start(() => api.btstReplay(3))}
            replayNote={storeLast ? undefined : "This server has no NSE store yet: run scripts/nse_pull.py first."} />
          <DaysList rows={rows} selected={shown?.date ?? null} onPick={openDay} />
        </div>
      </div>

      <HowItWorks />
      {schedule && !schedule.enabled && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer min-h-11 flex items-center">Turn on the daily schedule</summary>
          <p className="pb-2">Set BTST_SCHEDULER=1 on the server, or add these lines to the system cron:</p>
          <pre className="font-mono whitespace-pre-wrap bg-muted/50 rounded-md p-2">{schedule.cron}</pre>
        </details>
      )}
    </main>
  );
}
