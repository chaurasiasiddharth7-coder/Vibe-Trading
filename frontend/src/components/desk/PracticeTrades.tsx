import { useEffect, useRef, useState } from "react";
import { ChevronDown, MoreHorizontal } from "lucide-react";
import type { ExitPlan, PaperTrade } from "@/lib/api";
import { SymbolInsight } from "@/components/desk/SymbolInsight";
import { cn } from "@/lib/utils";
import { EXIT_PLANS, historyVerdict, inr, isTrailing, perHundred, planLabel, whatWeSee } from "@/components/desk/plain";

type Filter = "all" | "open" | "closed";

const EXIT_LABEL: Record<string, string> = {
  target: "Hit take-profit",
  stop: "Hit cut-loss",
  trail: "Hit moved cut-loss",
  time: "Closed after 20 trading days",
  manual: "Closed by you",
};

const COLLAPSE_KEY = "alphadesk-practice-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(v: boolean) {
  try {
    localStorage.setItem(COLLAPSE_KEY, v ? "1" : "0");
  } catch {
    // storage unavailable: state lasts for this visit only
  }
}

function money(v: number | null | undefined) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : "−"}${inr(Math.abs(v), 0)}`;
}

function resultOf(t: PaperTrade): number | null {
  return t.status === "OPEN" ? (t.unrealized_inr ?? null) : (t.pnl_inr ?? null);
}

function daysSince(iso: string, until?: string): number {
  const end = until ? Date.parse(until) : Date.now();
  return Math.max(0, Math.floor((end - Date.parse(iso)) / 86_400_000));
}

/** Plain reasons the board picked this trade, from the saved snapshot. */
function pickedBecause(t: PaperTrade): { lines: string[]; partial: boolean } {
  const votes = t.votes ?? { trend: 0, structure: 0, momentum: 0, forecast: 0 };
  const d = t.decision;
  const score = d?.score ?? Object.values(votes).reduce((a, b) => a + b, 0);
  const lines = whatWeSee({
    votes,
    score,
    rsi: d?.rsi ?? null,
    kronos_change_pct: d?.kronos_change_pct ?? null,
  });
  if (!d) {
    // Older trades saved only the four votes, not the forecast size.
    const k = votes.forecast;
    lines[3] =
      k > 0
        ? "The AI forecast pointed up."
        : k < 0
          ? "The AI forecast pointed down."
          : "The AI forecast didn't lean either way.";
  }
  return { lines, partial: !d };
}

/** Where the price sits between cut-loss (0) and take-profit (1). */
function progressOf(t: PaperTrade): number | null {
  const price = t.status === "OPEN" ? t.current_price : t.exit_price;
  if (price == null) return null;
  const span = t.side === "LONG" ? t.target - t.stop : t.stop - t.target;
  if (span <= 0) return null;
  const pos = t.side === "LONG" ? price - t.stop : t.stop - price;
  return Math.min(1, Math.max(0, pos / span));
}

function Detail({
  t,
  onChangePlan,
  planBusy,
}: {
  t: PaperTrade;
  onChangePlan: (id: string, plan: ExitPlan) => void;
  planBusy: boolean;
}) {
  const d = t.decision;
  const why = pickedBecause(t);
  const past = historyVerdict(d?.history);
  const progress = progressOf(t);
  const entryAt = (() => {
    const span = t.side === "LONG" ? t.target - t.stop : t.stop - t.target;
    const pos = t.side === "LONG" ? t.entry - t.stop : t.stop - t.entry;
    return span > 0 ? pos / span : null;
  })();
  const open = t.status === "OPEN";
  return (
    <div className="px-3 pb-4 pt-1 space-y-4 text-sm">
      {t.data_warning && (
        <p role="note" className="rounded-md border border-amber-700/60 bg-amber-950/30 px-3 py-2 text-amber-200">
          {t.symbol} moved {t.data_warning.change_pct > 0 ? "+" : "−"}
          {Math.abs(t.data_warning.change_pct).toFixed(0)}% in one day on {t.data_warning.date}. That is unusually large
          and can be a data error or a split or bonus. Check the price before trusting this result.
        </p>
      )}
      {t.side === "SHORT" && (
        <p className="text-xs text-muted-foreground">
          Practice shorts are held for days. On the NSE a real cash-market short must be closed the same day. Holding
          one overnight needs stock futures or options.
        </p>
      )}
      <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-4 gap-y-2">
        <div>
          <dt className="text-xs text-muted-foreground">Type</dt>
          <dd>{t.side === "LONG" ? "Buy (profit if it rises)" : "Short (profit if it falls)"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Shares</dt>
          <dd className="font-mono">{t.qty}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Money in the trade</dt>
          <dd className="font-mono">{inr(t.qty * t.entry, 0)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Most it could lose (planned)</dt>
          <dd className="font-mono">{inr(t.risk_inr, 0)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Opened</dt>
          <dd>
            {new Date(t.opened_at).toLocaleDateString("en-IN")} ·{" "}
            {daysSince(t.opened_at, t.closed_at)} day{daysSince(t.opened_at, t.closed_at) === 1 ? "" : "s"}{" "}
            {open ? "ago" : "held"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Entered at</dt>
          <dd className="font-mono">{inr(t.entry)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{open ? "Price now" : "Exited at"}</dt>
          <dd className="font-mono">
            {inr(open ? t.current_price : t.exit_price)}
            {open && t.price_as_of && <span className="text-xs text-muted-foreground font-sans"> ({t.price_as_of})</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Take profit at</dt>
          <dd className="font-mono text-emerald-400">{inr(t.target)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">
            {open && t.stop_now != null && t.stop_now !== t.stop ? "Cut loss now (started at)" : "Cut loss at"}
          </dt>
          <dd className="font-mono text-rose-400">
            {open && t.stop_now != null && t.stop_now !== t.stop ? (
              <>
                {inr(t.stop_now)} <span className="text-muted-foreground text-xs">({inr(t.stop, 0)})</span>
              </>
            ) : (
              inr(t.stop)
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{open ? "Result so far" : "Final result"}</dt>
          <dd
            className={cn(
              "font-mono",
              (resultOf(t) ?? 0) > 0 && "text-emerald-400",
              (resultOf(t) ?? 0) < 0 && "text-rose-400",
            )}
          >
            {money(resultOf(t))}
          </dd>
        </div>
      </dl>

      <div className="rounded-md border border-border p-3 space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm">
            <span className="font-semibold">Exit style:</span> {planLabel(t.plan)}
          </span>
          {open && (
            <label className="flex items-center gap-2 text-xs">
              <span className="text-muted-foreground">Change to</span>
              <select
                value={t.plan ?? "fixed"}
                disabled={planBusy}
                onChange={(e) => onChangePlan(t.id, e.target.value as ExitPlan)}
                className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              >
                {EXIT_PLANS.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{EXIT_PLANS.find((p) => p.key === (t.plan ?? "fixed"))?.explain}</p>
        {open && t.stop_now != null && (
          <p className="text-xs">
            {t.stop_now === t.stop
              ? `The cut-loss hasn't moved yet. Best price so far: ${inr(t.best_price)}.`
              : (t.locked_in_inr ?? 0) >= 0
                ? `The cut-loss has moved to ${inr(t.stop_now)}. If it's hit now you'd keep about ${inr(t.locked_in_inr, 0)} (before costs).`
                : `The cut-loss has moved to ${inr(t.stop_now)}, so the most you can lose is now about ${inr(Math.abs(t.locked_in_inr ?? 0), 0)} instead of ${inr(t.risk_inr, 0)}.`}
          </p>
        )}
        {open && (t.plans?.length ?? 0) > 1 && (
          <p className="text-[11px] text-muted-foreground">
            Changed {(t.plans?.length ?? 1) - 1} time{(t.plans?.length ?? 1) - 1 === 1 ? "" : "s"}. A change applies from the
            next trading session after it's made.
          </p>
        )}
      </div>

      {!open && t.review && (
        <div className="rounded-md border border-border p-3 space-y-2">
          <h4 className="text-sm font-semibold">What we learned</h4>
          <p>{t.review.what_happened}</p>
          <p>{t.review.lesson}</p>
          <p className="text-xs text-muted-foreground">
            Best it got: {t.review.best_move_r.toFixed(1)}× your risk in your favour. Worst: {t.review.worst_move_r.toFixed(1)}× against.
            Same trade under each exit style (same days, same costs):{" "}
            {EXIT_PLANS.map((p) => {
              const v = t.review?.if_exit_style[p.key];
              return `${p.label} ${v == null ? "still open" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}R`}`;
            }).join(" · ")}
            .
          </p>
        </div>
      )}

      {progress != null && (
        <div className="space-y-1">
          <div className="relative h-2 rounded bg-gradient-to-r from-rose-900/60 via-muted to-emerald-900/60">
            {entryAt != null && (
              <span
                className="absolute top-[-3px] h-[14px] w-px bg-muted-foreground"
                style={{ left: `${entryAt * 100}%` }}
                title="Where you entered"
              />
            )}
            {open && t.stop_now != null && t.stop_now !== t.stop && (() => {
              const span = t.side === "LONG" ? t.target - t.stop : t.stop - t.target;
              const pos = t.side === "LONG" ? t.stop_now - t.stop : t.stop - t.stop_now;
              const at = span > 0 ? Math.min(1, Math.max(0, pos / span)) : 0;
              return (
                <span
                  className="absolute top-[-3px] h-[14px] w-0.5 bg-rose-400"
                  style={{ left: `${at * 100}%` }}
                  title="Cut-loss now"
                />
              );
            })()}
            <span
              className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground"
              style={{ left: `${progress * 100}%` }}
              title={open ? "Price now" : "Exit price"}
            />
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>Cut loss {inr(t.stop, 0)}</span>
            <span>
              {open
                ? `dot = price now · grey line = entry${t.stop_now != null && t.stop_now !== t.stop ? " · red line = cut-loss now" : ""}`
                : "dot = exit price"}
            </span>
            <span>Take profit {inr(t.target, 0)}</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-1">
          <h4 className="text-sm font-semibold">Why this stock was picked</h4>
          <ul className="list-disc pl-4 space-y-1">
            {why.lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
          {d?.score != null && (
            <p className="text-xs text-muted-foreground">
              Signal strength when opened: {Math.abs(d.score)} of 4 checks agreed.
            </p>
          )}
          {why.partial && (
            <p className="text-xs text-muted-foreground">
              This trade was opened before full reasons were saved, so only the four up/down checks are shown.
            </p>
          )}
        </div>
        <div className="space-y-2">
          <div>
            <h4 className="text-sm font-semibold">Past record at the time</h4>
            {d?.history ? (
              <p>
                {past.label}. This rule on {t.symbol} won {Math.round((d.history.win_rate ?? 0) * d.history.trades)} of{" "}
                {d.history.trades} times and {perHundred(d.history.avg_r)}.
              </p>
            ) : (
              <p className="text-muted-foreground">Not recorded for this trade.</p>
            )}
            {d?.side_history && (
              <p className="text-xs text-muted-foreground">
                Across all stocks, {t.side === "LONG" ? "buy" : "short"} ideas {perHundred(d.side_history.avg_r)}.
              </p>
            )}
          </div>
          {d?.cautions && d.cautions.filter((c) => c !== "no Kronos forecast").length > 0 && (
            <div>
              <h4 className="text-sm font-semibold">Warnings at the time</h4>
              <ul className="list-disc pl-4 text-amber-300">
                {d.cautions
                  .filter((c) => c !== "no Kronos forecast")
                  .map((c) => (
                    <li key={c}>{c}</li>
                  ))}
              </ul>
            </div>
          )}
          <div>
            <h4 className="text-sm font-semibold">{open ? "What happens next" : "How it ended"}</h4>
            {open ? (
              <p>
                It closes by itself
                {t.plan === "trail_run" ? "" : ` if the price reaches ${inr(t.target, 0)} (take profit)`}
                {t.plan === "trail_run" ? " if the price falls back to the cut-loss" : " or"}{" "}
                {inr(t.stop_now ?? t.stop, 0)} (cut loss{isTrailing(t.plan) ? ", which keeps following the price" : ""}),
                or after 20 trading days, roughly 4 weeks after opening.
              </p>
            ) : (
              <p>
                {EXIT_LABEL[t.exit_reason ?? ""] ?? "Closed"} on {t.exit_date}
                {t.bars_held != null && ` after ${t.bars_held} trading day${t.bars_held === 1 ? "" : "s"}`}.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Actions menu for an open trade: exit now, move the cut-loss, change exit style. */
function TradeActions({
  t,
  busy,
  onChangePlan,
  onMoveStop,
  onExit,
}: {
  t: PaperTrade;
  busy: boolean;
  onChangePlan: (id: string, plan: ExitPlan) => void;
  onMoveStop: (id: string, price: number) => void;
  onExit: (id: string) => void;
}) {
  const [menu, setMenu] = useState(false);
  const [mode, setMode] = useState<"list" | "stop" | "exit">("list");
  const [stopInput, setStopInput] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const long = t.side === "LONG";
  const stopNow = t.stop_now ?? t.stop;
  const price = t.current_price;

  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [menu]);

  const close = () => {
    setMenu(false);
    setMode("list");
  };
  const stopValue = Number(stopInput);
  const stopOk =
    price != null && Number.isFinite(stopValue) && (long ? stopValue > stopNow && stopValue < price : stopValue < stopNow && stopValue > price);
  const item = "w-full text-left px-3 min-h-9 text-xs hover:bg-muted disabled:opacity-50";

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={menu}
        aria-label={`Actions for ${t.symbol}`}
        disabled={busy}
        onClick={() => {
          setMenu(!menu);
          setMode("list");
          setStopInput(String(t.suggestion?.stop ?? ""));
        }}
        className="h-9 w-9 grid place-items-center rounded-md hover:bg-muted disabled:opacity-50"
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden />
      </button>
      {menu && (
        <div role="menu" className="absolute right-0 top-10 z-20 w-64 rounded-md border border-border bg-card py-1 shadow-lg">
          {mode === "list" && (
            <>
              <button
                role="menuitem"
                type="button"
                className={item}
                disabled={price == null}
                onClick={() => setMode("exit")}
              >
                Exit now{price != null ? ` at ${inr(price)}` : ""}
              </button>
              <button role="menuitem" type="button" className={item} onClick={() => setMode("stop")}>
                Move cut-loss…
              </button>
              <div className="my-1 border-t border-border" />
              <p className="px-3 pb-1 text-[11px] text-muted-foreground">Exit style</p>
              {EXIT_PLANS.map((p) => (
                <button
                  key={p.key}
                  role="menuitemradio"
                  aria-checked={(t.plan ?? "fixed") === p.key}
                  type="button"
                  className={cn(item, (t.plan ?? "fixed") === p.key && "font-semibold")}
                  onClick={() => {
                    if ((t.plan ?? "fixed") !== p.key) onChangePlan(t.id, p.key);
                    close();
                  }}
                >
                  {(t.plan ?? "fixed") === p.key ? "✓ " : ""}
                  {p.label}
                </button>
              ))}
            </>
          )}
          {mode === "stop" && (
            <form
              className="px-3 py-2 space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (stopOk) {
                  onMoveStop(t.id, stopValue);
                  close();
                }
              }}
            >
              <label className="block text-xs">
                <span className="text-muted-foreground">New cut-loss price</span>
                <input
                  autoFocus
                  inputMode="decimal"
                  value={stopInput}
                  onChange={(e) => setStopInput(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-border bg-background px-2 font-mono text-sm"
                />
              </label>
              <p className="text-[11px] text-muted-foreground">
                It can only move closer to the price: {long ? "above" : "below"} {inr(stopNow)} and {long ? "below" : "above"}{" "}
                {inr(price)} now. Applies from the next session.
              </p>
              <div className="flex gap-2">
                <button type="submit" disabled={!stopOk} className="min-h-9 px-3 rounded-md border border-emerald-600 bg-emerald-600/20 text-xs disabled:opacity-40">
                  Move it
                </button>
                <button type="button" onClick={() => setMode("list")} className="min-h-9 px-3 rounded-md text-xs hover:bg-muted">
                  Back
                </button>
              </div>
            </form>
          )}
          {mode === "exit" && (
            <div className="px-3 py-2 space-y-2">
              <p className="text-xs">
                Close {t.symbol} now at about {inr(price)}? Result so far {money(t.unrealized_inr)}.
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    onExit(t.id);
                    close();
                  }}
                  className="min-h-9 px-3 rounded-md border border-rose-600 bg-rose-600/20 text-xs"
                >
                  Yes, close it
                </button>
                <button type="button" onClick={() => setMode("list")} className="min-h-9 px-3 rounded-md text-xs hover:bg-muted">
                  Back
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Company at a glance: the shared analysis panel plus this trade's suggested exit. */
function Glance({ t }: { t: PaperTrade }) {
  const open = t.status === "OPEN";
  return (
    <div className="px-3 pb-4 pt-3 border-t border-border/60 bg-muted/20">
      <SymbolInsight
        symbol={t.symbol}
        name={t.decision?.name ?? t.symbol}
        industry={t.decision?.industry}
        side={t.side}
        entry={t.entry}
        stop={open ? (t.stop_now ?? t.stop) : t.stop}
        target={t.target}
        votes={t.votes}
        reasons={t.decision?.reasons}
        savedForecastPct={t.decision?.kronos_change_pct ?? null}
        isOpenTrade={open}
      >
        <div className="rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold">{open ? "Suggested exit" : "What we learned"}</h4>
          <p className="mt-1">
            {open
              ? (t.suggestion?.text ?? "No suggestion yet. Update prices first.")
              : (t.review?.lesson ?? "No review available for this trade.")}
          </p>
          {open && (
            <p className="mt-1 text-xs text-muted-foreground">
              A suggestion only. Nothing changes until you use the Actions menu.
            </p>
          )}
        </div>
      </SymbolInsight>
    </div>
  );
}

export function PracticeTrades({
  trades,
  busy,
  onUpdate,
  onChangePlan,
  onMoveStop,
  onExit,
  planBusy,
}: {
  trades: PaperTrade[];
  busy: boolean;
  onUpdate: () => void;
  onChangePlan: (id: string, plan: ExitPlan) => void;
  onMoveStop: (id: string, price: number) => void;
  onExit: (id: string) => void;
  planBusy: string | null;
}) {
  const [glanceId, setGlanceId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [filter, setFilter] = useState<Filter>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const openTrades = trades.filter((t) => t.status === "OPEN");
  const closedTrades = trades.filter((t) => t.status === "CLOSED");
  const sum = (xs: Array<number | null>) => xs.reduce<number>((a, b) => a + (b ?? 0), 0);
  const shown = filter === "open" ? openTrades : filter === "closed" ? closedTrades : trades;

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <section className="rounded-lg border border-border" aria-labelledby="trades-h">
      <div className="flex flex-wrap items-center justify-between gap-2 p-4">
        <button
          type="button"
          aria-expanded={!collapsed}
          onClick={() => {
            setCollapsed(!collapsed);
            writeCollapsed(!collapsed);
          }}
          className="flex items-center gap-2 text-left min-h-9"
        >
          <ChevronDown
            className={cn("h-4 w-4 transition-transform duration-150 ease-out motion-reduce:transition-none", collapsed && "-rotate-90")}
            aria-hidden
          />
          <h2 id="trades-h" className="text-base font-semibold">
            My practice trades
          </h2>
          <span className="text-xs text-muted-foreground">
            {openTrades.length} open · {closedTrades.length} finished
          </span>
        </button>
        <button
          type="button"
          onClick={onUpdate}
          disabled={busy}
          className="min-h-9 px-3 rounded-md border border-border text-xs hover:bg-muted disabled:opacity-50"
        >
          {busy ? "Checking…" : "Update prices and results"}
        </button>
      </div>

      {!collapsed && (
        <div className="px-4 pb-4 space-y-3">
          {trades.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No practice trades yet. Pick an idea below and press "Practice with fake money".
            </p>
          ) : (
            <>
              <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div className="rounded-md bg-muted/50 p-3">
                  <dt className="text-xs text-muted-foreground">Open trades so far</dt>
                  <dd className={cn("font-mono text-base", sum(openTrades.map(resultOf)) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    {money(sum(openTrades.map(resultOf)))}
                  </dd>
                </div>
                <div className="rounded-md bg-muted/50 p-3">
                  <dt className="text-xs text-muted-foreground">Finished trades</dt>
                  <dd className={cn("font-mono text-base", sum(closedTrades.map(resultOf)) >= 0 ? "text-emerald-400" : "text-rose-400")}>
                    {closedTrades.length ? money(sum(closedTrades.map(resultOf))) : "—"}
                  </dd>
                </div>
                <div className="rounded-md bg-muted/50 p-3">
                  <dt className="text-xs text-muted-foreground">Winning right now</dt>
                  <dd className="font-mono text-base">
                    {trades.filter((t) => (resultOf(t) ?? 0) > 0).length} of {trades.length}
                  </dd>
                </div>
                <div className="rounded-md bg-muted/50 p-3">
                  <dt className="text-xs text-muted-foreground">Money in open trades</dt>
                  <dd className="font-mono text-base">{inr(sum(openTrades.map((t) => t.qty * t.entry)), 0)}</dd>
                </div>
              </dl>

              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter trades">
                {(["all", "open", "closed"] as const).map((f) => (
                  <button
                    key={f}
                    type="button"
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                    className={cn(
                      "min-h-8 px-3 rounded-md border text-xs",
                      filter === f ? "bg-emerald-600/20 border-emerald-600" : "border-border text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {f === "all" ? `All (${trades.length})` : f === "open" ? `Open (${openTrades.length})` : `Finished (${closedTrades.length})`}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    setExpanded(expanded.size === shown.length ? new Set() : new Set(shown.map((t) => t.id)))
                  }
                  className="min-h-8 px-3 rounded-md text-xs text-muted-foreground hover:bg-muted ml-auto"
                >
                  {expanded.size === shown.length && shown.length > 0 ? "Hide all details" : "Show all details"}
                </button>
              </div>

              <ul className="divide-y divide-border rounded-md border border-border">
                {shown.map((t) => {
                  const r = resultOf(t);
                  const isOpen = expanded.has(t.id);
                  return (
                    <li key={t.id}>
                      <div className="flex items-stretch">
                        <button
                          type="button"
                          aria-expanded={glanceId === t.id}
                          aria-label={`${t.symbol} at a glance`}
                          onClick={() => setGlanceId(glanceId === t.id ? null : t.id)}
                          className="min-h-12 pl-3 pr-2 text-left w-36 sm:w-44 shrink-0 hover:bg-muted/40"
                        >
                          <span className="font-semibold underline decoration-dotted underline-offset-4">{t.symbol}</span>
                          {t.decision?.name && t.decision.name !== t.symbol && (
                            <span className="block text-xs text-muted-foreground truncate">{t.decision.name}</span>
                          )}
                        </button>
                        <button
                          type="button"
                          aria-expanded={isOpen}
                          onClick={() => toggle(t.id)}
                          className="flex-1 min-w-0 min-h-12 px-2 py-2 grid grid-cols-[1fr_auto] sm:grid-cols-[1fr_1.2fr_1fr_auto] items-center gap-x-3 gap-y-1 text-left hover:bg-muted/40"
                        >
                          <span className="text-sm hidden sm:block">
                            {t.side === "LONG" ? "Buy" : "Short"} × {t.qty}
                          </span>
                          <span className="font-mono text-sm hidden sm:block whitespace-nowrap">
                            {inr(t.entry)} → {inr(t.status === "OPEN" ? t.current_price : t.exit_price)}
                          </span>
                          <span className="text-xs text-muted-foreground hidden sm:block">
                            {t.status === "OPEN" ? "Open" : (EXIT_LABEL[t.exit_reason ?? ""] ?? "Closed")}
                            {t.status === "OPEN" && (t.plan !== "fixed" || (t.stop_now != null && t.stop_now !== t.stop)) && (
                              <span className="block text-[11px]">
                                {isTrailing(t.plan) ? "Trailing" : t.plan === "breakeven" ? "Break-even" : "Moved"} · cut-loss {inr(t.stop_now ?? t.stop, 0)}
                              </span>
                            )}
                          </span>
                          <span className="flex items-center gap-2 justify-end whitespace-nowrap">
                            <span className={cn("font-mono text-sm", (r ?? 0) > 0 && "text-emerald-400", (r ?? 0) < 0 && "text-rose-400")}>
                              {money(r)}
                            </span>
                            <ChevronDown
                              className={cn("h-4 w-4 text-muted-foreground transition-transform duration-150 ease-out motion-reduce:transition-none", isOpen && "rotate-180")}
                              aria-hidden
                            />
                          </span>
                        </button>
                        <div className="w-12 shrink-0 flex items-center justify-center">
                          {t.status === "OPEN" && (
                            <TradeActions
                              t={t}
                              busy={planBusy === t.id}
                              onChangePlan={onChangePlan}
                              onMoveStop={onMoveStop}
                              onExit={onExit}
                            />
                          )}
                        </div>
                      </div>
                      {glanceId === t.id && <Glance t={t} />}
                      {isOpen && <Detail t={t} onChangePlan={onChangePlan} planBusy={planBusy === t.id} />}
                    </li>
                  );
                })}
              </ul>
              <p className="text-xs text-muted-foreground">
                Open results are on paper and change with the price. A trade counts toward the 30-trade track record
                once it finishes.
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
