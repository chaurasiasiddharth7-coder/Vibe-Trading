import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { Check, ChevronDown, Maximize2, Minimize2, X } from "lucide-react";
import { useDarkMode } from "@/hooks/useDarkMode";
import {
  chartSignalsApi,
  isForecast,
  loadPicked,
  savePicked,
  type ChartStrategy,
  type SignalForecast,
  type SignalTrade,
  type StrategySignals,
} from "@/lib/chartSignalsApi";

export interface DeskCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

interface LevelMark {
  price: number;
  name: string;
  color?: string;
  dashed?: boolean;
}

interface Props {
  historical: DeskCandle[];
  forecast?: DeskCandle[];
  target?: number | null;
  stopLoss?: number | null;
  levels?: LevelMark[];
  height?: number;
  /** Draw the 20 and 50-day average lines. */
  averages?: boolean;
  /** Draw volume bars under the candles (needs volume on the candles). */
  showVolume?: boolean;
  /** Small spaces: hide level labels (the panel beside the chart lists them). */
  quiet?: boolean;
  /** Label for a pattern on the latest candle, e.g. "Hammer". */
  patternMark?: { label: string; direction: "bull" | "bear" | "neutral" } | null;
  /** Candles shown at first (the rest is a pinch / scroll away). Default: about 3 months. */
  initialBars?: number;
  /** The stock on the chart. Turns on the strategy picker (dotted lines of what each strategy predicted). */
  symbol?: string;
}

/** Quick ranges in trading days. */
const RANGES: Array<[string, number]> = [["1M", 22], ["3M", 66], ["6M", 130], ["1Y", 250]];
/** Past signals drawn per strategy (the newest), so long histories stay quick on a phone. */
const MAX_DRAWN = 60;
const UP = "#26a69a";
const DOWN = "#ef5350";

const fmt = (n: number | null | undefined) =>
  n == null || !Number.isFinite(n) ? "—" : n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n: number | null | undefined) => (n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(2)}%`);

/** Daily bars come as YYYY-MM-DD; anything longer is a timestamp. */
function toTime(s: string): Time {
  return (s.length <= 10 ? s : Math.floor(Date.parse(s) / 1000)) as Time;
}
const timeKey = (t: Time | undefined): string =>
  t == null ? "" : typeof t === "string" ? t : typeof t === "number" ? new Date(t * 1000).toISOString().slice(0, 10) : `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;

function emaSeries(values: number[], period: number): Array<number | null> {
  const k = 2 / (period + 1);
  const out: Array<number | null> = [];
  let prev: number | null = null;
  values.forEach((v, i) => {
    if (i < period - 1) return void out.push(null);
    prev = prev == null ? values.slice(0, period).reduce((a, b) => a + b, 0) / period : v * k + prev * (1 - k);
    out.push(Math.round(prev * 100) / 100);
  });
  return out;
}

const OUTCOME_TEXT: Record<string, string> = { target: "✓", stop: "✗", time: "⏱", "hand-closed": "✋" };
const OUTCOME_WORD: Record<string, string> = {
  target: "hit target",
  stop: "hit stop",
  time: "ran out of days",
  "hand-closed": "closed by hand",
  open: "still open",
};

type Picked = { strategy: StrategySignals; item: SignalTrade | SignalForecast };

