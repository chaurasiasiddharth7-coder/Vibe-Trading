import { useEffect, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";

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
  /** Draw a volume strip under the candles (needs volume on the candles). */
  showVolume?: boolean;
  /** Small spaces: hide the legend and level labels (the panel beside the chart lists them). */
  quiet?: boolean;
  /** Label for a pattern on the latest candle, e.g. "Hammer". */
  patternMark?: { label: string; direction: "bull" | "bear" | "neutral" } | null;
  /** Candles shown at first (the rest is a pinch / scroll away). Default: about 3 months. */
  initialBars?: number;
}

/** Quick ranges in trading days. */
const RANGES: Array<[string, number]> = [["1M", 22], ["3M", 66], ["6M", 130], ["1Y", 250]];

const fmt = (n: number | null | undefined) =>
  n == null || !Number.isFinite(n) ? "—" : n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

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
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  // The zoom you set survives live price updates and theme changes (kept as start/end percent).
  const zoomRef = useRef<{ start: number; end: number } | null>(null);
  const [range, setRange] = useState<string | null>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current || historical.length === 0) return;
    const chart = echarts.init(ref.current);
    chartRef.current = chart;

    const histDates = historical.map((c) => c.time);
    const candle = historical.map((c) => [c.open, c.close, c.low, c.high]);
    const lastClose = historical[historical.length - 1].close;
    const lastTime = historical[historical.length - 1].time;

    const forecastDates = forecast.map((c) => c.time);
    const allDates = [...histDates];
    for (const d of forecastDates) {
      if (!allDates.includes(d)) allDates.push(d);
    }

    const forecastLine = allDates.map((d) => {
      if (d === lastTime) return lastClose;
      const f = forecast.find((c) => c.time === d);
      return f ? f.close : null;
    });

    const vol = showVolume && historical.every((c) => typeof c.volume === "number");
    const markLineData: Array<{ yAxis: number; name: string; lineStyle: { color: string; type?: string }; label?: { position: string; fontSize?: number; show?: boolean } }> = [];
    if (target != null) {
      markLineData.push({ yAxis: target, name: "AI TARGET", lineStyle: { color: "#22c55e" } });
    }
    if (stopLoss != null) {
      markLineData.push({ yAxis: stopLoss, name: "STOP", lineStyle: { color: "#ef4444" } });
    }
    for (const lv of levels) {
      markLineData.push({
        yAxis: lv.price,
        name: lv.name,
        lineStyle: { color: lv.color || "#38bdf8", type: lv.dashed ? "dashed" : "solid" },
        // Support and resistance labels sit on the left so they don't pile up with stop and target on the right.
        ...(lv.dashed ? { label: { position: "insideStartTop", show: !quiet } } : {}),
      });
    }
    const total = forecast.length ? historical.length + forecast.filter((c) => !histDates.includes(c.time)).length : historical.length;
    const startPct = zoomRef.current?.start ?? Math.max(0, 100 - (Math.min(initialBars, total) / total) * 100);
    const endPct = zoomRef.current?.end ?? 100;
    const labelBox = {
      fontSize: 10,
      color: dark ? "#e4e4e7" : "#27272a",
      backgroundColor: dark ? "rgba(24,24,27,0.85)" : "rgba(255,255,255,0.9)",
      padding: [1, 4],
      borderRadius: 2,
      formatter: (p: { name?: string; value?: number }) => (p.name && !/^(Support|Resistance)/.test(p.name) ? `${p.name} ${fmt(p.value)}` : fmt(p.value)),
    };

    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      legend: {
        show: !quiet,
        data: averages ? ["OHLC", "Kronos", "EMA 20", "EMA 50"] : ["OHLC", "Kronos"],
        top: 0,
        textStyle: { color: dark ? "#a1a1aa" : "#52525b" },
      },
      grid: vol
        ? [
            { left: 8, right: 60, top: quiet ? 12 : 36, bottom: 96 },
            { left: 8, right: 60, height: 48, bottom: 30 },
          ]
        : { left: 8, right: 60, top: quiet ? 12 : 36, bottom: 32 },
      xAxis: [
        {
          type: "category",
          data: allDates,
          axisLabel: { color: dark ? "#71717a" : "#a1a1aa", hideOverlap: true, show: !vol },
          axisLine: { lineStyle: { color: dark ? "#27272a" : "#e4e4e7" } },
        },
        ...(vol
          ? [
              {
                type: "category" as const,
                gridIndex: 1,
                data: allDates,
                axisLabel: { color: dark ? "#71717a" : "#a1a1aa", hideOverlap: true },
                axisLine: { lineStyle: { color: dark ? "#27272a" : "#e4e4e7" } },
              },
            ]
          : []),
      ],
      yAxis: [
        {
          scale: true,
          position: "right",
          axisLabel: { color: dark ? "#71717a" : "#a1a1aa" },
          splitLine: { lineStyle: { color: dark ? "#18181b" : "#f4f4f5" } },
        },
        ...(vol
          ? [
              {
                gridIndex: 1,
                splitNumber: 2,
                axisLabel: { show: false },
                axisLine: { show: false },
                axisTick: { show: false },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: vol ? [0, 1] : [0],
          start: startPct,
          end: endPct,
          minValueSpan: 8,
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          preventDefaultMouseMove: true,
        },
      ],
      tooltip: {
        trigger: "axis",
        confine: true,
        axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }], label: { backgroundColor: dark ? "#3f3f46" : "#52525b" } },
        backgroundColor: dark ? "rgba(24,24,27,0.95)" : "rgba(255,255,255,0.97)",
        borderColor: dark ? "#3f3f46" : "#e4e4e7",
        textStyle: { color: dark ? "#e4e4e7" : "#27272a", fontSize: 12 },
        formatter: (raw: unknown) => {
          const items = (Array.isArray(raw) ? raw : [raw]) as Array<{ seriesName: string; dataIndex: number; name: string; value: unknown }>;
          const i = items[0]?.dataIndex ?? -1;
          const c = historical[i];
          const date = items[0]?.name ?? "";
          if (!c) {
            const f = items.find((x) => x.seriesName === "Kronos");
            return `${date}<br/>AI forecast: <b>${fmt(f?.value as number)}</b>`;
          }
          const prev = historical[i - 1]?.close;
          const chg = prev ? ((c.close - prev) / prev) * 100 : null;
          const col = chg == null ? "inherit" : chg >= 0 ? "#22c55e" : "#ef4444";
          const rows = [
            `<b>${date}</b>`,
            `O ${fmt(c.open)} &nbsp;H ${fmt(c.high)}`,
            `L ${fmt(c.low)} &nbsp;C <b>${fmt(c.close)}</b>`,
            chg == null ? "" : `<span style="color:${col}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span>`,
            c.volume ? `Vol ${Math.round(c.volume).toLocaleString("en-IN")}` : "",
          ];
          for (const it of items) {
            if (it.seriesName.startsWith("EMA") && typeof it.value === "number") rows.push(`${it.seriesName} ${fmt(it.value)}`);
          }
          return rows.filter(Boolean).join("<br/>");
        },
      },
      series: [
        {
          name: "OHLC",
          type: "candlestick",
          data: [
            ...candle,
            ...forecastDates.map(() => [null, null, null, null]),
          ].slice(0, allDates.length),
          itemStyle: {
            color: "#22c55e",
            color0: "#ef4444",
            borderColor: "#22c55e",
            borderColor0: "#ef4444",
          },
          markPoint: patternMark
            ? {
                symbol: "pin",
                symbolSize: 42,
                itemStyle: {
                  color: patternMark.direction === "bull" ? "#22c55e" : patternMark.direction === "bear" ? "#ef4444" : "#a1a1aa",
                },
                label: { show: true, formatter: patternMark.label, fontSize: 9, color: "#fff" },
                data: [{ coord: [lastTime, historical[historical.length - 1].high] }],
              }
            : undefined,
          markLine:
            markLineData.length > 0
              ? {
                  symbol: "none",
                  silent: true,
                  label: { position: "insideEndTop", ...labelBox },
                  data: markLineData,
                }
              : undefined,
        },
        ...(vol
          ? [
              {
                name: "Volume",
                type: "bar" as const,
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: historical.map((c) => ({
                  value: c.volume ?? 0,
                  itemStyle: { color: c.close >= c.open ? "rgba(34,197,94,0.55)" : "rgba(239,68,68,0.55)" },
                })),
              },
            ]
          : []),
        ...(averages
          ? [
              { name: "EMA 20", values: emaSeries(historical.map((c) => c.close), 20), color: "#f59e0b" },
              { name: "EMA 50", values: emaSeries(historical.map((c) => c.close), 50), color: "#a78bfa" },
            ].map((e) => ({
              name: e.name,
              type: "line" as const,
              data: [...e.values, ...forecastDates.map(() => null)].slice(0, allDates.length),
              showSymbol: false,
              lineStyle: { width: 1.2, color: e.color },
              connectNulls: false,
            }))
          : []),
        {
          name: "Kronos",
          type: "line",
          data: forecastLine,
          showSymbol: false,
          lineStyle: { type: "dashed", width: 2, color: "#38bdf8" },
          connectNulls: false,
        },
      ],
    });

    chart.on("datazoom", () => {
      const dz = (chart.getOption() as { dataZoom?: Array<{ start: number; end: number }> }).dataZoom?.[0];
      if (dz) zoomRef.current = { start: dz.start, end: dz.end };
      setRange(null);
    });

    const onResize = () => chart.resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(onResize) : null;
    if (ro) ro.observe(ref.current);
    else window.addEventListener("resize", onResize);
    return () => {
      ro?.disconnect();
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [historical, forecast, target, stopLoss, levels, dark, averages, showVolume, quiet, patternMark, initialBars]);

  const total = historical.length + forecast.filter((c) => !historical.some((h) => h.time === c.time)).length;
  const zoomTo = (label: string, bars: number | null) => {
    const chart = chartRef.current;
    if (!chart || total === 0) return;
    const start = bars == null ? 0 : Math.max(0, 100 - (Math.min(bars, total) / total) * 100);
    chart.dispatchAction({ type: "dataZoom", start, end: 100 });
    zoomRef.current = { start, end: 100 };
    setRange(label);
  };
  const btn = (active: boolean) =>
    `min-h-8 px-2.5 rounded-md text-xs font-medium ${active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1 flex-wrap" role="group" aria-label="Chart range">
        {RANGES.filter(([, n]) => n < historical.length + 10).map(([label, n]) => (
          <button key={label} type="button" className={btn(range === label)} aria-pressed={range === label} onClick={() => zoomTo(label, n)}>
            {label}
          </button>
        ))}
        <button type="button" className={btn(range === "All")} aria-pressed={range === "All"} onClick={() => zoomTo("All", null)}>
          All
        </button>
        <span className="ms-auto text-[11px] text-muted-foreground hidden sm:inline">Scroll or pinch to zoom · drag to move</span>
        <span className="ms-auto text-[11px] text-muted-foreground sm:hidden">Pinch to zoom · drag to move</span>
      </div>
      <div ref={ref} style={{ width: "100%", height, touchAction: "pan-y" }} className="rounded-lg border border-border" />
    </div>
  );
}
