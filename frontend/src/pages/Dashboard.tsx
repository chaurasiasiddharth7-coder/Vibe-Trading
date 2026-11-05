import { Suspense, lazy, useCallback, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { LayoutDashboard, X } from "lucide-react";
import type { PaperTrade, TrackRecord } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Guide } from "@/pages/Guide";
import { inr, trustSentence } from "@/components/desk/plain";
import { ApiKeyGate } from "@/components/desk/ApiKeyGate";
import { ServerConnection } from "@/components/desk/ServerConnection";
import { DASHBOARD_ONLY } from "@/lib/hosted";
import { CustomizePanel } from "@/components/desk/CustomizePanel";
import { investedDate } from "@/components/desk/tradeFilters";
import { DeskPrefsProvider, useDeskPrefs, visibleTiles } from "@/lib/deskPrefs";

const Desk = lazy(() => import("@/pages/Desk").then((m) => ({ default: m.Desk })));

type Tab = "ideas" | "trades" | "chart";
const TABS: Array<{ key: Tab; label: string }> = [
  { key: "ideas", label: "Today's ideas" },
  { key: "trades", label: "My practice trades" },
  { key: "chart", label: "Chart and tools" },
];
const WELCOME_KEY = "alphadesk-welcome-dismissed";

function readDismissed(): boolean {
  try {
    return localStorage.getItem(WELCOME_KEY) === "1";
  } catch {
    return false;
  }
}

const money = (v: number) => `${v >= 0 ? "+" : "−"}${inr(Math.abs(v), 0)}`;
const resultOf = (t: PaperTrade) => (t.status === "OPEN" ? (t.unrealized_inr ?? 0) : (t.pnl_inr ?? 0));

function Stat({ label, value, tone, strip, compact }: { label: string; value: string; tone?: "good" | "bad"; strip?: boolean; compact?: boolean }) {
  return (
    <div className={cn(strip ? "px-4 py-1 min-w-32" : cn("rounded-md bg-muted/50", compact ? "p-2" : "p-3"))}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-mono text-base", tone === "good" && "text-emerald-400", tone === "bad" && "text-rose-400")}>
        {value}
      </dd>
    </div>
  );
}

type TileView = { label: string; value: string; tone?: "good" | "bad" };
const toneOf = (n: number): "good" | "bad" => (n >= 0 ? "good" : "bad");

/** Every tile the person can pick, worked out from their practice trades. */
function tileViews(trades: PaperTrade[]): Record<string, TileView> {
  const open = trades.filter((t) => t.status === "OPEN");
  const closed = trades.filter((t) => t.status === "CLOSED");
  const openSum = open.reduce((a, t) => a + resultOf(t), 0);
  const closedSum = closed.reduce((a, t) => a + resultOf(t), 0);
  const ranked = [...trades].sort((a, b) => resultOf(b) - resultOf(a));
  const best = ranked[0];
  const worst = ranked[ranked.length - 1];
  const wins = closed.filter((t) => resultOf(t) > 0).length;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
  const startedToday = trades.filter((t) => investedDate(t) === today).length;
  return {
    open_pnl: { label: `Open trades (${open.length})`, value: open.length ? money(openSum) : "—", tone: open.length ? toneOf(openSum) : undefined },
    closed_pnl: { label: `Finished trades (${closed.length})`, value: closed.length ? money(closedSum) : "—", tone: closed.length ? toneOf(closedSum) : undefined },
    winning: { label: "Winning now", value: trades.length ? `${trades.filter((t) => resultOf(t) > 0).length} of ${trades.length}` : "—" },
    money_open: { label: "Money in open trades", value: open.length ? inr(open.reduce((a, t) => a + t.qty * t.entry, 0), 0) : "—" },
    win_rate: { label: "Win rate", value: closed.length ? `${Math.round((wins / closed.length) * 100)}% (${wins} of ${closed.length})` : "—" },
    best: { label: "Best trade", value: best ? `${money(resultOf(best))} ${best.symbol}` : "—", tone: best ? toneOf(resultOf(best)) : undefined },
    worst: { label: "Worst trade", value: worst ? `${money(resultOf(worst))} ${worst.symbol}` : "—", tone: worst ? toneOf(resultOf(worst)) : undefined },
    today: { label: "Invested today", value: String(startedToday) },
  };
}

export function Dashboard() {
  return (
    <DeskPrefsProvider>
      <DashboardView />
    </DeskPrefsProvider>
  );
}