export function DeskChart({
  historical,
  forecast = [],
  target = null,
  stopLoss = null,
  levels = [],
  height = 420,
  averages = false,
  showVolume = false,
  quiet = false,
  patternMark = null,
  initialBars = 66,
  symbol,
}: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const keptRange = useRef<{ from: number; to: number } | null>(null);
  const { dark } = useDarkMode();
  const [range, setRange] = useState<string | null>(null);
  const [log, setLog] = useState(false);
  const [full, setFull] = useState(false);
  const [hover, setHover] = useState<DeskCandle | null>(null);
  const [failed, setFailed] = useState(false);

  // Strategy overlays
  const [options, setOptions] = useState<ChartStrategy[]>([]);
  const [picked, setPicked] = useState<string[]>(() => (symbol ? loadPicked() : []));
  const [menu, setMenu] = useState(false);
  const [signals, setSignals] = useState<StrategySignals[]>([]);
  const [loading, setLoading] = useState(false);
  const [sigError, setSigError] = useState("");
  const [detail, setDetail] = useState<Picked | null>(null);

  useEffect(() => {
    if (!symbol) return;
    chartSignalsApi
      .options()
      .then((r) => setOptions(r.data))
      .catch(() => setOptions([]));
  }, [symbol]);

  const active = useMemo(() => picked.filter((id) => options.some((o) => o.id === id)), [picked, options]);
  useEffect(() => {
    if (!symbol || active.length === 0) {
      setSignals([]);
      return;
    }
    let alive = true;
    setLoading(true);
    setSigError("");
    chartSignalsApi
      .signals(symbol, active)
      .then((r) => alive && setSignals(r.data.strategies))
      .catch(() => alive && setSigError("Couldn't load the strategy lines. Try again in a moment."))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [symbol, active]);

  const toggle = (id: string) => {
    const next = picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id];
    setPicked(next);
    savePicked(next);
    setDetail(null);
  };

  const last = historical[historical.length - 1];
  const shown = hover ?? last;
  const prevOf = (c: DeskCandle | undefined) => {
    if (!c) return undefined;
    const i = historical.findIndex((h) => h.time === c.time);
    return i > 0 ? historical[i - 1].close : undefined;
  };
  const prevClose = prevOf(shown);
  const chg = shown && prevClose ? ((shown.close - prevClose) / prevClose) * 100 : null;

  useEffect(() => {
    const el = box.current;
    if (!el || historical.length === 0) return;
    const text = dark ? "#a1a1aa" : "#52525b";
    const lines = dark ? "#1f1f23" : "#f1f1f3";
    let chart: IChartApi;
    try {
      chart = createChart(el, {
        autoSize: true,
        layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: text, fontSize: 11 },
        grid: { vertLines: { color: lines }, horzLines: { color: lines } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: dark ? "#27272a" : "#e4e4e7", mode: log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
        timeScale: { borderColor: dark ? "#27272a" : "#e4e4e7", rightOffset: 6, barSpacing: 8, minBarSpacing: 1 },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      });
    } catch {
      setFailed(true);
      return;
    }
    setFailed(false);
    chartRef.current = chart;

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    });
    candleRef.current = candles;
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: showVolume ? 0.22 : 0.06 } });
    candles.setData(historical.map((c) => ({ time: toTime(c.time), open: c.open, high: c.high, low: c.low, close: c.close })));

    if (showVolume && historical.some((c) => typeof c.volume === "number")) {
      const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
      vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(
        historical.map((c) => ({ time: toTime(c.time), value: c.volume ?? 0, color: c.close >= c.open ? "rgba(38,166,154,0.45)" : "rgba(239,83,80,0.45)" })),
      );
    }

    if (averages) {
      const closes = historical.map((c) => c.close);
      for (const [n, color] of [[20, "#f59e0b"], [50, "#a78bfa"]] as const) {
        const ema = emaSeries(closes, n);
        const s = chart.addSeries(LineSeries, { color, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData(historical.flatMap((c, i) => (ema[i] == null ? [] : [{ time: toTime(c.time), value: ema[i] as number }])));
      }
    }

    // Today's Kronos forecast: a dashed path from the last close.
    const ahead = forecast.filter((c) => c.time > last.time);
    if (ahead.length) {
      const k = chart.addSeries(LineSeries, { color: "#38bdf8", lineWidth: 2, lineStyle: LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false, title: quiet ? "" : "Kronos" });
      k.setData([{ time: toTime(last.time), value: last.close }, ...ahead.map((c) => ({ time: toTime(c.time), value: c.close }))]);
    }

    const priceLine = (price: number, title: string, color: string, style: LineStyle) =>
      candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title: quiet ? "" : title });
    if (target != null) priceLine(target, "Target", "#22c55e", LineStyle.Solid);
    if (stopLoss != null) priceLine(stopLoss, "Stop", "#ef4444", LineStyle.Solid);
    for (const lv of levels) priceLine(lv.price, lv.name, lv.color || "#38bdf8", lv.dashed ? LineStyle.Dashed : LineStyle.Solid);

    // Strategy overlays: entry → target dotted (what it predicted), entry → stop fainter, a mark where it ended.
    const markers: SeriesMarker<Time>[] = [];
    const firstDay = historical[0].time;
    for (const s of signals) {
      const items = s.items.filter((i) => (isForecast(i) ? i.as_of : i.entry_date) >= firstDay).slice(-MAX_DRAWN);
      for (const it of items) {
        if (isForecast(it)) {
          const pts = [{ time: toTime(it.as_of), value: it.start }, ...it.path.filter((p) => p.time > it.as_of).map((p) => ({ time: toTime(p.time), value: p.close }))];
          if (pts.length < 2) continue;
          const l = chart.addSeries(LineSeries, { color: s.color, lineWidth: 2, lineStyle: LineStyle.Dotted, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
          l.setData(pts);
          if (it.direction_right != null) {
            markers.push({ time: pts[pts.length - 1].time, position: "atPriceMiddle", price: pts[pts.length - 1].value, shape: "circle", color: it.direction_right ? "#22c55e" : "#ef4444", size: 0.6 });
          }
          continue;
        }
        if (it.entry == null) continue;
        const end = it.exit_date && it.exit_date > it.entry_date ? it.exit_date : last.time > it.entry_date ? last.time : null;
        if (!end) continue;
        const color = it.off ? "#a1a1aa" : s.color;
        const seg = (to: number, style: LineStyle, width: 1 | 2) => {
          const l = chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
          l.setData([{ time: toTime(it.entry_date), value: it.entry as number }, { time: toTime(end), value: to }]);
        };
        if (it.target != null) seg(it.target, LineStyle.Dotted, 2);
        if (it.stop != null) seg(it.stop, LineStyle.SparseDotted, 1);
        markers.push({ time: toTime(it.entry_date), position: it.side === "LONG" ? "belowBar" : "aboveBar", shape: it.side === "LONG" ? "arrowUp" : "arrowDown", color, size: 0.7 });
        if (it.exit_date && it.outcome !== "open") {
          markers.push({
            time: toTime(it.exit_date),
            position: "aboveBar",
            shape: "circle",
            color: it.outcome === "target" ? "#22c55e" : it.outcome === "stop" ? "#ef4444" : "#a1a1aa",
            text: OUTCOME_TEXT[it.outcome] ?? "",
            size: 0.6,
          });
        }
      }
    }
    if (patternMark) {
      markers.push({
        time: toTime(last.time),
        position: "aboveBar",
        shape: "arrowDown",
        color: patternMark.direction === "bull" ? "#22c55e" : patternMark.direction === "bear" ? "#ef4444" : "#a1a1aa",
        text: patternMark.label,
      });
    }
    markers.sort((a, b) => timeKey(a.time).localeCompare(timeKey(b.time)));
    if (markers.length) createSeriesMarkers(candles, markers);

    // Zoom: keep yours across live updates; first time, show the last few months.
    const n = historical.length;
    chart.timeScale().setVisibleLogicalRange(keptRange.current ?? { from: Math.max(0, n - initialBars), to: n + 6 });
    chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
      if (r) keptRange.current = { from: r.from, to: r.to };
    });

    const byTime = new Map(historical.map((c) => [timeKey(toTime(c.time)), c]));
    const onMove = (p: MouseEventParams<Time>) => setHover(p.time ? byTime.get(timeKey(p.time)) ?? null : null);
    chart.subscribeCrosshairMove(onMove);

    const onClick = (p: MouseEventParams<Time>) => {
      const t = timeKey(p.time);
      if (!t) return setDetail(null);
      for (const s of signals) {
        for (const it of [...s.items].reverse()) {
          if (isForecast(it)) {
            const endT = it.path[it.path.length - 1]?.time ?? it.as_of;
            if (it.as_of <= t && t <= endT) return setDetail({ strategy: s, item: it });
          } else if (it.entry_date <= t && t <= (it.exit_date ?? last.time)) {
            return setDetail({ strategy: s, item: it });
          }
        }
      }
      setDetail(null);
    };
    chart.subscribeClick(onClick);

    return () => {
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, [historical, forecast, target, stopLoss, levels, dark, averages, showVolume, quiet, patternMark, initialBars, log, signals, last]);

  const zoomTo = (label: string, bars: number | null) => {
    const chart = chartRef.current;
    if (!chart) return;
    const n = historical.length;
    const r = { from: bars == null ? 0 : Math.max(0, n - bars), to: n + 6 };
    chart.timeScale().setVisibleLogicalRange(r);
    keptRange.current = r;
    setRange(label);
  };

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setFull(false);
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [full]);

  const btn = (on: boolean) =>
    `min-h-8 px-2.5 rounded-md text-xs font-medium ${on ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`;
  const optionById = new Map(options.map((o) => [o.id, o]));

  return (
    <div className={full ? "fixed inset-0 z-50 bg-background flex flex-col p-2 gap-1.5" : "space-y-1.5"} role={full ? "dialog" : undefined} aria-label={full ? "Full-screen chart" : undefined}>
      <div className="flex items-center gap-1 flex-wrap" role="group" aria-label="Chart range">
        {RANGES.filter(([, n]) => n < historical.length + 10).map(([label, n]) => (
          <button key={label} type="button" className={btn(range === label)} aria-pressed={range === label} onClick={() => zoomTo(label, n)}>
            {label}
          </button>
        ))}
        <button type="button" className={btn(range === "All")} aria-pressed={range === "All"} onClick={() => zoomTo("All", null)}>
          All
        </button>
        <button type="button" className={btn(log)} aria-pressed={log} onClick={() => setLog((v) => !v)} title="Log scale">
          log
        </button>
        {symbol && (
          <div className="relative">
            <button type="button" className={`${btn(active.length > 0)} inline-flex items-center gap-1 border border-border`} aria-expanded={menu} aria-haspopup="listbox" onClick={() => setMenu((v) => !v)}>
              Strategies{active.length ? ` (${active.length})` : ""} <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            </button>
            {menu && (
              <div role="listbox" aria-multiselectable="true" aria-label="Strategies to show" className="absolute z-20 mt-1 w-64 max-h-72 overflow-auto rounded-md border border-border bg-popover shadow-lg p-1">
                {options.length === 0 && <p className="px-2 py-1.5 text-xs text-muted-foreground">No strategies yet.</p>}
                {options.map((o) => {
                  const on = picked.includes(o.id);
                  return (
                    <button key={o.id} type="button" role="option" aria-selected={on} onClick={() => toggle(o.id)} className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-sm text-start hover:bg-muted">
                      <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ background: o.color }} aria-hidden />
                      <span className="flex-1 truncate">{o.label}</span>
                      {on && <Check className="h-4 w-4 text-primary" aria-hidden />}
                    </button>
                  );
                })}
                <button type="button" onClick={() => setMenu(false)} className="w-full mt-1 px-2 py-1.5 rounded text-xs text-muted-foreground hover:bg-muted">
                  Done
                </button>
              </div>
            )}
          </div>
        )}
        <button type="button" className={`${btn(false)} ms-auto`} onClick={() => setFull((v) => !v)} aria-label={full ? "Exit full screen" : "Full screen"}>
          {full ? <Minimize2 className="h-4 w-4" aria-hidden /> : <Maximize2 className="h-4 w-4" aria-hidden />}
        </button>
      </div>

      {active.length > 0 && (
        <div className="flex flex-wrap gap-1" aria-label="Strategies on the chart">
          {active.map((id) => {
            const o = optionById.get(id);
            return (
              <span key={id} className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px]">
                <span className="h-2 w-2 rounded-full" style={{ background: o?.color }} aria-hidden />
                {o?.label}
                <button type="button" onClick={() => toggle(id)} aria-label={`Hide ${o?.label}`} className="text-muted-foreground hover:text-foreground">
                  <X className="h-3 w-3" aria-hidden />
                </button>
              </span>
            );
          })}
          {loading && <span className="text-[11px] text-muted-foreground px-1 py-0.5">Replaying…</span>}
        </div>
      )}

      <div className={`relative ${full ? "flex-1 min-h-0" : ""}`}>
        {shown && (
          <div className="pointer-events-none absolute left-2 top-1 z-10 text-[11px] font-mono tabular-nums leading-4">
            {symbol && <span className="font-sans font-semibold me-2">{symbol} · 1D · NSE</span>}
            <span className="text-muted-foreground">O</span> {fmt(shown.open)} <span className="text-muted-foreground">H</span> {fmt(shown.high)}{" "}
            <span className="text-muted-foreground">L</span> {fmt(shown.low)} <span className="text-muted-foreground">C</span> {fmt(shown.close)}{" "}
            {chg != null && <span style={{ color: chg >= 0 ? UP : DOWN }}>{pct(chg)}</span>}
          </div>
        )}
        <div
          ref={box}
          data-testid="desk-chart"
          style={{ width: "100%", height: full ? "100%" : height, touchAction: "none" }}
          className="rounded-lg border border-border"
        />
        {failed && <p className="absolute inset-0 grid place-items-center text-sm text-muted-foreground">The chart couldn't load in this browser.</p>}
      </div>
      {!full && <p className="text-[11px] text-muted-foreground">Pinch or scroll to zoom · drag to move{symbol ? " · tap a dotted line for details" : ""}</p>}

      {sigError && <p className="text-xs text-red-700 dark:text-red-400">{sigError}</p>}
      {detail && <SignalDetail picked={detail} onClose={() => setDetail(null)} />}
      {signals.length > 0 && <Scorecard rows={signals} />}
    </div>
  );
}

