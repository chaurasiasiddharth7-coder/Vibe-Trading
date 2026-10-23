import { cn } from "@/lib/utils";
import { ScoreBadge } from "@/components/desk/ScoreBadge";
import { nowScore, type TradeScore } from "@/components/desk/score";
import {
  buildSignals,
  describeCandles,
  entryVsNow,
  parsePatterns,
  tally,
  type Candle,
  type Direction,
  type Level,
  type SupportResistance,
} from "@/components/desk/analysisView";

const BADGE: Record<Direction, { text: string; cls: string }> = {
  bull: { text: "▲ Bullish", cls: "text-emerald-400 border-emerald-700/60" },
  bear: { text: "▼ Bearish", cls: "text-rose-400 border-rose-700/60" },
  neutral: { text: "● Neutral", cls: "text-muted-foreground border-border" },
};

function Badge({ d, caution }: { d: Direction; caution?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span className={cn("rounded border px-1.5 py-0.5 text-[11px]", BADGE[d].cls)}>{BADGE[d].text}</span>
      {caution && <span className="rounded border border-amber-700/60 px-1.5 py-0.5 text-[11px] text-amber-300">⚠ Stretched</span>}
    </span>
  );
}

const voteDir = (v: number): Direction => (v > 0 ? "bull" : v < 0 ? "bear" : "neutral");
const glyph = (d: Direction | null) => (d === "bull" ? "▲" : d === "bear" ? "▼" : d === "neutral" ? "●" : "–");

export interface ForecastControl {
  state: "idle" | "loading" | "error" | "done";
  error?: string | null;
  summary?: string | null;
  fresh: boolean;
  onRun: () => void;
}

function LevelLine({ kind, l }: { kind: "Resistance" | "Support"; l: Level }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 text-sm">
      <span className={cn("text-xs w-20", kind === "Resistance" ? "text-rose-300" : "text-emerald-300")}>{kind}</span>
      <span className="font-mono">₹{l.price.toLocaleString("en-IN")}</span>
      <span className="font-mono text-xs text-muted-foreground">
        {l.distPct >= 0 ? "+" : "−"}
        {Math.abs(l.distPct).toFixed(1)}% away
      </span>
      <span className="text-xs text-muted-foreground">
        {l.touches > 1 ? `tested ${l.touches} times` : "tested once"}
      </span>
    </li>
  );
}

