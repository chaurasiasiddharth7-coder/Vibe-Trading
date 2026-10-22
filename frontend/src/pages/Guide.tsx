import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, Compass, Loader2, Minus, Plus } from "lucide-react";
import {
  api,
  type BoardRow,
  type BoardSide,
  type ExitPlan,
  type PaperTrade,
  type SignalBoard,
  type TrackRecord,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PracticeTrades } from "@/components/desk/PracticeTrades";
import { SymbolInsight } from "@/components/desk/SymbolInsight";
import { JobLostError, friendlyError, pollJob, type Progress } from "@/lib/pollJob";
import {
  EXIT_PLANS,
  SIDE_LABEL,
  closestToBuy,
  historyVerdict,
  missingForBuy,
  inr,
  pct,
  perHundred,
  planFor,
  trustSentence,
  whatWeSee,
} from "@/components/desk/plain";

type SortKey = "signal" | "history" | "risk";

type Prefs = {
  exitPlan: ExitPlan;
  ideasCollapsed: boolean;
  universe: string;
  sector: string;
  riskInr: number;
  show: Record<BoardSide, boolean>;
  includeFunds: boolean;
  provenOnly: boolean;
  sort: SortKey;
};

const DEFAULT_PREFS: Prefs = {
  exitPlan: "fixed",
  ideasCollapsed: false,
  universe: "nifty100",
  sector: "",
  riskInr: 1000,
  show: { LONG: true, SHORT: true, STEER_CLEAR: true, NO_EDGE: false },
  includeFunds: true,
  provenOnly: false,
  sort: "signal",
};

const PREFS_KEY = "alphadesk-guide-prefs";
const PAGE = 24;
const FRESH_MS = 30 * 60 * 1000;

const FALLBACK_UNIVERSES = [
  { key: "core", label: "Original list (30 big companies)" },
  { key: "nifty50", label: "Nifty 50: India's 50 biggest companies" },
  { key: "nifty100", label: "Nifty 100: top 100 companies" },
  { key: "nifty200", label: "Nifty 200: top 200 companies" },
  { key: "nifty500", label: "Nifty 500: top 500 companies" },
  { key: "midcap150", label: "Midcap 150: mid-size companies" },
  { key: "smallcap250", label: "Smallcap 250: smaller companies" },
];


function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<Prefs>) };
  } catch {
    // storage unavailable: use defaults
  }
  return DEFAULT_PREFS;
}

function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    // storage unavailable: settings last for this visit only
  }
}

const SIDE_TONE: Record<BoardSide, string> = {
  LONG: "text-emerald-400 border-emerald-700/50",
  SHORT: "text-rose-400 border-rose-700/50",
  STEER_CLEAR: "text-amber-400 border-amber-700/50",
  NO_EDGE: "text-muted-foreground border-border",
};

const GLOSSARY: Array<[string, string]> = [
  ["Buy idea", "The checks suggest the price is more likely to rise. You'd buy now and sell later."],
  [
    "Short idea",
    "The checks suggest the price is more likely to fall. You'd sell first and buy back cheaper. In the normal cash market this must be closed the same day. Holding it longer needs futures or options.",
  ],
  ["Stay away", "Weak and falling. Not a good time to buy, and not clean enough to short."],
  ["Take profit", "The price where you'd close the trade and keep the gain."],
  ["Cut loss (stop-loss)", "The price where you'd close the trade to stop a loss from growing. Decide it before you enter."],
  [
    "Trailing cut-loss (trailing stop)",
    "A cut-loss that follows the price as it moves your way and never moves back. It locks in part of a gain, but normal ups and downs can also close a trade early.",
  ],
  [
    "Break-even",
    "Moving the cut-loss to your entry price once the trade is comfortably up, so a winner can't turn into a loser.",
  ],
  ["Most I'll lose per trade", "We size each idea so that hitting the cut-loss price loses about this much, not more."],
  ["Trend", "Is the price generally heading up or down lately? (SuperTrend indicator)"],
  ["Averages", "Is the price above or below its 50-day and 200-day average? Above is healthier."],
  ["Pressure", "Are buyers or sellers getting stronger? (MACD indicator)"],
  ["AI forecast", "Kronos, a model trained on price charts, guesses the next 3 weeks. It's often wrong on its own."],
  ["Practice trade", "A pretend trade with fake money. It closes by itself when the price hits take-profit or cut-loss, or after 20 trading days."],
  ["Past test", "We replay the same rules over 5 years of past prices to see how they would have done. The past doesn't guarantee the future."],
];