function SignalDetail({ picked, onClose }: { picked: Picked; onClose: () => void }) {
  const { strategy: s, item } = picked;
  return (
    <div className="rounded-md border border-border p-2 text-xs space-y-0.5" role="status">
      <div className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: s.color }} aria-hidden />
        <strong className="flex-1">{s.label}</strong>
        <button type="button" onClick={onClose} aria-label="Close details" className="text-muted-foreground hover:text-foreground">
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
      {isForecast(item) ? (
        <p>
          Forecast made on {item.as_of} ({item.source === "live" ? "live" : "worked out later from data up to that day"}): {pct(item.predicted_pct)} over {item.path.length}{" "}
          days.{" "}
          {item.actual_pct == null ? "Not over yet." : `Actually ${pct(item.actual_pct)}: direction ${item.direction_right ? "right" : "wrong"}.`}
        </p>
      ) : (
        <p>
          {item.source === "live" ? "Live paper trade" : "Replay"} · {item.side === "LONG" ? "Buy" : "Short"} on {item.entry_date} at {fmt(item.entry)} · target {fmt(item.target)} · stop{" "}
          {fmt(item.stop)}. {item.outcome === "open" ? "Still open." : `Ended ${item.exit_date ?? ""} at ${fmt(item.exit)}: ${OUTCOME_WORD[item.outcome] ?? item.outcome}`}
          {item.pct != null ? `, ${pct(item.pct)}` : ""}
          {item.r != null ? ` (${item.r > 0 ? "+" : ""}${item.r.toFixed(2)}R)` : ""}.{item.off ? " The desk no longer takes this kind of trade." : ""}
        </p>
      )}
    </div>
  );
}

