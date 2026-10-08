import { useCallback, useEffect, useState } from "react";
import { Activity, Loader2, Target, ShieldAlert } from "lucide-react";
import { DeskChart, type DeskCandle } from "@/components/desk/DeskChart";
import { api, type AnalysisPayload as ApiAnalysis, type KronosForecastPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

const WATCHLIST = ["RELIANCE", "TCS", "HDFCBANK", "TMPV", "INFY"];

type TradePlan = {
  action: string;
  confidence_score: number;
  entry_range: string;
  target_price: number;
  stop_loss: number;
  risk_reward_ratio: string;
  position_size_percent: number;
  holding_horizon: string;
  executive_summary: string;
};

type Metrics = {
  rsi_14: number | null;
  ema_20: number;
  ema_50: number;
  ema_200: number | null;
  macd_status: string;
  supertrend: string;
  atr: number | null;
  active_candle_patterns: string[];
};

type AnalysisView = {
  symbol: string;
  ticker: string;
  current_price: number;
  change_percent: number;
  metrics: Metrics;
  chart_data: DeskCandle[];
};

const asMetrics = (m: Record<string, unknown>): Metrics => ({
  rsi_14: (m.rsi_14 as number | null) ?? null,
  ema_20: Number(m.ema_20 ?? 0),
  ema_50: Number(m.ema_50 ?? 0),
  ema_200: (m.ema_200 as number | null) ?? null,
  macd_status: String(m.macd_status ?? ""),
  supertrend: String(m.supertrend ?? ""),
  atr: (m.atr as number | null) ?? null,
  active_candle_patterns: Array.isArray(m.active_candle_patterns)
    ? (m.active_candle_patterns as string[])
    : [],
});

const toView = (data: ApiAnalysis): AnalysisView => ({
  symbol: data.symbol,
  ticker: data.ticker,
  current_price: data.current_price,
  change_percent: data.change_percent,
  metrics: asMetrics(data.metrics),
  chart_data: data.chart_data,
});

export function Desk() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [custom, setCustom] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisView | null>(null);
  const [kronos, setKronos] = useState<KronosForecastPayload | null>(null);
  const [tradePlan, setTradePlan] = useState<TradePlan | null>(null);
  const [signalId, setSignalId] = useState<string | null>(null);
  const [committeeBusy, setCommitteeBusy] = useState(false);
  const [paperBusy, setPaperBusy] = useState(false);
  const [paperMsg, setPaperMsg] = useState<string | null>(null);
  const [arenaVotes, setArenaVotes] = useState<Array<Record<string, unknown>> | null>(null);
  const [pmPlan, setPmPlan] = useState<Record<string, unknown> | null>(null);
  const [scorecard, setScorecard] = useState<Array<Record<string, unknown>> | null>(null);
  const [targetNet, setTargetNet] = useState("1500000");
  const [brLevels, setBrLevels] = useState<Array<{ price: number; name: string; color?: string }>>([]);
  const [liveStatus, setLiveStatus] = useState<Record<string, unknown> | null>(null);
  const [liveTickets, setLiveTickets] = useState<Array<Record<string, unknown>>>([]);
  const [liveMsg, setLiveMsg] = useState<string | null>(null);
  const [liveBusy, setLiveBusy] = useState(false);
  const [manualQty, setManualQty] = useState("1");
  const [ticketCopy, setTicketCopy] = useState<string | null>(null);

  const refreshSurvival = useCallback(async () => {
    try {
      const sc = await api.getDeskScorecard();
      setScorecard(sc.engines);
    } catch {
      setScorecard(null);
    }
  }, []);

  const refreshLive = useCallback(async () => {
    try {
      const res = await api.getDeskLiveStatus("dhan");
      setLiveStatus(res.data);
      setLiveTickets(res.tickets ?? []);
    } catch {
      setLiveStatus(null);
    }
  }, []);

  const load = useCallback(async (ticker: string) => {
    setLoading(true);
    setError(null);
    setTradePlan(null);
    setSignalId(null);
    setPaperMsg(null);
    setArenaVotes(null);
    try {
      const [a, k] = await Promise.all([
        api.getAlphadeskAnalysis(ticker),
        api.getKronosForecast(ticker).catch(() => null),
      ]);
      const view = toView(a.data);
      setAnalysis(view);
      setKronos(k?.data ?? null);
      void refreshSurvival();
      // Auto mentor call: action + entry + exit without waiting for Arena click
      try {
        const qs = await api.postDeskQuickSignal({
          symbol: view.symbol,
          analysis: {
            current_price: view.current_price,
            metrics: view.metrics,
          },
          kronos: k?.data
            ? { kronos_projection: k.data.kronos_projection }
            : undefined,
        });
        setTradePlan(qs.plan as TradePlan);
        setSignalId(qs.signal_id ?? null);
      } catch (qe) {
        // Non-fatal — user can still Run Arena
        console.warn("quick-signal failed", qe);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setAnalysis(null);
      setKronos(null);
    } finally {
      setLoading(false);
    }
  }, [refreshSurvival]);

  useEffect(() => {
    void load(symbol);
  }, [symbol, load]);

  const handleSelect = (s: string) => {
    setSymbol(s);
  };

  const handleCustom = () => {
    const t = custom.trim().toUpperCase();
    if (!t) return;
    setSymbol(t);
  };

  const handleRunArena = async () => {
    if (!analysis) return;
    setCommitteeBusy(true);
    setError(null);
    try {
      await api.postDeskHeartbeat().catch(() => null);
      const res = await api.postDeskArena({
        symbol: analysis.symbol,
        target_net: Number(targetNet) || 0,
        analysis: {
          current_price: analysis.current_price,
          metrics: analysis.metrics,
        },
        kronos: kronos
          ? { kronos_projection: kronos.kronos_projection }
          : undefined,
      });
      setTradePlan(res.plan as TradePlan);
      setSignalId(res.signal_id);
      setArenaVotes(res.votes);
      const lv = Array.isArray(res.levels)
        ? (res.levels as Array<Record<string, unknown>>).map((l) => ({
            price: Number(l.level ?? l.price ?? 0),
            name: String(l.level_name ?? l.variant ?? "LVL"),
            color: l.side === "BUY" ? "#22c55e" : l.side === "SELL" ? "#ef4444" : "#38bdf8",
          }))
        : [];
      setBrLevels(lv.filter((x) => x.price > 0));
      void refreshSurvival();
      void refreshLive();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitteeBusy(false);
    }
  };

  const handlePmPlan = async () => {
    setError(null);
    setCommitteeBusy(true);
    try {
      // Full mentor universe (stocks + ETFs) — do not limit to 5 Desk chips
      const res = await api.postPmPlan({
        target_net: Number(targetNet) || 0,
        full_universe: true,
        include_etfs: true,
      });
      setPmPlan(res.plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitteeBusy(false);
    }
  };

  const handleRunCommittee = async () => {
    if (!analysis) return;
    setCommitteeBusy(true);
    setError(null);
    try {
      const userVars: Record<string, string> = {
        target: analysis.ticker || `${analysis.symbol}.NS`,
        market: "IN",
        price: String(analysis.current_price),
        metrics_json: JSON.stringify(analysis.metrics),
        kronos_json: JSON.stringify(kronos?.kronos_projection ?? {}),
        portfolio_json: "{}",
      };
      const started = await api.createSwarmRun("india_fund_committee", userVars);
      const runId = started.id;
      const deadline = Date.now() + 12 * 60 * 1000;
      let finalReport = "";
      let terminalStatus = "";
      while (Date.now() < deadline) {
        const run = await api.getSwarmRun(runId);
        const status = String(run.status ?? "");
        if (status === "completed" || status === "failed" || status === "cancelled") {
          terminalStatus = status;
          finalReport = String(run.final_report ?? "");
          if (!finalReport && Array.isArray(run.tasks)) {
            const cio = (run.tasks as Array<Record<string, unknown>>).find(
              (t) => t.id === "task-cio",
            );
            finalReport = String(cio?.summary ?? "");
          }
          break;
        }
        await new Promise((r) => setTimeout(r, 2500));
      }
      if (!terminalStatus) {
        throw new Error(`Committee still running (${runId}); check Swarm dashboard`);
      }
      if (terminalStatus !== "completed" || !finalReport.trim()) {
        throw new Error(
          `Committee ${terminalStatus || "timed out"} (${runId})${
            finalReport ? `: ${finalReport.slice(0, 240)}` : ""
          }`,
        );
      }
      const saved = await api.saveDeskSignal(
        analysis.symbol,
        finalReport,
        {
          metrics: analysis.metrics,
          kronos: kronos?.kronos_projection ?? null,
          swarm_run_id: runId,
        },
        {
          fallback_heuristic: true,
          analysis: {
            current_price: analysis.current_price,
            metrics: analysis.metrics,
          },
          kronos: kronos
            ? { kronos_projection: kronos.kronos_projection }
            : undefined,
        },
      );
      setTradePlan(saved.plan as TradePlan);
      setSignalId(saved.signal_id);
      const summary = String((saved.plan as Record<string, unknown>).executive_summary ?? "");
      if (summary.includes("Committee JSON missing")) {
        setError(
          "Committee finished but CIO returned no JSON (model tried tool calls). Showing heuristic plan — re-run Committee after API restart for the fix.",
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCommitteeBusy(false);
    }
  };

  const handlePaper = async (side: "buy" | "sell") => {
    if (!analysis || !tradePlan) return;
    setPaperBusy(true);
    setPaperMsg(null);
    try {
      const notional = 100000 * (tradePlan.position_size_percent / 100);
      const qty = Math.max(1, Math.floor(notional / analysis.current_price));
      const engineId = String((tradePlan as Record<string, unknown>).engine_id ?? "pm");
      const res = await api.placeDeskPaperOrder({
        symbol: analysis.ticker || analysis.symbol,
        side,
        quantity: qty,
        signal_id: signalId ?? undefined,
        profile_id: "dhan-paper-trade",
        engine_id: engineId,
      });
      const outcomeNote =
        res.outcome?.engine != null
          ? ` · outcome N=${String(res.outcome.engine.closed_trades)} E[R]=${String(res.outcome.engine.expectancy_r ?? "—")}`
          : "";
      setPaperMsg(`Paper ${side.toUpperCase()} qty=${qty} via ${res.profile_id}${outcomeNote}`);
      void refreshSurvival();
    } catch (e) {
      setPaperMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setPaperBusy(false);
    }
  };

  const handleApproveSignal = async () => {
    if (!signalId) return;
    setLiveBusy(true);
    setLiveMsg(null);
    try {
      await api.postDeskSignalApprove(signalId);
      setLiveMsg(`Signal ${signalId.slice(0, 8)}… approved`);
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  const handleGrantUnlock = async (engineId: string) => {
    setLiveBusy(true);
    setLiveMsg(null);
    try {
      await api.postDeskLiveUnlock({ engine_id: engineId, action: "grant" });
      setLiveMsg(`Unlocked ${engineId}`);
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  const handleLiveSync = async () => {
    setLiveBusy(true);
    setLiveMsg(null);
    try {
      await api.postDeskLiveSync();
      setLiveMsg("Live-readonly sync saved");
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  const handleReplayBr = async () => {
    setLiveBusy(true);
    setLiveMsg(null);
    try {
      const res = await api.postDeskReplayBrPdhPdl({
        symbol: analysis?.symbol || symbol,
        max_trades: 40,
      });
      const applied = Number(res.data.applied ?? 0);
      const eng = res.data.engine as Record<string, unknown> | undefined;
      setLiveMsg(
        res.data.skipped
          ? `Replay skipped: ${String(res.data.reason)}`
          : `Replay applied ${applied} · N=${String(eng?.closed_trades ?? "—")} E[R]=${String(eng?.expectancy_r ?? "—")}`,
      );
      void refreshSurvival();
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  const handleIssueTicket = async () => {
    if (!signalId || !analysis || !tradePlan) return;
    setLiveBusy(true);
    setLiveMsg(null);
    setTicketCopy(null);
    try {
      const qty = Math.max(1, Math.min(10, Number(manualQty) || 1));
      const engineId = String((tradePlan as Record<string, unknown>).engine_id ?? "br_pdh_pdl");
      const side = tradePlan.action === "SELL" || tradePlan.action === "TRIM" ? "sell" : "buy";
      const limit = Number(tradePlan.target_price) > 0
        ? analysis.current_price
        : analysis.current_price;
      const ticketRes = await api.postDeskLiveTicket({
        signal_id: signalId,
        engine_id: engineId,
        symbol: analysis.symbol,
        side,
        quantity: qty,
        limit_price: limit,
        notional_inr: limit * qty,
        order_type: "limit",
        product: "CNC",
        broker: "dhan",
      });
      const copy = String(ticketRes.ticket.copyable_order ?? "");
      setTicketCopy(copy);
      setLiveMsg(`Ticket ${String(ticketRes.ticket.id).slice(0, 8)}… PENDING_MANUAL — place in Dhan`);
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  const handleMarkFilled = async (ticketId: string) => {
    setLiveBusy(true);
    try {
      await api.postDeskLiveTicketFill(ticketId, {
        fill_price: analysis?.current_price,
      });
      setLiveMsg(`Marked filled ${ticketId.slice(0, 8)}…`);
      void refreshLive();
    } catch (e) {
      setLiveMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveBusy(false);
    }
  };

  useEffect(() => {
    void refreshLive();
  }, [refreshLive]);

  return (
    <main className="min-h-[calc(100vh-2rem)] p-6 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-border pb-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Activity className="h-6 w-6 text-emerald-500" />
            AlphaDesk India
            <span className="text-[10px] font-mono uppercase tracking-wider border border-emerald-700/40 text-emerald-400 px-2 py-0.5 rounded">
              NSE/BSE
            </span>
          </h1>
          <p className="text-sm text-muted-foreground">
            PM hygiene + Arena (PM vs Debate) · paper sleeves · survival scorecard
          </p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <input
            value={targetNet}
            onChange={(e) => setTargetNet(e.target.value)}
            className="h-8 w-28 rounded-md border border-border bg-background px-2 text-xs font-mono"
            aria-label="Liquidity target net INR"
            title="Liquidity target (net INR)"
          />
          <button
            type="button"
            onClick={() => void handlePmPlan()}
            disabled={committeeBusy}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
            title="Scan watchlist for horizon-bucketed ideas"
          >
            {committeeBusy ? "Scanning…" : "PM Plan"}
          </button>
          {WATCHLIST.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => handleSelect(s)}
              className={cn(
                "px-3 py-1.5 rounded-md text-xs font-semibold tracking-wide border transition",
                symbol === s
                  ? "bg-emerald-600 text-white border-emerald-600"
                  : "bg-background border-border hover:bg-muted",
              )}
            >
              {s}
            </button>
          ))}
          <input
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCustom()}
            placeholder="Symbol"
            className="h-8 w-28 rounded-md border border-border bg-background px-2 text-xs"
            aria-label="Custom symbol"
          />
          <button
            type="button"
            onClick={handleCustom}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted"
          >
            Go
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-rose-800/50 bg-rose-950/30 text-rose-200 text-sm px-3 py-2">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading analysis…
        </div>
      )}

      {analysis && !loading && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-4">
            <div className="flex justify-between items-end rounded-lg border border-border bg-card/40 p-4">
              <div>
                <h2 className="text-xl font-bold">{analysis.symbol}</h2>
                <p className="text-2xl font-mono font-semibold text-emerald-400">
                  ₹{analysis.current_price}
                  <span
                    className={cn(
                      "ml-2 text-sm",
                      analysis.change_percent >= 0 ? "text-emerald-400" : "text-rose-400",
                    )}
                  >
                    {analysis.change_percent >= 0 ? "+" : ""}
                    {analysis.change_percent}%
                  </span>
                </p>
              </div>
              <div className="flex flex-wrap gap-4 text-xs font-mono text-muted-foreground">
                <div>
                  RSI: <span className="text-foreground">{analysis.metrics.rsi_14 ?? "—"}</span>
                </div>
                <div>
                  EMA50: <span className="text-foreground">₹{analysis.metrics.ema_50}</span>
                </div>
                <div>
                  SuperTrend:{" "}
                  <span
                    className={
                      analysis.metrics.supertrend === "BULLISH"
                        ? "text-emerald-400"
                        : "text-rose-400"
                    }
                  >
                    {analysis.metrics.supertrend}
                  </span>
                </div>
                {kronos && (
                  <div>
                    Kronos:{" "}
                    <span className="text-sky-400">
                      {kronos.kronos_projection.trajectory_bias}{" "}
                      {kronos.kronos_projection.expected_change_percent}%
                    </span>
                  </div>
                )}
              </div>
            </div>

            <DeskChart
              historical={analysis.chart_data}
              forecast={kronos?.forecast_data}
              target={tradePlan?.target_price}
              stopLoss={tradePlan?.stop_loss}
              levels={brLevels}
            />
          </div>

          <div className="space-y-4">
            <div className="rounded-lg border border-border bg-card/40 p-5 space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-xs font-bold tracking-widest text-muted-foreground uppercase">
                  AI Signal Call
                </span>
                {tradePlan ? (
                  <span className="px-3 py-1 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs font-bold rounded-full">
                    {tradePlan.action} · {tradePlan.confidence_score}%
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">No plan yet</span>
                )}
              </div>

              {tradePlan ? (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-background p-3 rounded border border-border col-span-2">
                      <span className="text-muted-foreground text-xs block">Entry</span>
                      <span className="font-mono font-bold text-sm">
                        {tradePlan.entry_range || "—"}
                      </span>
                    </div>
                    <div className="bg-background p-3 rounded border border-border">
                      <span className="text-muted-foreground text-xs flex items-center gap-1">
                        <Target className="h-3 w-3" /> Target (exit)
                      </span>
                      <span className="text-emerald-400 font-mono font-bold text-lg">
                        ₹{tradePlan.target_price}
                      </span>
                    </div>
                    <div className="bg-background p-3 rounded border border-border">
                      <span className="text-muted-foreground text-xs flex items-center gap-1">
                        <ShieldAlert className="h-3 w-3" /> Stop
                      </span>
                      <span className="text-rose-400 font-mono font-bold text-lg">
                        ₹{tradePlan.stop_loss}
                      </span>
                    </div>
                    <div className="bg-background p-3 rounded border border-border">
                      <span className="text-muted-foreground text-xs block">R / R</span>
                      <span className="font-mono font-bold">{tradePlan.risk_reward_ratio}</span>
                    </div>
                    <div className="bg-background p-3 rounded border border-border">
                      <span className="text-muted-foreground text-xs block">Horizon</span>
                      <span className="font-mono font-bold text-sm">
                        {tradePlan.holding_horizon || "—"}
                      </span>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground leading-relaxed bg-background p-3 rounded border border-border">
                    {tradePlan.executive_summary}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Loading auto signal… Arena / Committee refine when you want a deeper call.
                </p>
              )}

              {arenaVotes && (
                <div className="text-[11px] font-mono space-y-1 border border-border rounded p-2">
                  {arenaVotes.map((v) => (
                    <div key={String(v.engine_id)} className="flex justify-between gap-2">
                      <span>
                        {String(v.engine_id)} · {String(v.action)}
                        {v.excluded ? " (DEAD)" : ""}
                      </span>
                      <span>{String(v.arena_score)}</span>
                    </div>
                  ))}
                </div>
              )}

              {analysis.metrics.active_candle_patterns.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {analysis.metrics.active_candle_patterns.map((p) => (
                    <span key={p} className="text-[10px] bg-muted text-muted-foreground px-2 py-0.5 rounded">
                      {p}
                    </span>
                  ))}
                </div>
              )}

              <div className="flex flex-col gap-2 pt-2">
                <button
                  type="button"
                  disabled={committeeBusy}
                  onClick={() => void handleRunArena()}
                  className="h-9 rounded-md bg-sky-600 text-white text-sm font-semibold hover:bg-sky-500 disabled:opacity-50"
                >
                  {committeeBusy ? "Arena running…" : "Run Arena (PM vs Debate)"}
                </button>
                <button
                  type="button"
                  disabled={committeeBusy}
                  onClick={() => void handleRunCommittee()}
                  className="h-9 rounded-md bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
                >
                  {committeeBusy ? "Committee running…" : "Run Committee"}
                </button>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    disabled={!tradePlan || paperBusy}
                    onClick={() => void handlePaper("buy")}
                    className="h-9 rounded-md border border-border text-sm disabled:opacity-50 hover:bg-muted"
                  >
                    Paper Buy
                  </button>
                  <button
                    type="button"
                    disabled={!tradePlan || paperBusy}
                    onClick={() => void handlePaper("sell")}
                    className="h-9 rounded-md border border-border text-sm disabled:opacity-50 hover:bg-muted"
                  >
                    Paper Sell
                  </button>
                </div>
                {paperMsg && <p className="text-[11px] text-muted-foreground font-mono">{paperMsg}</p>}
              </div>
            </div>
          </div>
        </div>
      )}

      {(scorecard || pmPlan) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {scorecard && (
            <div className="rounded-lg border border-border p-4 space-y-2">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <ShieldAlert className="h-4 w-4" /> Survival scorecard
              </h3>
              <div className="text-[11px] font-mono space-y-1">
                {scorecard.map((e) => (
                  <div key={String(e.engine_id)} className="flex justify-between gap-2">
                    <span>
                      {String(e.engine_id)} · {String(e.status)} · sleeve ₹
                      {Number(e.sleeve_inr ?? 0).toLocaleString("en-IN")}
                    </span>
                    <span>
                      N={String(e.closed_trades)} E[R]=
                      {e.expectancy_r == null ? "—" : String(e.expectancy_r)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {pmPlan && (
            <div className="rounded-lg border border-border p-4 space-y-2">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <Target className="h-4 w-4" /> PM hygiene
              </h3>
              <p className="text-xs text-muted-foreground">
                Names {String(pmPlan.name_count)} · exits {String(pmPlan.hygiene_exits)} · raised net ₹
                {Number(pmPlan.raised_net ?? 0).toLocaleString("en-IN")}
              </p>
              <ul className="text-[11px] font-mono max-h-32 overflow-auto space-y-0.5">
                {Array.isArray(pmPlan.proposals) &&
                  (pmPlan.proposals as Array<Record<string, unknown>>).slice(0, 8).map((p, i) => (
                    <li key={`${String(p.symbol)}-${i}`}>
                      {String(p.action)} {String(p.symbol)} · ₹{Number(p.net ?? 0).toLocaleString("en-IN")}
                    </li>
                  ))}
                {(!Array.isArray(pmPlan.proposals) || pmPlan.proposals.length === 0) && (
                  <li className="text-muted-foreground">No hygiene exits — mentor scan below.</li>
                )}
              </ul>
            </div>
          )}
        </div>
      )}

      {pmPlan && typeof pmPlan.opportunities === "object" && pmPlan.opportunities !== null && (
        <div className="rounded-lg border border-emerald-800/40 bg-emerald-950/10 p-4 space-y-4">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <Target className="h-4 w-4 text-emerald-500" />
            Mentor plan — where to put money
          </h3>
          {(() => {
            const opp = pmPlan.opportunities as Record<string, unknown>;
            const alloc = opp.allocation as Record<string, unknown> | undefined;
            const sleeves = Array.isArray(alloc?.sleeves)
              ? (alloc.sleeves as Array<Record<string, unknown>>)
              : [];
            const mfPlay = Array.isArray(alloc?.mf_playbook)
              ? (alloc.mf_playbook as Array<Record<string, unknown>>)
              : [];
            const stockBuys = Array.isArray(opp.top_stock_buys)
              ? (opp.top_stock_buys as Array<Record<string, unknown>>)
              : [];
            const fundBuys = Array.isArray(opp.top_fund_etf_buys)
              ? (opp.top_fund_etf_buys as Array<Record<string, unknown>>)
              : [];
            const buckets = opp.buckets as
              | Record<string, Array<Record<string, unknown>>>
              | undefined;
            return (
              <>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {String(opp.mentor_brief ?? "")}
                  {opp.universe_n != null ? ` · scanned ${String(opp.universe_n)} names` : ""}
                </p>
                {sleeves.length > 0 && (
                  <div className="space-y-2">
                    <div className="text-[11px] font-bold uppercase tracking-wider text-emerald-400">
                      Suggested allocation
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2">
                      {sleeves.map((s) => (
                        <div
                          key={String(s.sleeve)}
                          className="rounded border border-border bg-background/60 p-2 text-[11px]"
                        >
                          <div className="font-semibold">{String(s.pct)}% · {String(s.sleeve)}</div>
                          <div className="text-muted-foreground">
                            {s.inr != null
                              ? `₹${Number(s.inr).toLocaleString("en-IN")}`
                              : "—"}
                          </div>
                          <div className="text-muted-foreground mt-1">{String(s.note)}</div>
                        </div>
                      ))}
                    </div>
                    {alloc?.disclaimer != null && (
                      <p className="text-[10px] text-muted-foreground">{String(alloc.disclaimer)}</p>
                    )}
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="rounded border border-border bg-background/60 p-3 space-y-2">
                    <div className="text-[11px] font-bold uppercase tracking-wider">Best stocks (BUY)</div>
                    {stockBuys.length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">No aligned stock BUYs — wait or use ETFs.</p>
                    ) : (
                      <ul className="space-y-2">
                        {stockBuys.slice(0, 6).map((row) => (
                          <li key={String(row.symbol)} className="text-[11px] font-mono space-y-0.5">
                            <button
                              type="button"
                              className="text-left font-semibold hover:underline"
                              onClick={() => handleSelect(String(row.symbol))}
                            >
                              {String(row.symbol)} · {String(row.horizon)} · {String(row.confidence)}%
                            </button>
                            <div className="text-muted-foreground">
                              entry {String(row.entry_range ?? "—")} · tgt ₹
                              {Number(row.target_price ?? 0).toFixed(0)} · stop ₹
                              {Number(row.stop_loss ?? 0).toFixed(0)}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div className="rounded border border-border bg-background/60 p-3 space-y-2">
                    <div className="text-[11px] font-bold uppercase tracking-wider">
                      Blue-chip / index ETFs &amp; funds
                    </div>
                    {fundBuys.length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        No tactical ETF BUY — still SIP NIFTYBEES / Sensex as core.
                      </p>
                    ) : (
                      <ul className="space-y-2">
                        {fundBuys.slice(0, 6).map((row) => (
                          <li key={String(row.symbol)} className="text-[11px] font-mono space-y-0.5">
                            <button
                              type="button"
                              className="text-left font-semibold hover:underline"
                              onClick={() => handleSelect(String(row.symbol))}
                            >
                              {String(row.action)} {String(row.symbol)} · {String(row.asset_class)}
                            </button>
                            <div className="text-muted-foreground">{String(row.mentor_note ?? "")}</div>
                          </li>
                        ))}
                      </ul>
                    )}
                    {mfPlay.length > 0 && (
                      <div className="pt-2 border-t border-border space-y-1">
                        <div className="text-[10px] font-bold uppercase text-muted-foreground">
                          Open-ended MF SIP playbook
                        </div>
                        {mfPlay.map((m) => (
                          <div key={String(m.category)} className="text-[10px] text-muted-foreground">
                            <span className="text-foreground font-medium">{String(m.category)}</span>
                            {" — "}
                            {String(m.why)} ({String(m.horizon)})
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                  {(["1 week", "1 month", "3 months", "6 months+"] as const).map((bucket) => {
                    const list = buckets?.[bucket] ?? [];
                    return (
                      <div key={bucket} className="rounded border border-border bg-background/60 p-3 space-y-2">
                        <div className="text-[11px] font-bold uppercase tracking-wider text-emerald-400">
                          {bucket}
                        </div>
                        {list.length === 0 ? (
                          <p className="text-[11px] text-muted-foreground">No BUY setups</p>
                        ) : (
                          <ul className="space-y-2">
                            {list.map((row) => (
                              <li key={String(row.symbol)} className="text-[11px] font-mono space-y-0.5">
                                <button
                                  type="button"
                                  className="text-left font-semibold text-foreground hover:underline"
                                  onClick={() => handleSelect(String(row.symbol))}
                                >
                                  {String(row.action)} {String(row.symbol)} · {String(row.confidence)}%
                                </button>
                                <div className="text-muted-foreground">
                                  entry {String(row.entry_range ?? "—")}
                                </div>
                                <div>
                                  tgt ₹{Number(row.target_price ?? 0).toFixed(0)} · stop ₹
                                  {Number(row.stop_loss ?? 0).toFixed(0)}
                                  {row.suggested_inr != null
                                    ? ` · ~₹${Number(row.suggested_inr).toLocaleString("en-IN")}`
                                    : ""}
                                </div>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            );
          })()}
        </div>
      )}

      <div className="rounded-lg border border-amber-700/40 bg-amber-950/10 p-4 space-y-3">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-amber-500" />
          Manual tiny live
          <span className="text-[10px] font-mono uppercase tracking-wider border border-amber-700/40 text-amber-400 px-2 py-0.5 rounded">
            no API place
          </span>
        </h3>
        <p className="text-xs text-muted-foreground">
          Unlock after N≥30 E[R]&gt;0 · Approve signal · mandate via chat Consent · ticket → place in Dhan
          yourself · sync readonly for P&L.
        </p>
        <div className="flex flex-wrap gap-2 items-center">
          <button
            type="button"
            disabled={liveBusy || !signalId}
            onClick={() => void handleApproveSignal()}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Approve signal
          </button>
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => void handleGrantUnlock("br_pdh_pdl")}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Unlock br_pdh_pdl
          </button>
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => void handleReplayBr()}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Replay br_pdh_pdl paper
          </button>
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => void handleLiveSync()}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Sync live-readonly
          </button>
          <input
            value={manualQty}
            onChange={(e) => setManualQty(e.target.value)}
            className="h-8 w-16 rounded-md border border-border bg-background px-2 text-xs font-mono"
            aria-label="Manual ticket qty"
            title="Qty (max 10)"
          />
          <button
            type="button"
            disabled={liveBusy || !signalId || !tradePlan}
            onClick={() => void handleIssueTicket()}
            className="h-8 px-3 rounded-md text-xs bg-amber-700 text-white hover:bg-amber-600 disabled:opacity-50"
          >
            Issue manual ticket
          </button>
        </div>
        {liveMsg && <p className="text-[11px] font-mono text-muted-foreground">{liveMsg}</p>}
        {ticketCopy && (
          <pre className="text-[10px] font-mono whitespace-pre-wrap rounded border border-border bg-background p-3 max-h-40 overflow-auto">
            {ticketCopy}
          </pre>
        )}
        {liveStatus && Array.isArray(liveStatus.engines) && (
          <div className="text-[11px] font-mono space-y-1">
            <p>
              mandate={String(liveStatus.mandate_ok)} · halted={String(liveStatus.halted)} · mode=
              {String(liveStatus.mode)}
            </p>
            {(liveStatus.engines as Array<Record<string, unknown>>)
              .filter((e) => String(e.engine_id).startsWith("br_") || e.live_ready || e.expectancy_eligible)
              .slice(0, 8)
              .map((e) => (
                <div key={String(e.engine_id)} className="flex justify-between gap-2">
                  <span>
                    {String(e.engine_id)} · eligible={String(e.expectancy_eligible)} · unlocked=
                    {String(e.human_unlocked)} · ready={String(e.live_ready)}
                  </span>
                  <span>
                    N={String((e.scorecard as Record<string, unknown> | undefined)?.closed_trades ?? "—")}
                  </span>
                </div>
              ))}
          </div>
        )}
        {liveTickets.length > 0 && (
          <ul className="text-[11px] font-mono space-y-1 max-h-32 overflow-auto">
            {liveTickets.slice(0, 5).map((t) => (
              <li key={String(t.id)} className="flex flex-wrap items-center gap-2 justify-between">
                <span>
                  {String(t.status)} · {String(t.side)} {String(t.symbol)} ×{String(t.quantity)}
                </span>
                {t.status === "PENDING_MANUAL" && (
                  <button
                    type="button"
                    disabled={liveBusy}
                    onClick={() => void handleMarkFilled(String(t.id))}
                    className="h-6 px-2 rounded border border-border text-[10px] hover:bg-muted"
                  >
                    Mark filled
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-lg border border-border p-4 space-y-3">
        <h3 className="text-sm font-semibold">Hardening</h3>
        <p className="text-xs text-muted-foreground">
          Morning cron report · tax YTD · token budget · audit export
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => {
              setLiveBusy(true);
              void api
                .postDeskMorning({ target_net: Number(targetNet) || 0, force: true })
                .then((r) => {
                  setLiveMsg(
                    r.data.skipped
                      ? `Morning skipped (${String(r.data.reason)})`
                      : `Morning ok · holdings=${String(r.data.holding_count)} · ${String(r.data.path ?? "")}`,
                  );
                  void refreshSurvival();
                })
                .catch((e: unknown) => setLiveMsg(e instanceof Error ? e.message : String(e)))
                .finally(() => setLiveBusy(false));
            }}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Run morning desk
          </button>
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => {
              setLiveBusy(true);
              void Promise.all([api.getDeskTaxYtd(), api.getDeskTokenBudget()])
                .then(([tax, bud]) => {
                  setLiveMsg(
                    `Tax ${String(tax.data.fy)} est ₹${Number(tax.data.est_tax_inr ?? 0).toLocaleString("en-IN")} · tokens left ${String((bud.data.remaining as Record<string, unknown> | undefined)?.total ?? "—")}`,
                  );
                })
                .catch((e: unknown) => setLiveMsg(e instanceof Error ? e.message : String(e)))
                .finally(() => setLiveBusy(false));
            }}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Tax / budget
          </button>
          <button
            type="button"
            disabled={liveBusy}
            onClick={() => {
              setLiveBusy(true);
              void api
                .postDeskAuditExport(true)
                .then((r) => {
                  setLiveMsg(`Audit → ${String(r.data.zip_path || r.data.json_path)}`);
                })
                .catch((e: unknown) => setLiveMsg(e instanceof Error ? e.message : String(e)))
                .finally(() => setLiveBusy(false));
            }}
            className="h-8 px-3 rounded-md text-xs border border-border hover:bg-muted disabled:opacity-50"
          >
            Export audit
          </button>
        </div>
      </div>
    </main>
  );
}