export function AnalysisPanel({
  price,
  metrics,
  candles,
  forecastPct,
  votes,
  reasons,
  side,
  sr,
  srWarnings = [],
  forecast,
  compact = false,
  scoreSide,
  entryScore,
}: {
  scoreSide?: "LONG" | "SHORT";
  entryScore?: TradeScore | null;
  sr?: SupportResistance;
  srWarnings?: string[];
  forecast?: ForecastControl;
  compact?: boolean;
  price: number;
  metrics: Record<string, unknown>;
  candles: Candle[];
  forecastPct?: number | null;
  votes?: { trend: number; structure: number; momentum: number; forecast: number } | null;
  reasons?: string[];
  side?: "LONG" | "SHORT";
}) {
  const rows = buildSignals({ price, metrics, forecastPct, forecastFresh: forecast?.fresh, candles });
  const t = tally(rows);
  const total = t.bull + t.bear + t.neutral;
  const patterns = parsePatterns(metrics.active_candle_patterns);
  const recent = describeCandles(candles, 3);
  const entry = entryVsNow(votes, rows, forecast?.fresh);
  const asOf = candles.length ? candles[candles.length - 1].time : null;
  const now = scoreSide ? nowScore(rows, scoreSide) : null;
  const againstTrade =
    side && ((side === "LONG" && t.lean === "bear") || (side === "SHORT" && t.lean === "bull"));

  return (
    <section aria-label="Analysis" className="space-y-3 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold">What the analysis says</h4>
        {asOf && <span className="text-[11px] text-muted-foreground">Daily candles as of {asOf}</span>}
      </div>

      {now && (
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2" data-testid="now-score">
          <ScoreBadge score={now.score} tone={now.tone} verdict={now.verdict} label="Chart score today" />
          {entryScore && (
            <div className="text-xs text-muted-foreground">
              At entry: <span className="font-mono text-foreground">{entryScore.score.toFixed(1)}/10</span> {entryScore.verdict}
              <span className="block">
                {now.score > entryScore.score + 0.5 ? "Looking better than when you opened it." : now.score < entryScore.score - 0.5 ? "Looking weaker than when you opened it." : "About the same as when you opened it."}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="space-y-1">
        <div
          className="flex h-2 overflow-hidden rounded bg-muted"
          role="img"
          aria-label={`${t.bull} bullish, ${t.neutral} neutral, ${t.bear} bearish signals`}
        >
          {total > 0 && (
            <>
              <span className="bg-emerald-500" style={{ width: `${(t.bull / total) * 100}%` }} />
              <span className="bg-muted-foreground/40" style={{ width: `${(t.neutral / total) * 100}%` }} />
              <span className="bg-rose-500" style={{ width: `${(t.bear / total) * 100}%` }} />
            </>
          )}
        </div>
        <p className="text-sm">
          <span className="font-semibold">{t.verdict}</span>{" "}
          <span className="text-xs text-muted-foreground font-mono">
            ▲ {t.bull} · ● {t.neutral} · ▼ {t.bear}
          </span>
        </p>
        {againstTrade && (
          <p role="note" className="text-xs text-amber-300">
            The signals now lean against this {side === "LONG" ? "buy" : "short"}. Check the suggested exit below.
          </p>
        )}
      </div>

      <ul className={cn("grid grid-cols-1 gap-x-6 gap-y-2", !compact && "md:grid-cols-2")}>
        {rows.map((r) => (
          <li key={r.key} className="space-y-0.5">
            <div className="flex flex-wrap items-center justify-between gap-x-2">
              <span className="text-xs text-muted-foreground">{r.label}</span>
              {r.counts || r.caution ? <Badge d={r.direction} caution={r.caution} /> : null}
            </div>
            <p className="text-sm font-mono">{r.reading}</p>
            <p className="text-xs text-muted-foreground">{r.meaning}</p>
          </li>
        ))}
      </ul>

      {sr && (sr.support.length > 0 || sr.resistance.length > 0) && (
        <div className="space-y-2 border-t border-border pt-3">
          <h4 className="text-sm font-semibold">Support and resistance</h4>
          <ul className="space-y-1">
            {[...sr.resistance].reverse().map((l) => (
              <LevelLine key={`r${l.price}`} kind="Resistance" l={l} />
            ))}
            <li className="text-xs font-mono text-muted-foreground pl-20">now ₹{price.toLocaleString("en-IN")}</li>
            {sr.support.map((l) => (
              <LevelLine key={`s${l.price}`} kind="Support" l={l} />
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Resistance is a price where rises have stalled before. Support is where falls have stopped. Dashed lines on the chart.
          </p>
          {srWarnings.map((w) => (
            <p key={w} role="note" className="text-xs text-amber-300">
              {w}
            </p>
          ))}
        </div>
      )}

      {forecast && (
        <div className="space-y-2 border-t border-border pt-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold">AI forecast</h4>
            <button
              type="button"
              onClick={forecast.onRun}
              disabled={forecast.state === "loading"}
              className="min-h-9 px-3 rounded-md border border-border text-xs hover:bg-muted disabled:opacity-50"
            >
              {forecast.state === "loading" ? "Running forecast…" : forecast.fresh ? "Run again" : "Run fresh forecast"}
            </button>
          </div>
          {forecast.state === "loading" && (
            <p className="text-xs text-muted-foreground" aria-live="polite">
              This can take a minute the first time. The rest of the page keeps working.
            </p>
          )}
          {forecast.state === "error" && (
            <p role="alert" className="text-xs text-rose-300">
              {forecast.error ?? "The forecast isn't available right now."}
            </p>
          )}
          {forecast.state === "done" && forecast.summary && <p className="text-sm">{forecast.summary}</p>}
          {forecast.state === "idle" && (
            <p className="text-xs text-muted-foreground">
              {forecastPct != null
                ? "Showing the forecast from an earlier check. Run a fresh one to update the reading and draw it on the chart."
                : "No forecast yet. Run one to see the expected move and draw it on the chart (dashed blue line)."}
            </p>
          )}
        </div>
      )}

      <div className="space-y-2 border-t border-border pt-3">
        <h4 className="text-sm font-semibold">Candle analysis</h4>
        {patterns.length > 0 ? (
          <ul className="space-y-1">
            {patterns.map((p) => (
              <li key={p.name} className="text-sm">
                <span className="mr-2 font-semibold">{p.name}</span>
                <Badge d={p.direction} />
                <span className="block text-xs text-muted-foreground">{p.meaning}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No named pattern (hammer, engulfing, doji and so on) on the latest candles.</p>
        )}
        {recent.length > 0 && (
          <ul className="space-y-1">
            {recent.map((c) => (
              <li key={c.time} className="flex gap-2 text-xs">
                <span className="font-mono text-muted-foreground shrink-0">{c.time.slice(5)}</span>
                <span aria-hidden>{glyph(c.direction)}</span>
                <span>{c.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {entry.length > 0 && (
        <div className="space-y-2 border-t border-border pt-3">
          <h4 className="text-sm font-semibold">Why it was picked, and does it still hold?</h4>
          <ul className={cn("grid grid-cols-2 gap-2", !compact && "md:grid-cols-4")}>
            {entry.map((e) => {
              const then = voteDir(e.atEntry);
              const agrees = e.now == null ? null : e.now === then;
              return (
                <li key={e.label} className="rounded-md bg-muted/50 p-2 text-xs">
                  <span className="block text-muted-foreground">{e.label}</span>
                  <span className="font-mono text-sm">
                    {glyph(then)} → {glyph(e.now)}
                  </span>
                  <span className="block">
                    {agrees == null ? "Not rechecked" : agrees ? "Still agrees" : "Has changed"}
                  </span>
                </li>
              );
            })}
          </ul>
          {reasons && reasons.length > 0 && (
            <ul className="list-disc pl-4 text-xs text-muted-foreground">
              {reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        These are rule-based readings of past prices. They describe what happened, they don't predict what will.
      </p>
    </section>
  );
}