function Toggle({
  on,
  onChange,
  children,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={() => onChange(!on)}
      className={cn(
        "min-h-9 px-3 rounded-md border text-xs transition-colors duration-150 ease-out motion-reduce:transition-none",
        on ? "bg-emerald-600/20 border-emerald-600 text-foreground" : "border-border text-muted-foreground hover:bg-muted",
      )}
    >
      <span aria-hidden className="mr-1">{on ? "✓" : "○"}</span>
      {children}
    </button>
  );
}

function TrustCard({
  track,
  board,
  busy,
  onSettle,
  onReplay,
  note,
  replayProgress,
}: {
  track: TrackRecord | null;
  board: SignalBoard | null;
  busy: string | null;
  onSettle: () => void;
  onReplay: () => void;
  note: string | null;
  replayProgress: Progress;
}) {
  const n = track?.closed_trades ?? 0;
  const h = board?.history;
  return (
    <section className="rounded-lg border border-border p-4 space-y-3" aria-labelledby="trust-h">
      <h2 id="trust-h" className="text-base font-semibold">
        Can you trust these ideas yet?
      </h2>
      {track && <p className="text-sm">{trustSentence(track)}</p>}
      <div>
        <div className="flex justify-between text-xs text-muted-foreground mb-1">
          <span>Finished practice trades</span>
          <span className="font-mono">{Math.min(n, 30)} / 30</span>
        </div>
        <div className="h-2 rounded bg-muted overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={30} aria-valuenow={Math.min(n, 30)}>
          <div className="h-full bg-emerald-500" style={{ width: `${Math.min(100, (n / 30) * 100)}%` }} />
        </div>
      </div>
      {h && (
        <div className="text-sm space-y-1">
          <p>
            <span className="font-semibold">Past test ({h.period.replace("y", " years")}):</span> buy ideas won{" "}
            {pct(h.long.win_rate)} of {h.long.trades} times and {perHundred(h.long.avg_r)}. Short ideas won{" "}
            {pct(h.short.win_rate)} of {h.short.trades} times and {perHundred(h.short.avg_r)}.
          </p>
          {h.short.avg_r != null && h.short.avg_r < 0 && (
            <p className="text-amber-300 text-xs">
              In the past, short ideas lost money with these rules. Treat them with extra caution.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Last run {new Date(h.ran_at).toLocaleString("en-IN")}. The past test doesn't use the AI forecast.
          </p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onSettle}
          disabled={busy !== null}
          className="min-h-9 px-3 rounded-md border border-border text-xs hover:bg-muted disabled:opacity-50"
        >
          {busy === "settle" ? "Checking…" : "Update my practice trades"}
        </button>
        <button
          type="button"
          onClick={onReplay}
          disabled={busy !== null}
          className="min-h-9 px-3 rounded-md border border-border text-xs hover:bg-muted disabled:opacity-50"
        >
          {busy === "replay"
            ? `Testing 5 years… ${replayProgress?.total ? `${replayProgress.done} of ${replayProgress.total}` : ""}`
            : "Re-run the past test for this list"}
        </button>
      </div>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </section>
  );
}

/** Collapsed until opened, so scanning a long list stays fast. */
function IdeaAnalysis({ row, openTrade }: { row: BoardRow; openTrade: boolean }) {
  const [open, setOpen] = useState(false);
  const dir = row.side === "LONG" || row.side === "SHORT" ? row.side : undefined;
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)} className="text-sm rounded-md border border-border">
      <summary className="cursor-pointer select-none min-h-10 px-3 flex items-center text-xs font-semibold">
        See the analysis: chart, candles, volume, support and resistance
      </summary>
      {open && (
        <div className="px-3 pb-3">
          <SymbolInsight
            symbol={row.symbol}
            side={dir}
            entry={row.entry}
            stop={row.stop}
            target={row.target}
            votes={row.votes}
            reasons={row.reasons}
            savedForecastPct={row.kronos_change_pct}
            isOpenTrade={openTrade}
            compact
            chartHeight={300}
          />
        </div>
      )}
    </details>
  );
}