function DashboardView() {
  const { prefs, me, ready, compact } = useDeskPrefs();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const visibleTabs = TABS.filter((t) => !prefs.hidden_tabs.includes(t.key));
  const wanted: Tab = raw === "trades" || raw === "chart" ? raw : "ideas";
  const tab: Tab = visibleTabs.some((t) => t.key === wanted) ? wanted : (visibleTabs[0]?.key ?? "ideas");
  const stacked = prefs.layout === "stacked";
  const showIdeas = visibleTabs.some((t) => t.key === "ideas");
  const showTrades = visibleTabs.some((t) => t.key === "trades");
  const showChart = visibleTabs.some((t) => t.key === "chart");
  const guideView = stacked ? (showIdeas && showTrades ? "all" : showTrades ? "trades" : "ideas") : tab === "trades" ? "trades" : "ideas";
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [track, setTrack] = useState<TrackRecord | null>(null);
  const [chartSeen, setChartSeen] = useState(tab === "chart");
  const tiles = tileViews(trades);
  const shownTiles = visibleTiles(prefs).filter((id) => tiles[id]);
  const [needsKey, setNeedsKey] = useState(false);
  const [welcome, setWelcome] = useState(() => !readDismissed());
  const refs = useRef<Record<Tab, HTMLButtonElement | null>>({ ideas: null, trades: null, chart: null });

  const onTradesChange = useCallback((t: PaperTrade[], tr: TrackRecord | null) => {
    setTrades(t);
    setTrack(tr);
  }, []);

  const go = (next: Tab) => {
    if (next === "chart") setChartSeen(true);
    setParams(next === "ideas" ? {} : { tab: next }, { replace: true });
  };

  const onKey = (e: KeyboardEvent, i: number) => {
    const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!move) return;
    e.preventDefault();
    const next = visibleTabs[(i + move + visibleTabs.length) % visibleTabs.length].key;
    go(next);
    refs.current[next]?.focus();
  };

  const dismiss = () => {
    setWelcome(false);
    try {
      localStorage.setItem(WELCOME_KEY, "1");
    } catch {
      // storage unavailable: the card returns next visit
    }
  };

  const open = trades.filter((t) => t.status === "OPEN");
  const attention = open.filter((t) => t.suggestion && t.suggestion.action !== "hold");

  return (
    <main className={cn("min-h-[calc(100vh-2rem)] max-w-6xl", compact ? "p-3 sm:p-4 space-y-3" : "p-4 sm:p-6 space-y-5")} data-density={prefs.density}>
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <LayoutDashboard className="h-6 w-6 text-emerald-500" aria-hidden />
          Dashboard
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Today's ideas, your practice trades and the charts, in one place. All trades here use fake money.
        </p>
        {me && !me.is_owner && (
          <p className="text-xs text-muted-foreground">
            Signed in as <span className="font-semibold text-foreground">{me.name}</span>. Your practice trades, intraday days and view are your own.
          </p>
        )}
      </header>

      {ready && <CustomizePanel />}

      <ApiKeyGate onNeedsKey={setNeedsKey} />
      {DASHBOARD_ONLY && params.get("settings") === "1" && <ServerConnection />}

      <section aria-label="Summary" className="space-y-3">
        {shownTiles.length > 0 && (
          <dl
            className={cn(
              prefs.tile_style === "strip"
                ? "flex flex-wrap divide-x divide-border rounded-md bg-muted/50 py-2"
                : cn("grid grid-cols-2 lg:grid-cols-4", compact ? "gap-2" : "gap-3"),
            )}
          >
            {shownTiles.map((id) => (
              <Stat key={id} {...tiles[id]} strip={prefs.tile_style === "strip"} compact={compact} />
            ))}
          </dl>
        )}
        <p className="text-xs text-muted-foreground">{track ? trustSentence(track) : needsKey ? "Waiting for your server key to load your trades." : "Loading your track record…"}</p>
        {attention.length > 0 && (
          <button
            type="button"
            onClick={() => go("trades")}
            className="w-full text-left rounded-md border border-amber-700/60 bg-amber-950/30 px-3 min-h-11 text-sm text-amber-200 hover:bg-amber-950/50"
          >
            {attention.length} open trade{attention.length === 1 ? "" : "s"} could use a look:{" "}
            {attention.map((t) => t.symbol).join(", ")}. Open My practice trades to see the suggestion.
          </button>
        )}
      </section>

      {welcome && (
        <section aria-label="How this works" className="rounded-lg border border-border p-4 relative">
          <button
            type="button"
            onClick={dismiss}
            aria-label="Hide this guide"
            className="absolute right-2 top-2 h-9 w-9 grid place-items-center rounded-md hover:bg-muted"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
          <h2 className="text-base font-semibold">New here? Three steps</h2>
          <ol className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <li>
              <span className="font-semibold">1. Check ideas.</span> Pick a list of stocks. They are sorted into buy,
              short and stay-away.
            </li>
            <li>
              <span className="font-semibold">2. Practice.</span> Press "Practice with fake money" on an idea. It
              closes by itself at take-profit, cut-loss or after 20 trading days.
            </li>
            <li>
              <span className="font-semibold">3. Learn.</span> Finished trades say what went right or wrong. Don't
              use real money until the track record looks good.
            </li>
          </ol>
        </section>
      )}

      {!stacked && (
        <div role="tablist" aria-label="Dashboard sections" className="flex gap-1 border-b border-border overflow-x-auto">
          {visibleTabs.map((tb, i) => (
            <button
              key={tb.key}
              ref={(el) => {
                refs.current[tb.key] = el;
              }}
              role="tab"
              id={`tab-${tb.key}`}
              aria-selected={tab === tb.key}
              aria-controls={`panel-${tb.key}`}
              tabIndex={tab === tb.key ? 0 : -1}
              onClick={() => go(tb.key)}
              onKeyDown={(e) => onKey(e, i)}
              className={cn(
                "min-h-11 px-4 text-sm whitespace-nowrap border-b-2 -mb-px",
                tab === tb.key
                  ? "border-emerald-500 font-semibold"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tb.label}
              {tb.key === "trades" && open.length > 0 && (
                <span className="ml-2 text-xs font-mono text-muted-foreground">{open.length}</span>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Mounted even when only the chart is shown: the summary tiles read their trades from it. */}
      <div
        {...(stacked
          ? {}
          : {
              role: "tabpanel",
              id: tab === "trades" ? "panel-trades" : "panel-ideas",
              "aria-labelledby": tab === "trades" ? "tab-trades" : "tab-ideas",
            })}
        hidden={stacked ? !showIdeas && !showTrades : tab === "chart"}
      >
        <Guide embedded view={guideView} onTradesChange={onTradesChange} />
      </div>
      {showChart && (stacked || chartSeen) && (
        <div {...(stacked ? {} : { role: "tabpanel", id: "panel-chart", "aria-labelledby": "tab-chart" })} hidden={!stacked && tab !== "chart"}>
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
            <Desk embedded />
          </Suspense>
        </div>
      )}
    </main>
  );
}
