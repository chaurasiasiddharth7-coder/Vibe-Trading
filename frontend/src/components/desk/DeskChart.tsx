import { useEffect, useRef } from "react";
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
}

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
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);
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
    const markLineData: Array<{ yAxis: number; name: string; lineStyle: { color: string; type?: string }; label?: { position: string; fontSize: number; show?: boolean } }> = [];
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
        ...(lv.dashed ? { label: { position: "insideStartTop", fontSize: 10, show: !quiet } } : {}),
      });
    }

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
            { left: 56, right: 24, top: quiet ? 12 : 36, bottom: 96 },
            { left: 56, right: 24, height: 48, bottom: 30 },
          ]
        : { left: 56, right: 24, top: 36, bottom: 32 },
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
      tooltip: { trigger: "axis", axisPointer: { link: [{ xAxisIndex: "all" }] } },
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
                  label: { position: "insideEndTop" },
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

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [historical, forecast, target, stopLoss, levels, dark, averages, showVolume, quiet, patternMark]);

  return <div ref={ref} style={{ width: "100%", height }} className="rounded-lg border border-border" />;
}
