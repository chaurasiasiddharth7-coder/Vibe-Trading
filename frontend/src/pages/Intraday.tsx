import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { ApiKeyGate } from "@/components/desk/ApiKeyGate";
import { DayStrip } from "@/components/intraday/DayStrip";
import { DaysPanel } from "@/components/intraday/DaysPanel";
import { RulesPanel } from "@/components/intraday/RulesPanel";
import { TradeList } from "@/components/intraday/TradeList";
import { TradeSearch } from "@/components/intraday/TradeSearch";
import { api } from "@/lib/api";
import {
  dayLabel, lastWeekday, shiftWeekday, todayIst, waitForIdle,
  type DayRecord, type DayRow, type IntradaySettings, type IntradayStats, type RunKind, type ScheduleStatus,
} from "@/lib/intraday";
import { friendlyError } from "@/lib/pollJob";
import { cn } from "@/lib/utils";

const RUN_LABEL: Record<RunKind, string> = {
  open: "Plan open",
  refresh: "Refresh prices",
  close: "Close and settle",
  replay: "Replay this day",
};

const btn = "min-h-11 px-3 rounded-md border border-border text-sm hover:bg-muted/50 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 inline-flex items-center gap-2 transition-colors duration-150 ease-out motion-reduce:transition-none";

export function Intraday() {
  const today = todayIst();
  const [date, setDate] = useState(() => lastWeekday(today));
  const [rec, setRec] = useState<DayRecord | null>(null);
  const [days, setDays] = useState<DayRow[]>([]);
  const [stats, setStats] = useState<IntradayStats | null>(null);
  const [settings, setSettings] = useState<IntradaySettings | null>(null);
  const [universes, setUniverses] = useState<Record<string, string>>({});
  const [schedule, setSchedule] = useState<ScheduleStatus | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  // StrictMode mounts, unmounts and re-mounts: the flag must come back on, not stay off.
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const loadLists = useCallback(async () => {
    const [d, s] = await Promise.all([api.intradayDays(), api.intradayStats()]);
    if (!alive.current) return;
    setDays(d.data);
    setStats(s.data);
  }, []);

  const loadDay = useCallback(async (d: string) => {
    const r = await api.intradayDay(d);
    if (!alive.current) return;
    setRec(r.data);
    setRunning(r.running);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [st, sc] = await Promise.all([api.intradaySettings(), api.intradaySchedule()]);
        if (cancelled) return;
        setSettings(st.data);
        setUniverses(st.universes);
        setSchedule(sc.data);
        await Promise.all([loadDay(date), loadLists()]);
      } catch (e) {
        if (!cancelled) setError(friendlyError(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the first load only; day changes go through pick()
  }, []);

  const pick = useCallback(async (d: string) => {
    setDate(d);
    setError(null);
    try {
      await loadDay(d);
    } catch (e) {
      setError(friendlyError(e));
    }
  }, [loadDay]);

  const run = async (kind: RunKind) => {
    setError(null);
    setRunning(kind);
    try {
      await api.intradayRun(date, kind);
      await waitForIdle(() => api.intradayDay(date), (r) => { if (alive.current) setRec(r.data); }, () => alive.current);
      await Promise.all([loadDay(date), loadLists()]);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      if (alive.current) setRunning(null);
    }
  };

  const backfill = async (force = false) => {
    setError(null);
    setRunning("backfill");
    try {
      await api.intradayBackfill(force ? 90 : 30, force);
      await waitForIdle(() => api.intradayDay(today), () => undefined, () => alive.current);
      await Promise.all([loadDay(date), loadLists()]);
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      if (alive.current) setRunning(null);
    }
  };

  const isToday = date === today;
  const busy = running !== null;
  const kinds: RunKind[] = isToday ? ["open", "refresh", "close"] : ["replay"];

  return (
    <main className="min-h-[calc(100vh-2rem)] p-4 sm:p-6 space-y-5 max-w-6xl">
      <header className="space-y-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Activity className="h-6 w-6 text-emerald-500" aria-hidden />
            Intraday
          </h1>
          <p className="text-sm text-muted-foreground max-w-2xl">
            One trading day at a time: what was planned at the open, how each trade moved, and how the day ended. All trades here use fake money.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={btn} onClick={() => pick(shiftWeekday(date, -1))} aria-label="Previous day"><ChevronLeft className="h-4 w-4" aria-hidden /></button>
          <label className="sr-only" htmlFor="intraday-date">Day</label>
          <input id="intraday-date" type="date" max={today} value={date} onChange={(e) => e.target.value && pick(e.target.value)}
            className="min-h-11 rounded-md border border-border bg-background px-2 font-mono text-sm" />
          <button type="button" className={btn} onClick={() => pick(shiftWeekday(date, 1))} disabled={date >= today} aria-label="Next day"><ChevronRight className="h-4 w-4" aria-hidden /></button>
          <span className="text-sm text-muted-foreground mr-2">{dayLabel(date)}</span>
          <div className="flex flex-wrap gap-2 ml-auto">
            {kinds.map((k) => (
              <button key={k} type="button" className={btn} disabled={busy} onClick={() => run(k)}>
                {running === k && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                {running === k ? "Working…" : RUN_LABEL[k]}
              </button>
            ))}
          </div>
        </div>
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {schedule ? `Opening scan ${schedule.open_time}, closing settle ${schedule.close_time} IST · ${schedule.enabled ? "on" : "off"}` : ""}
          {rec?.as_of ? ` · prices as of ${rec.as_of}` : ""}
          {rec?.runs.length ? ` · last run: ${rec.runs[rec.runs.length - 1].kind}${rec.runs[rec.runs.length - 1].ok ? "" : " (failed)"}` : ""}
          {running ? ` · ${running === "backfill" ? "filing past days" : "working"}` : ""}
        </p>
        {busy && <div className="h-0.5 w-full bg-muted overflow-hidden" aria-hidden><div className="h-full w-full bg-emerald-500 origin-left scale-x-50 transition-transform duration-300 ease-out motion-reduce:transition-none" /></div>}
      </header>

      <ApiKeyGate />
      {error && <p role="alert" className="text-sm text-amber-400 rounded-md bg-muted/50 p-3">{error}</p>}

      <DayStrip rec={rec} />

      <div className="grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6">
        <section aria-label="This day" className="space-y-3 min-w-0">
          <h2 className="text-sm font-semibold">This day{rec?.universe ? ` · ${rec.universe.label}` : ""}</h2>
          {loading ? (
            <div className="space-y-2" aria-busy><div className="h-14 rounded-md bg-muted/50" /><div className="h-14 rounded-md bg-muted/50" /></div>
          ) : rec && rec.status !== "empty" ? (
            <>
              {rec.universe && rec.universe.eligible > rec.universe.covered && (
                <p className="text-xs text-amber-400">Covered {rec.universe.covered} of {rec.universe.eligible} liquid NSE stocks (the busiest ones). Each symbol costs one price request.</p>
              )}
              <TradeList rec={rec} />
            </>
          ) : (
            <div className="rounded-md bg-muted/50 p-4 text-sm space-y-3">
              <p>No record for this day yet. {isToday ? "Press Plan open to build it," : "Press Replay this day to build it from stored prices,"} or let the schedule file it.</p>
              {days.length === 0 && (
                <button type="button" className={cn(btn, "bg-background")} disabled={busy} onClick={() => backfill()}>
                  {running === "backfill" && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                  Fill the last 30 days from stored prices
                </button>
              )}
            </div>
          )}
          {rec?.note && <p className="text-xs text-muted-foreground">{rec.note}</p>}
        </section>

        <DaysPanel days={days} stats={stats} selected={date} onPick={pick} onRefile={() => backfill(true)} busy={busy} />
      </div>

      <TradeSearch onOpenDay={(d) => { void pick(d); window.scrollTo({ top: 0, behavior: "auto" }); }} />

      {rec?.day && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer min-h-11 flex items-center">How the kind of day is decided</summary>
          <p className="pb-2 max-w-2xl">{rec.day.rule}</p>
        </details>
      )}

      <RulesPanel settings={settings} universes={universes} schedule={schedule} onSaved={setSettings} />
    </main>
  );
}
