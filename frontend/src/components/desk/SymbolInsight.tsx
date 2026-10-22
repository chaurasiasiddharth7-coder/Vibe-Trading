import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type AnalysisPayload, type KronosForecastPayload } from "@/lib/api";
import { DeskChart } from "@/components/desk/DeskChart";
import { AnalysisPanel, type ForecastControl } from "@/components/desk/AnalysisPanel";
import { levelWarnings, parsePatterns, supportResistance } from "@/components/desk/analysisView";
import { inr } from "@/components/desk/plain";

const FORECAST_FALLBACK = "The forecast couldn't run right now. Try again in a minute.";

/** Keep short, readable server messages; hide technical ones. */
function friendlyError(e: unknown): string {
  const msg = e instanceof Error ? e.message : "";
  return msg && msg.length <= 100 && !/tensor|dimension|traceback|internal error/i.test(msg) ? msg : FORECAST_FALLBACK;
}

export interface InsightProps {
  symbol: string;
  name?: string | null;
  industry?: string | null;
  /** LONG / SHORT when a trade or idea has a direction. */
  side?: "LONG" | "SHORT";
  entry?: number | null;
  stop?: number | null;
  target?: number | null;
  votes?: { trend: number; structure: number; momentum: number; forecast: number } | null;
  reasons?: string[];
  /** Forecast from an earlier check, in percent. */
  savedForecastPct?: number | null;
  /** True when the position is already open (the warning note about leaning against it applies). */
  isOpenTrade?: boolean;
  compact?: boolean;
  chartHeight?: number;
  children?: ReactNode;
}

/** Key numbers, chart with levels and volume, full analysis, and an on-demand AI forecast for one stock. */
export function SymbolInsight({
  symbol,
  name,
  industry,
  side,
  entry,
  stop,
  target,
  votes,
  reasons,
  savedForecastPct = null,
  isOpenTrade = false,
  compact = false,
  chartHeight = 320,
  children,
}: InsightProps) {
  const [data, setData] = useState<AnalysisPayload | null>(null);
  const [failed, setFailed] = useState(false);
  const [fc, setFc] = useState<{ state: ForecastControl["state"]; error: string | null; data: KronosForecastPayload | null }>({
    state: "idle",
    error: null,
    data: null,
  });

  useEffect(() => {
    let alive = true;
    setData(null);
    setFailed(false);
    api
      .getAlphadeskAnalysis(symbol)
      .then((r) => alive && setData(r.data))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [symbol]);

  const runForecast = useCallback(() => {
    setFc((p) => ({ ...p, state: "loading", error: null }));
    api
      .getKronosForecast(symbol, 400, 15, true)
      .then((r) => setFc({ state: "done", error: null, data: r.data }))
      .catch((e) =>
        setFc((p) => ({ ...p, state: "error", error: friendlyError(e) })),
      );
  }, [symbol]);

  const m = (data?.metrics ?? {}) as Record<string, unknown>;
  const price = data?.current_price ?? null;
  const candles = useMemo(() => data?.chart_data ?? [], [data]);
  const sr = useMemo(() => (price != null && candles.length ? supportResistance(candles, price) : undefined), [price, candles]);
  const freshPct = fc.data?.kronos_projection.expected_change_percent ?? null;
  const forecastPct = freshPct ?? savedForecastPct;

  const levels = useMemo(() => {
    const out: Array<{ price: number; name: string; color: string; dashed?: boolean }> = [];
    if (entry != null) out.push({ price: entry, name: "Entry", color: "#a1a1aa" });
    for (const l of sr?.resistance ?? []) out.push({ price: l.price, name: `Resistance ${l.price.toLocaleString("en-IN")}`, color: "#fb7185", dashed: true });
    for (const l of sr?.support ?? []) out.push({ price: l.price, name: `Support ${l.price.toLocaleString("en-IN")}`, color: "#34d399", dashed: true });
    return out;
  }, [entry, sr]);

  const firstPattern = parsePatterns(m.active_candle_patterns)[0];
  const mark = useMemo(
    () => (firstPattern ? { label: firstPattern.name, direction: firstPattern.direction } : null),
    [firstPattern?.name, firstPattern?.direction], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const num = (k: string) => (typeof m[k] === "number" ? (m[k] as number) : null);
  const atr = num("atr");
  const rsi = num("rsi_14");
  const hi = candles.length ? Math.max(...candles.map((c) => c.high)) : null;
  const lo = candles.length ? Math.min(...candles.map((c) => c.low)) : null;
  const stats: Array<[string, string]> = [
    ["Price", inr(price)],
    ["Today", data ? `${data.change_percent >= 0 ? "+" : "−"}${Math.abs(data.change_percent).toFixed(2)}%` : "—"],
    ["6-month range", hi != null && lo != null ? `${inr(lo, 0)} – ${inr(hi, 0)}` : "—"],
    ["Usual daily move", atr != null && price ? `${((atr / price) * 100).toFixed(1)}%` : "—"],
    ["Momentum (RSI)", rsi != null ? rsi.toFixed(0) : "—"],
  ];

  const control: ForecastControl = {
    state: fc.state,
    error: fc.error,
    fresh: fc.state === "done",
    onRun: runForecast,
    summary: fc.data
      ? `Expected move over the next 15 trading days: ${freshPct != null && freshPct >= 0 ? "+" : "−"}${Math.abs(freshPct ?? 0).toFixed(1)}% (range ${inr(fc.data.kronos_projection.projected_low, 0)} to ${inr(fc.data.kronos_projection.projected_high, 0)}), bias ${fc.data.kronos_projection.trajectory_bias.toLowerCase()}. Drawn as the dashed blue line.`
      : null,
  };

  return (
    <div className="space-y-3 text-sm">
      {(name || industry) && (
        <div className="flex flex-wrap items-baseline gap-2">
          {name && <h3 className="font-semibold">{name}</h3>}
          {industry && <span className="text-xs text-muted-foreground">{industry}</span>}
        </div>
      )}
      <dl className={compact ? "grid grid-cols-2 gap-x-4 gap-y-2" : "grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-4 gap-y-2"}>
        {stats.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-muted-foreground">{k}</dt>
            <dd className="font-mono text-sm">{v}</dd>
          </div>
        ))}
      </dl>
      {data ? (
        <>
          <DeskChart
            historical={data.chart_data}
            forecast={fc.data?.forecast_data}
            target={target ?? null}
            stopLoss={stop ?? null}
            levels={levels}
            averages
            showVolume
            quiet={compact}
            patternMark={mark}
            height={chartHeight}
          />
          <AnalysisPanel
            price={data.current_price}
            metrics={data.metrics}
            candles={data.chart_data}
            forecastPct={forecastPct}
            votes={votes}
            reasons={reasons}
            side={isOpenTrade ? side : undefined}
            sr={sr}
            srWarnings={
              sr && side && price != null ? levelWarnings(sr, { side, price, target, stop }) : []
            }
            forecast={control}
            compact={compact}
          />
        </>
      ) : (
        <p className="text-xs text-muted-foreground">{failed ? "Couldn't load the analysis right now." : "Loading analysis…"}</p>
      )}
      {children}
    </div>
  );
}