function IdeaCard({
  row,
  riskInr,
  openTrade,
  busy,
  onPractice,
  minimized,
  onToggleMin,
  exitPlan,
}: {
  exitPlan: ExitPlan;
  row: BoardRow;
  riskInr: number;
  openTrade: boolean;
  busy: boolean;
  onPractice: (row: BoardRow) => void;
  minimized: boolean;
  onToggleMin: (symbol: string) => void;
}) {
  const plan = planFor(row, riskInr);
  const past = historyVerdict(row.history?.symbol_side);
  const sees = whatWeSee(row);
  const tradeable = row.side === "LONG" || row.side === "SHORT";
  const hs = row.history?.symbol_side;
  return (
    <article className="rounded-lg border border-border p-4 space-y-3 flex flex-col">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">{row.symbol}</h3>
          {(row.name && row.name !== row.symbol) || row.industry ? (
            <p className="text-xs text-muted-foreground">
              {row.name && row.name !== row.symbol ? row.name : ""}
              {row.name && row.name !== row.symbol && row.industry ? " · " : ""}
              {row.industry}
            </p>
          ) : null}
          <p className="text-xs text-muted-foreground font-mono">
            {inr(row.price)}
            {row.change_percent != null && (
              <span className={row.change_percent >= 0 ? "text-emerald-400" : "text-rose-400"}>
                {" "}
                {row.change_percent >= 0 ? "+" : ""}
                {row.change_percent}% today
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <span className={cn("text-xs font-semibold px-2 py-1 rounded border", SIDE_TONE[row.side])}>
            {SIDE_LABEL[row.side]}
          </span>
          <button
            type="button"
            onClick={() => onToggleMin(row.symbol)}
            aria-expanded={!minimized}
            aria-label={minimized ? `Expand ${row.symbol}` : `Minimize ${row.symbol}`}
            title={minimized ? "Expand" : "Minimize"}
            className="h-8 w-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
          >
            {minimized ? <Plus className="h-4 w-4" aria-hidden /> : <Minus className="h-4 w-4" aria-hidden />}
          </button>
        </div>
      </header>
      {minimized ? (
        <p className="text-xs text-muted-foreground">
          {tradeable && plan
            ? `${row.side === "LONG" ? "Buy" : "Short"} ${plan.qty} at ${inr(row.entry)} · gain ${inr(plan.gainIfRight, 0)} / lose ${inr(plan.lossIfWrong, 0)}`
            : sees[0]}
        </p>
      ) : (
      <>

      <ul className="text-sm space-y-1 list-disc pl-4">
        {sees.slice(0, 2).map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
      {sees.length > 2 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">More reasons</summary>
          <ul className="list-disc pl-4 mt-1 space-y-1">
            {sees.slice(2).map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </details>
      )}

      {tradeable && plan && (
        <div className="rounded-md bg-muted/50 p-3 text-sm space-y-1">
          <p>
            {row.side === "LONG" ? "Buy" : "Short"} <span className="font-mono font-semibold">{plan.qty}</span> shares
            at about <span className="font-mono">{inr(row.entry)}</span> (
            <span className="font-mono">{inr(plan.invest, 0)}</span>
            {row.side === "SHORT" ? " position" : " needed"}).
          </p>
          <p>
            <span className="text-emerald-400">If right:</span> exit at{" "}
            <span className="font-mono">{inr(row.target)}</span>, gain about{" "}
            <span className="font-mono">{inr(plan.gainIfRight, 0)}</span>.
          </p>
          <p>
            <span className="text-rose-400">If wrong:</span> exit at <span className="font-mono">{inr(row.stop)}</span>,
            lose about <span className="font-mono">{inr(plan.lossIfWrong, 0)}</span>.
          </p>
          {exitPlan !== "fixed" && (
            <p className="text-xs text-muted-foreground">
              {exitPlan === "breakeven"
                ? "Once it's up by as much as you risked, the cut-loss moves to your entry price."
                : exitPlan === "trail"
                  ? "The cut-loss then follows the price as it moves your way."
                  : "No take-profit: the cut-loss follows the price and you exit when it's hit."}
            </p>
          )}
          {row.side === "SHORT" && (
            <p className="text-xs text-muted-foreground">
              Same day only in the cash market. Holding it longer needs futures or options.
            </p>
          )}
        </div>
      )}

      {tradeable && (
        <p
          className={cn(
            "text-xs",
            past.tone === "good" && "text-emerald-300",
            past.tone === "bad" && "text-rose-300",
            (past.tone === "mixed" || past.tone === "unknown") && "text-muted-foreground",
          )}
        >
          <span className="font-semibold">{past.label}.</span>
          {hs && hs.trades > 0 && (
            <>
              {" "}
              On {row.symbol} this rule won {Math.round((hs.win_rate ?? 0) * hs.trades)} of {hs.trades} times and{" "}
              {perHundred(hs.avg_r)}.
            </>
          )}
        </p>
      )}
      {row.stale && (
        <p className="text-xs text-amber-300">
          Price is from {row.as_of}, so it may be out of date. Check the live price before acting.
        </p>
      )}

      <IdeaAnalysis row={row} openTrade={openTrade} />

      {tradeable && (
        <div className="mt-auto pt-1">
          <button
            type="button"
            disabled={openTrade || busy}
            onClick={() => onPractice(row)}
            className="min-h-10 w-full rounded-md bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50 disabled:hover:bg-emerald-600"
          >
            {openTrade ? "Practice trade already open" : busy ? "Opening…" : "Practice with fake money"}
          </button>
        </div>
      )}
      </>
      )}
    </article>
  );
}

export function Guide({
  embedded = false,
  view = "all",
  onTradesChange,
}: {
  embedded?: boolean;
  view?: "all" | "ideas" | "trades";
  onTradesChange?: (trades: PaperTrade[], track: TrackRecord | null) => void;
} = {}) {
  const showIdeas = view !== "trades";
  const showTrades = view !== "ideas";
  const Shell = embedded ? "div" : "main";
  const [settingsOpen, setSettingsOpen] = useState(embedded ? false : true);
  const [searchParams] = useSearchParams();
  const wantSettings = searchParams.get("settings") === "1";
  useEffect(() => {
    if (!wantSettings) return;
    setSettingsOpen(true);
    const t = window.setTimeout(() => document.getElementById("settings-h")?.scrollIntoView({ block: "start" }), 50);
    return () => window.clearTimeout(t);
  }, [wantSettings]);
  const Heading = embedded ? "h2" : "h1";
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [track, setTrack] = useState<TrackRecord | null>(null);
  useEffect(() => {
    onTradesChange?.(trades, track);
  }, [trades, track, onTradesChange]);
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [board, setBoard] = useState<SignalBoard | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [lookup, setLookup] = useState("");
  const [extra, setExtra] = useState<BoardRow | null>(null);
  const [search, setSearch] = useState("");
  const [visible, setVisible] = useState(PAGE);
  const [progress, setProgress] = useState<Progress>(null);
  const [replayProgress, setReplayProgress] = useState<Progress>(null);
  const [universes, setUniverses] = useState(FALLBACK_UNIVERSES);
  const scanToken = useRef(0);
  const [minimized, setMinimized] = useState<Set<string>>(new Set());
  const toggleMin = (sym: string) =>
    setMinimized((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym);
      else next.add(sym);
      return next;
    });

  useEffect(() => {
    void api
      .getUniverses()
      .then((r) => setUniverses(r.data))
      .catch(() => undefined);
  }, []);

  const update = (patch: Partial<Prefs>) => {
    setPrefs((p) => {
      const next = { ...p, ...patch };
      savePrefs(next);
      return next;
    });
  };

  const refreshTrades = useCallback(async () => {
    try {
      const res = await api.getBoardPaper();
      setTrades(res.trades);
      setTrack(res.track_record);
    } catch {
      // keep last known
    }
  }, []);

  const check = useCallback(
    async (force = false) => {
      const token = ++scanToken.current;
      const alive = () => token === scanToken.current;
      setLoading(true);
      setError(null);
      setProgress(null);
      try {
        if (!force) {
          const last = await api.getBoardLast(prefs.universe).catch(() => null);
          const saved = last?.data;
          if (saved && saved.include_funds === prefs.includeFunds && alive()) {
            setBoard(saved);
            setTrack(saved.track_record);
            const age = saved.scanned_at ? Date.now() - Date.parse(saved.scanned_at) : Infinity;
            if (age < FRESH_MS) {
              void refreshTrades();
              return;
            }
          }
        }
        const start = () => api.postBoardScan({ universe: prefs.universe, include_funds: prefs.includeFunds });
        const watch = (id: string) => pollJob<SignalBoard>(id, (p) => alive() && setProgress(p), alive);
        let result: SignalBoard | null;
        try {
          result = await watch((await start()).job_id);
        } catch (e) {
          if (!(e instanceof JobLostError)) throw e;
          // The server restarted mid-scan: start it again once.
          result = await watch((await start()).job_id);
        }
        if (!result || !alive()) return;
        setBoard(result);
        setTrack(result.track_record);
        const closed = result.just_closed ?? [];
        if (closed.length > 0) {
          setNote(`${closed.length} practice trade(s) just finished. See "My practice trades".`);
        }
        void refreshTrades();
      } catch (e) {
        if (alive()) setError(friendlyError(e));
      } finally {
        if (alive()) {
          setLoading(false);
          setProgress(null);
        }
      }
    },
    [prefs.universe, prefs.includeFunds, refreshTrades],
  );

  useEffect(() => {
    void refreshTrades();
  }, [refreshTrades]);

  useEffect(() => {
    void check();
  }, [check]);

  useEffect(() => {
    setVisible(PAGE);
  }, [prefs, search, board]);

  const sectors = useMemo(
    () =>
      [...new Set((board?.rows ?? []).map((r) => r.industry).filter((x): x is string => Boolean(x)))].sort(),
    [board],
  );

  const openSymbols = useMemo(
    () => new Set(trades.filter((t) => t.status === "OPEN").map((t) => t.symbol)),
    [trades],
  );

  const ideas = useMemo(() => {
    if (!board) return [];
    let rows = (board.rows ?? []).filter((r) => prefs.show[r.side]);
    if (!prefs.includeFunds) rows = rows.filter((r) => r.asset_class === "stock");
    if (prefs.provenOnly) rows = rows.filter((r) => historyVerdict(r.history?.symbol_side).tone === "good");
    if (prefs.sector) rows = rows.filter((r) => r.industry === prefs.sector);
    const q = search.trim().toLowerCase();
    if (q) rows = rows.filter((r) => r.symbol.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q));
    const order: Record<BoardSide, number> = { LONG: 0, SHORT: 1, STEER_CLEAR: 2, NO_EDGE: 3 };
    const key = (r: BoardRow) => {
      if (prefs.sort === "history") return -(r.history?.symbol_side?.avg_r ?? -99);
      if (prefs.sort === "risk") return r.risk_pct ?? 99;
      return -Math.abs(r.score);
    };
    return [...rows].sort((a, b) => order[a.side] - order[b.side] || key(a) - key(b));
  }, [board, prefs, search]);

  const practice = async (row: BoardRow) => {
    const symbol = row.symbol;
    setBusy(`open:${symbol}`);
    setNote(null);
    try {
      const res = await api.postBoardPaper({
        symbol,
        risk_inr: prefs.riskInr,
        name: row.name,
        industry: row.industry,
        universe: prefs.universe,
        plan: prefs.exitPlan,
      });
      setNote(
        `Practice trade opened: ${res.trade.side === "LONG" ? "buy" : "short"} ${res.trade.qty} ${symbol} at ${inr(res.trade.entry)}. It closes by itself at take-profit, cut-loss, or after 20 trading days.`,
      );
      await refreshTrades();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const changePlan = async (tradeId: string, plan: ExitPlan) => {
    setBusy(`plan:${tradeId}`);
    setNote(null);
    try {
      await api.postBoardPlan(tradeId, plan);
      setNote("Exit style changed. It applies from the next trading session.");
      await refreshTrades();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const tradeAction = async (tradeId: string, run: () => Promise<string>) => {
    setBusy(`plan:${tradeId}`);
    setNote(null);
    try {
      setNote(await run());
      await refreshTrades();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const moveStop = (tradeId: string, price: number) =>
    tradeAction(tradeId, async () => {
      await api.postBoardStop(tradeId, price);
      return `Cut-loss moved to ${inr(price)}. It applies from the next trading session.`;
    });

  const exitNow = (tradeId: string) =>
    tradeAction(tradeId, async () => {
      const res = await api.postBoardExit(tradeId);
      setTrack(res.track_record);
      return `Closed ${res.trade.symbol} at ${inr(res.trade.exit_price)}.`;
    });

  const settle = async () => {
    setBusy("settle");
    setNote(null);
    try {
      const res = await api.postBoardSettle();
      setTrack(res.track_record);
      setNote(
        res.closed.length === 0
          ? "No practice trade has hit its take-profit or cut-loss yet."
          : `${res.closed.length} practice trade(s) finished.`,
      );
      await refreshTrades();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const replay = async () => {
    setBusy("replay");
    setNote(null);
    setReplayProgress(null);
    try {
      const { job_id } = await api.postBoardReplay(prefs.universe);
      await pollJob(job_id, setReplayProgress, () => true);
      setNote("Past test updated. Re-checking prices to attach the new results…");
      await check(true);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      setReplayProgress(null);
    }
  };

  const doLookup = async () => {
    const sym = lookup.trim().toUpperCase();
    if (!sym) return;
    setBusy("lookup");
    setExtra(null);
    setError(null);
    try {
      const res = await api.postDeskBoard({ symbols: [sym] });
      const row = res.data.rows?.[0];
      if (!row) throw new Error(`Couldn't find data for ${sym}. Check the spelling (NSE symbol, e.g. TATASTEEL).`);
      setExtra(row);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const counts = board
    ? { LONG: board.long.length, SHORT: board.short.length, STEER_CLEAR: board.steer_clear.length, NO_EDGE: board.no_edge.length }
    : null;

  return (
    <Shell className={embedded ? "space-y-6" : "min-h-[calc(100vh-2rem)] p-4 sm:p-6 space-y-6 max-w-6xl"}>
      <header className={cn("space-y-1", embedded && "sr-only")}>
        <Heading className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <Compass className="h-6 w-6 text-emerald-500" aria-hidden />
          Your market guide
        </Heading>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Pick which Indian stocks to check, from the 50 biggest companies up to the top 500. We sort them into buy
          ideas, short ideas, and ones to stay away from. Each idea shows what you could gain or lose. Try ideas with
          fake money first.
        </p>
      </header>

      {error && (
        <div className="rounded-md border border-rose-800/50 bg-rose-950/30 text-rose-200 text-sm px-3 py-2" role="alert">
          {error}
        </div>
      )}

      {note && !showTrades && (
        <p role="status" className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          {note}
        </p>
      )}

      {showTrades && (
        <>
      <TrustCard
        track={track}
        board={board}
        busy={busy}
        onSettle={() => void settle()}
        onReplay={() => void replay()}
        note={note}
        replayProgress={replayProgress}
      />

      <PracticeTrades
        trades={trades}
        busy={busy === "settle"}
        onUpdate={() => void settle()}
        onChangePlan={(id, plan) => void changePlan(id, plan)}
        onMoveStop={(id, price) => void moveStop(id, price)}
        onExit={(id) => void exitNow(id)}
        planBusy={busy?.startsWith("plan:") ? busy.slice(5) : null}
      />
        </>
      )}

      {showIdeas && (
        <>
      <section className="rounded-lg border border-border p-4 space-y-4" aria-labelledby="settings-h">
        <details open={settingsOpen || (!board && !loading)} onToggle={(e) => setSettingsOpen(e.currentTarget.open)} className="space-y-4">
        <summary className="cursor-pointer select-none min-h-9 flex items-center">
          <h2 id="settings-h" className="text-base font-semibold">
            Your settings
          </h2>
          <span className="ml-2 text-xs text-muted-foreground">
            {settingsOpen ? "" : "stock list, industry, risk and filters"}
          </span>
        </summary>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="space-y-1 block">
            <span className="block text-sm">Which stocks to check</span>
            <select
              value={prefs.universe}
              onChange={(e) => update({ universe: e.target.value, sector: "" })}
              className="h-10 w-full max-w-sm rounded-md border border-border bg-background px-3 text-sm"
            >
              {universes.map((u) => (
                <option key={u.key} value={u.key}>
                  {u.label}
                </option>
              ))}
            </select>
            <span className="text-xs text-muted-foreground block">
              Bigger lists find more ideas but take longer: about 2–3 minutes per 100 stocks for the first check
              each day, then quick for the next 30 minutes. Smaller companies move more and can be riskier.
            </span>
          </label>
          <label className="space-y-1 block">
            <span className="block text-sm">Industry</span>
            <select
              value={prefs.sector}
              onChange={(e) => update({ sector: e.target.value })}
              className="h-10 w-full max-w-sm rounded-md border border-border bg-background px-3 text-sm"
            >
              <option value="">All industries</option>
              {sectors.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 block">
            <span className="block text-sm">Most I'm willing to lose on one trade</span>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">₹</span>
              <input
                type="number"
                min={100}
                step={100}
                value={prefs.riskInr}
                onChange={(e) => update({ riskInr: Math.max(100, Number(e.target.value) || 100) })}
                className="h-10 w-36 rounded-md border border-border bg-background px-3 font-mono text-sm"
              />
            </div>
            <span className="text-xs text-muted-foreground block">
              Share counts below are worked out from this. A common rule is 1% of your trading money.
            </span>
          </label>
          <div className="space-y-1 md:col-span-2">
            <label className="space-y-1 block">
              <span className="block text-sm">How to exit trades</span>
              <select
                value={prefs.exitPlan}
                onChange={(e) => update({ exitPlan: e.target.value as ExitPlan })}
                className="h-10 w-full max-w-sm rounded-md border border-border bg-background px-3 text-sm"
              >
                {EXIT_PLANS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-xs text-muted-foreground">
              {EXIT_PLANS.find((p) => p.key === prefs.exitPlan)?.explain} Used for new practice trades. You can change it
              on an open trade too.
            </p>
            {board?.history?.plans && (
              <div className="overflow-x-auto">
                <table className="text-xs mt-2 min-w-[28rem]">
                  <caption className="text-left text-muted-foreground pb-1">
                    How each exit style did in the past test ({board.history.period.replace("y", " years")}, per ₹100
                    risked):
                  </caption>
                  <thead className="text-muted-foreground">
                    <tr>
                      <th className="text-left font-normal pr-4 py-1">Exit style</th>
                      <th className="text-right font-normal pr-4 py-1">Buy ideas</th>
                      <th className="text-right font-normal pr-4 py-1">Short ideas</th>
                      <th className="text-right font-normal py-1">Won</th>
                    </tr>
                  </thead>
                  <tbody>
                    {EXIT_PLANS.map((p) => {
                      const r = board.history?.plans?.[p.key];
                      if (!r) return null;
                      const cell = (v: number | null) =>
                        v == null ? "—" : `${v >= 0 ? "+" : "−"}₹${Math.abs(Math.round(v * 100))}`;
                      const best = Math.max(
                        ...EXIT_PLANS.map((q) => board.history?.plans?.[q.key]?.long.avg_r ?? -9),
                      );
                      return (
                        <tr key={p.key} className={cn(p.key === prefs.exitPlan && "bg-muted/60")}>
                          <td className="pr-4 py-1">{p.label}</td>
                          <td
                            className={cn(
                              "text-right font-mono pr-4",
                              (r.long.avg_r ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400",
                              r.long.avg_r === best && "font-semibold",
                            )}
                          >
                            {cell(r.long.avg_r)}
                          </td>
                          <td
                            className={cn(
                              "text-right font-mono pr-4",
                              (r.short.avg_r ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400",
                            )}
                          >
                            {cell(r.short.avg_r)}
                          </td>
                          <td className="text-right font-mono">{pct(r.overall.win_rate)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <label className="space-y-1 block">
            <span className="block text-sm">Sort ideas by</span>
            <select
              value={prefs.sort}
              onChange={(e) => update({ sort: e.target.value as SortKey })}
              className="h-10 w-full max-w-xs rounded-md border border-border bg-background px-3 text-sm"
            >
              <option value="signal">Strongest signal first</option>
              <option value="history">Best past record first</option>
              <option value="risk">Smallest price swing first</option>
            </select>
          </label>
        </div>
        <div className="space-y-2">
          <span className="text-sm block">Show me</span>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(SIDE_LABEL) as BoardSide[]).map((side) => (
              <Toggle key={side} on={prefs.show[side]} onChange={(v) => update({ show: { ...prefs.show, [side]: v } })}>
                {SIDE_LABEL[side]}
                {counts && <span className="font-mono"> ({counts[side]})</span>}
              </Toggle>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Toggle on={prefs.includeFunds} onChange={(v) => update({ includeFunds: v })}>
              Include funds (ETFs)
            </Toggle>
            <Toggle on={prefs.provenOnly} onChange={(v) => update({ provenOnly: v })}>
              Only ideas that worked in the past
            </Toggle>
          </div>
        </div>
        </details>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search this list by name"
            aria-label="Search this list by name or symbol"
            className="h-10 w-56 rounded-md border border-border bg-background px-3 text-sm"
          />
          <input
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void doLookup()}
            placeholder="Or look up any NSE stock"
            aria-label="Look up any stock"
            className="h-10 w-64 rounded-md border border-border bg-background px-3 text-sm"
          />
          <button
            type="button"
            onClick={() => void doLookup()}
            disabled={busy === "lookup"}
            className="min-h-10 px-4 rounded-md border border-border text-sm hover:bg-muted disabled:opacity-50"
          >
            {busy === "lookup" ? "Looking…" : "Look up"}
          </button>
          <button
            type="button"
            onClick={() => void check(true)}
            disabled={loading}
            className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50 inline-flex items-center gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}
            {loading ? "Checking…" : "Check prices again"}
          </button>
        </div>
        {loading && (
          <div className="space-y-1" aria-live="polite">
            <p className="text-xs text-muted-foreground">
              {progress?.total
                ? `Checked ${progress.done} of ${progress.total} stocks…`
                : "Starting the check…"}{" "}
              You can keep using the page while this runs.
            </p>
            <div className="h-1.5 rounded bg-muted overflow-hidden">
              <div
                className="h-full bg-emerald-500 transition-[width] duration-300 ease-out motion-reduce:transition-none"
                style={{ width: `${progress?.total ? (progress.done / progress.total) * 100 : 2}%` }}
              />
            </div>
          </div>
        )}
        {board && (
          <p className="text-xs text-muted-foreground">
            {board.scanned} stocks checked · prices as of {board.as_of ?? "—"}.
            {(board.stale?.length ?? 0) > 0 &&
              ` ${board.stale.length} may be out of date (could be a holiday or a data gap).`}
            {board.failed.length > 0 && ` ${board.failed.length} couldn't be loaded and were skipped.`}
            {!board.kronos_live && " The AI forecast is off right now, so only the 3 price checks count."}
          </p>
        )}
      </section>

      {extra && (
        <section aria-label="Looked-up stock" className="space-y-2">
          <h2 className="text-base font-semibold">You looked up {extra.symbol}</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
            <IdeaCard
              row={extra}
              riskInr={prefs.riskInr}
              openTrade={openSymbols.has(extra.symbol)}
              busy={busy === `open:${extra.symbol}`}
              onPractice={(r) => void practice(r)}
              minimized={minimized.has(extra.symbol)}
              exitPlan={prefs.exitPlan}
              onToggleMin={toggleMin}
            />
          </div>
        </section>
      )}

      <section aria-labelledby="ideas-h" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            aria-expanded={!prefs.ideasCollapsed}
            onClick={() => update({ ideasCollapsed: !prefs.ideasCollapsed })}
            className="flex items-center gap-2 min-h-9 text-left"
          >
            <ChevronDown
              className={cn(
                "h-4 w-4 transition-transform duration-150 ease-out motion-reduce:transition-none",
                prefs.ideasCollapsed && "-rotate-90",
              )}
              aria-hidden
            />
            <h2 id="ideas-h" className="text-base font-semibold">
              Today's ideas{" "}
              {board && <span className="text-muted-foreground font-normal text-sm">({ideas.length} shown)</span>}
            </h2>
          </button>
          {!prefs.ideasCollapsed && ideas.length > 0 && (
            <button
              type="button"
              onClick={() =>
                setMinimized(
                  ideas.slice(0, visible).every((r) => minimized.has(r.symbol))
                    ? new Set()
                    : new Set(ideas.slice(0, visible).map((r) => r.symbol)),
                )
              }
              className="min-h-9 px-3 rounded-md text-xs text-muted-foreground hover:bg-muted"
            >
              {ideas.slice(0, visible).every((r) => minimized.has(r.symbol)) ? "Expand all cards" : "Minimize all cards"}
            </button>
          )}
        </div>
        {!prefs.ideasCollapsed && (
        <>
        {!board && loading && (
          <p className="text-sm text-muted-foreground">
            Checking prices… This can take a few minutes for a big list the first time each day.
          </p>
        )}
        {board && prefs.show.LONG && board.long.length === 0 && (
          <div className="rounded-lg border border-border p-4 space-y-2">
            <p className="text-sm">
              <span className="font-semibold">No buy ideas today.</span> A buy needs at least 3 of the 4 checks
              pointing up, and none of the {board.scanned} names in this list gets there right now. Try a wider list
              above, or wait. Not buying is a fine choice on days like this.
            </p>
            {closestToBuy(board.rows ?? []).length > 0 && (
              <div className="space-y-1">
                <p className="text-sm font-semibold">Closest to a buy (keep an eye on these)</p>
                <ul className="text-sm space-y-1">
                  {closestToBuy(board.rows ?? []).map((r) => (
                    <li key={r.symbol}>
                      <span className="font-semibold">{r.symbol}</span>{" "}
                      <span className="font-mono text-muted-foreground">{inr(r.price)}</span>: {missingForBuy(r)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {board && ideas.length === 0 && (
          <p className="text-sm text-muted-foreground">
            {Object.values(prefs.show).some(Boolean)
              ? "No cards match the lists you've switched on. Switch on more lists under \"Show me\" to see more."
              : "All lists are switched off. Switch some on under \"Show me\"."}
          </p>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
          {ideas.slice(0, visible).map((row) => (
            <IdeaCard
              key={row.symbol}
              row={row}
              riskInr={prefs.riskInr}
              openTrade={openSymbols.has(row.symbol)}
              busy={busy === `open:${row.symbol}`}
              onPractice={(r) => void practice(r)}
              minimized={minimized.has(row.symbol)}
              exitPlan={prefs.exitPlan}
              onToggleMin={toggleMin}
            />
          ))}
        </div>
        </>
        )}
      </section>

      {!prefs.ideasCollapsed && ideas.length > visible && (
        <button
          type="button"
          onClick={() => setVisible((v) => v + PAGE)}
          className="min-h-10 px-4 rounded-md border border-border text-sm hover:bg-muted"
        >
          Show {Math.min(PAGE, ideas.length - visible)} more ({ideas.length - visible} left)
        </button>
      )}



      <details className="rounded-lg border border-border">
        <summary className="cursor-pointer select-none px-4 py-3 text-base font-semibold">Words explained</summary>
        <dl className="px-4 pb-4 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
          {GLOSSARY.map(([term, meaning]) => (
            <div key={term}>
              <dt className="text-sm font-semibold">{term}</dt>
              <dd className="text-sm text-muted-foreground">{meaning}</dd>
            </div>
          ))}
        </dl>
      </details>

      <p className="text-xs text-muted-foreground max-w-3xl">
        This page is a tool that applies fixed rules to price data. It is not advice from a SEBI-registered adviser and
        doesn't know your finances. Ideas can and do lose money. Real trades are always placed by you, in your own broker
        app.
      </p>
        </>
      )}
    </Shell>
  );
}