function Scorecard({ rows }: { rows: StrategySignals[] }) {
  return (
    <table className="w-full text-xs">
      <caption className="text-start text-[11px] text-muted-foreground pb-1">On this stock (replays + live, after costs)</caption>
      <thead className="text-muted-foreground">
        <tr>
          <th className="text-start font-medium px-1 py-1">Strategy</th>
          <th className="text-end font-medium px-1 py-1">Signals</th>
          <th className="text-end font-medium px-1 py-1">Right</th>
          <th className="text-end font-medium px-1 py-1">A trade</th>
          <th className="text-end font-medium px-1 py-1">Net</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s) => (
          <tr key={s.id} className="border-t border-border">
            <td className="px-1 py-1">
              <span className="inline-block h-2 w-2 rounded-full me-1.5" style={{ background: s.color }} aria-hidden />
              {s.label}
              {s.error && <span className="block text-red-700 dark:text-red-400">{s.error}</span>}
            </td>
            {s.kind === "forecast" ? (
              <>
                <td className="px-1 py-1 text-end font-mono tabular-nums">{s.summary.forecasts ?? 0}</td>
                <td className="px-1 py-1 text-end font-mono tabular-nums" title="Direction right, of the forecasts that are over">
                  {s.summary.direction_right_pct == null ? "—" : `${s.summary.direction_right_pct}%`}
                </td>
                <td className="px-1 py-1 text-end text-muted-foreground" colSpan={2}>
                  {s.summary.forecasts ? `${s.summary.judged ?? 0} judged` : "Building up (saved from now on)"}
                </td>
              </>
            ) : (
              <>
                <td className="px-1 py-1 text-end font-mono tabular-nums">{s.summary.signals ?? 0}</td>
                <td className="px-1 py-1 text-end font-mono tabular-nums" title="Hit target, of the closed signals">
                  {s.summary.target_hit_pct == null ? "—" : `${s.summary.target_hit_pct}%`}
                </td>
                <td className="px-1 py-1 text-end font-mono tabular-nums" style={{ color: (s.summary.avg_pct ?? 0) >= 0 ? UP : DOWN }}>
                  {pct(s.summary.avg_pct)}
                </td>
                <td className="px-1 py-1 text-end font-mono tabular-nums" style={{ color: (s.summary.net_pct ?? 0) >= 0 ? UP : DOWN }}>
                  {pct(s.summary.net_pct)}
                </td>
              </>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
