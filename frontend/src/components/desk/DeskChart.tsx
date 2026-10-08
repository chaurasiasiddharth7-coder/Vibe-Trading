import { useEffect, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";

export interface DeskCandle {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface LevelMark {
  price: number;
  name: string;
  color?: string;
}

interface Props {
  historical: DeskCandle[];
  forecast?: DeskCandle[];
  target?: number | null;
  stopLoss?: number | null;
  levels?: LevelMark[];
  height?: number;
}

export function DeskChart({
  historical,
  forecast = [],
  target = null,
  stopLoss = null,
  levels = [],
  height = 420,
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

    const markLineData: Array<{ yAxis: number; name: string; lineStyle: { color: string } }> = [];
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
        lineStyle: { color: lv.color || "#38bdf8" },
      });
    }

    chart.setOption({
      backgroundColor: "transparent",
      animation: false,
      legend: {
        data: ["OHLC", "Kronos"],
        textStyle: { color: dark ? "#a1a1aa" : "#52525b" },
      },
      grid: { left: 56, right: 24, top: 36, bottom: 32 },
      xAxis: {
        type: "category",
        data: allDates,
        axisLabel: { color: dark ? "#71717a" : "#a1a1aa", hideOverlap: true },
        axisLine: { lineStyle: { color: dark ? "#27272a" : "#e4e4e7" } },
      },
      yAxis: {
        scale: true,
        axisLabel: { color: dark ? "#71717a" : "#a1a1aa" },
        splitLine: { lineStyle: { color: dark ? "#18181b" : "#f4f4f5" } },
      },
      tooltip: { trigger: "axis" },
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
          markLine:
            markLineData.length > 0
              ? {
                  symbol: "none",
                  label: { position: "insideEndTop" },
                  data: markLineData,
                }
              : undefined,
        },
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
  }, [historical, forecast, target, stopLoss, levels, dark]);

  return <div ref={ref} style={{ width: "100%", height }} className="rounded-lg border border-border" />;
}
