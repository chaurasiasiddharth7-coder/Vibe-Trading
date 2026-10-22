import { useCallback, useEffect, useState } from "react";
import { Activity, Loader2, ShieldAlert } from "lucide-react";
import { DeskChart, type DeskCandle } from "@/components/desk/DeskChart";
import { BoardColumns, EvidencePanel, TrustStrip } from "@/components/desk/SignalBoard";
import {
  api,
  type AnalysisPayload as ApiAnalysis,
  type BoardRow,
  type KronosForecastPayload,
  type SignalBoard,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const boardRows = (b: SignalBoard | null): BoardRow[] => b?.rows ?? [];

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

export function Desk({ embedded = false }: { embedded?: boolean } = {}) {
  const Shell = embedded ? "div" : "main";
  const Heading = embedded ? "h2" : "h1";
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
  const [board, setBoard] = useState<SignalBoard | null>(null);
  const [boardBusy, setBoardBusy] = useState(false);
  const [row, setRow] = useState<BoardRow | null>(null);
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

  const scanBoard = useCallback(async () => {
    setBoardBusy(true);
    setError(null);
    try {
      const res = await api.postDeskBoard();
      setBoard(res.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBoardBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!embedded) {
      void scanBoard();
      return;
    }
    // Inside the dashboard: show the last saved check instead of starting a
    // slow scan on every visit. The user presses "Check again" for a fresh one.
    let alive = true;
    api
      .getBoardLast("core")
      .then((r) => alive && r?.data && setBoard(r.data))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [scanBoard, embedded]);

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
      setAnalysis(toView(a.data));
      setKronos(k?.data ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setAnalysis(null);
      setKronos(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(symbol);
  }, [symbol, load]);

  // Evidence for the selected symbol: from the board, or a one-name scan.
  useEffect(() => {
    const hit = boardRows(board).find((r) => r.symbol === symbol);
    if (hit) {
      setRow(hit);
      return;
    }
    setRow(null);
    if (!board) return;
    let live = true;
    void api
      .postDeskBoard({ symbols: [symbol] })
      .then((res) => {
        if (live) setRow(boardRows(res.data)[0] ?? null);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [board, symbol]);

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
    <Shell className={embedded ? "space-y-5" : "min-h-[calc(100vh-2rem)] p-6 space-y-5"}>
      <header className={cn("flex flex-wrap items-end justify-between gap-4 pb-4", !embedded && "border-b border-border")}>
        <div className={embedded ? "sr-only" : undefined}>
          <Heading className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Activity className="h-6 w-6 text-emerald-500" />
            AlphaDesk India
          </Heading>
          <p className="text-sm text-muted-foreground">
            What looks worth buying, what might fall, and what to stay away from today. Practice with fake money before using real money.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <input
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCustom()}
            placeholder="Any stock, e.g. TCS"
            className="h-9 w-36 rounded-md border border-border bg-background px-2 text-xs"
            aria-label="Look up symbol"
          />
          <button
            type="button"
            onClick={handleCustom}
            className="h-9 px-3 rounded-md text-xs border border-border hover:bg-muted"
          >
            Look up
          </button>
          <button
            type="button"
            onClick={() => void scanBoard()}
            disabled={boardBusy}
            className="h-9 px-3 rounded-md text-xs font-semibold bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {boardBusy && <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />}
            {boardBusy ? "Checking…" : "Check again"}
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-md border border-rose-800/50 bg-rose-950/30 text-rose-200 text-sm px-3 py-2">
          {error}
        </div>
      )}

      {board ? (
        <>
          <TrustStrip board={board} />
          <BoardColumns board={board} selected={symbol} onSelect={handleSelect} />
        </>
      ) : (
        (boardBusy || embedded) && (
          <p className="text-sm text-muted-foreground">
            {boardBusy
              ? "Checking about 37 stocks and funds. The first check of the day runs the AI forecast too and can take a few minutes."
              : "No saved check yet. Press Check again to scan about 37 stocks and funds (the first check of the day can take a few minutes)."}
          </p>
        )
      )}

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6" aria-label="Selected symbol">
        <div className="lg:col-span-2 space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-muted-foreground text-sm">
              <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" /> Loading {symbol}…
            </div>
          )}
          {analysis && !loading && (
            <>
              <div className="flex items-baseline gap-3">
                <h2 className="text-xl font-bold">{analysis.symbol}</h2>
                <span className="text-xl font-mono">₹{analysis.current_price}</span>
                <span
                  className={cn(
                    "text-sm font-mono",
                    analysis.change_percent >= 0 ? "text-emerald-400" : "text-rose-400",
                  )}
                >
                  {analysis.change_percent >= 0 ? "+" : ""}
                  {analysis.change_percent}%
                </span>
              </div>
              <DeskChart
                historical={analysis.chart_data}
                forecast={kronos?.forecast_data}
                target={row?.target ?? tradePlan?.target_price}
                stopLoss={row?.stop ?? tradePlan?.stop_loss}
                levels={brLevels}
              />
            </>
          )}
        </div>
        <div>
          {row ? (
            <EvidencePanel row={row} />
          ) : (
            <p className="text-xs text-muted-foreground">
              {board ? "Loading the details…" : "Details appear after the check finishes."}
            </p>
          )}
        </div>
      </section>

      <details className="rounded-lg border border-border">
        <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold">
          Advanced tools (for experienced users)
        </summary>
        <div className="p-4 pt-0 space-y-4">
          <div className="rounded-lg border border-border p-4 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-semibold">Deeper call on {symbol}</span>
              <input
                value={targetNet}
                onChange={(e) => setTargetNet(e.target.value)}
                className="h-8 w-28 rounded-md border border-border bg-background px-2 text-xs font-mono"
                aria-label="Liquidity target net INR"
                title="Liquidity target (net INR)"
              />
            </div>
            {tradePlan && (
              <div className="text-xs font-mono space-y-1">
                <p>
                  {tradePlan.action} · entry {tradePlan.entry_range || "—"} · target ₹
                  {tradePlan.target_price} · stop ₹{tradePlan.stop_loss}
                </p>
                <p className="text-muted-foreground font-sans">{tradePlan.executive_summary}</p>
              </div>
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
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={committeeBusy || !analysis}
                onClick={() => void handleRunArena()}
                className="h-9 px-3 rounded-md bg-sky-600 text-white text-xs font-semibold hover:bg-sky-500 disabled:opacity-50"
              >
                {committeeBusy ? "Running…" : "Run Arena"}
              </button>
              <button
                type="button"
                disabled={committeeBusy || !analysis}
                onClick={() => void handleRunCommittee()}
                className="h-9 px-3 rounded-md border border-border text-xs hover:bg-muted disabled:opacity-50"
              >
                Run Committee (LLM, slow)
              </button>
              <button
                type="button"
                disabled={!tradePlan || paperBusy}
                onClick={() => void handlePaper("buy")}
                className="h-9 px-3 rounded-md border border-border text-xs disabled:opacity-50 hover:bg-muted"
              >
                Paper Buy
              </button>
              <button
                type="button"
                disabled={!tradePlan || paperBusy}
                onClick={() => void handlePaper("sell")}
                className="h-9 px-3 rounded-md border border-border text-xs disabled:opacity-50 hover:bg-muted"
              >
                Paper Sell
              </button>
            </div>
            {paperMsg && <p className="text-[11px] text-muted-foreground font-mono">{paperMsg}</p>}
          </div>

          {scorecard && (
            <div className="rounded-lg border border-border p-4 space-y-2">
              <h3 className="text-sm font-semibold flex items-center gap-2">
                <ShieldAlert className="h-4 w-4" /> Engine scorecard
              </h3>
              <div className="text-[11px] font-mono space-y-1">
                {scorecard.map((e) => (
                  <div key={String(e.engine_id)} className="flex justify-between gap-2">
                    <span>
                      {String(e.engine_id)} · {String(e.status)}
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
        </div>
      </details>
    </Shell>
  );
}
