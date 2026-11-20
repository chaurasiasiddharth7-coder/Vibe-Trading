import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { FlaskConical, Lock, Plus, Trash2, X } from "lucide-react";
import { useSession } from "@/lib/session";
import { Chip, Empty, Note, Page, PageHeader, Tiles } from "@/components/shell/ui";
import { rupees, tone } from "@/lib/deskApi";
import {
  pct,
  strategiesApi,
  UNIVERSE_OPTIONS,
  waitForJob,
  type Catalogue,
  type Condition,
  type GateCheck,
  type Rules,
  type StrategyRow,
  type TestResult,
  type TrackStats,
} from "@/lib/strategiesApi";
import { cn } from "@/lib/utils";

const field = "w-full min-h-10 rounded-md border border-border bg-background px-3 text-sm";
const KIND_LABEL: Record<string, string> = { "built-in": "Built-in", lab: "Lab", research: "Research", "exit settings": "Exit settings" };
const errText = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

/** Every strategy across the desk profiles: its track record, a test on past prices, and the paper / live switches. */
export function Strategies() {
  const { prefs, me } = useSession();
  const isOwner = me ? me.is_owner || me.role === "owner" : true;
  const canEdit = me?.role !== "viewer";
  const [data, setData] = useState<Catalogue | null>(null);
  const [error, setError] = useState("");
  const [kind, setKind] = useState("all");
  const [who, setWho] = useState("all");
  const [open, setOpen] = useState<StrategyRow | null>(null);
  const [building, setBuilding] = useState<StrategyRow | "new" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState("");

  const load = useCallback(() => {
    strategiesApi
      .list()
      .then((r) => {
        setData(r.data);
        setError("");
      })
      .catch((e) => setError(errText(e, "Couldn't load strategies.")));
  }, []);
  useEffect(load, [load]);

  const rows = useMemo(() => (data?.strategies ?? []).filter((s) => !s.archived), [data]);
  const people = useMemo(() => [...new Map(rows.map((r) => [r.profile ?? "owner", r.profile_name])).entries()], [rows]);
  const shown = rows.filter((r) => (kind === "all" || r.kind === kind) && (who === "all" || (r.profile ?? "owner") === who));
  const tested = rows.filter((r) => r.last_test);
  const best = [...rows]
    .filter((r) => (r.paper_stats?.trades ?? 0) >= 5)
    .sort((a, b) => (b.paper_stats?.avg_pct ?? -1e9) - (a.paper_stats?.avg_pct ?? -1e9))[0];

  const togglePaper = async (r: StrategyRow) => {
    setBusy(r.id + r.profile);
    setFlash("");
    try {
      await strategiesApi.paper(r.id, !r.paper, isOwner ? r.profile : null);
      load();
    } catch (e) {
      setFlash(errText(e, "Couldn't change the switch."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Page>
      <PageHeader
        title="Strategies"
        sub={isOwner ? "Every strategy across all profiles. Test it on past prices, then paper-trade it with fake money." : "Your strategies. Test them on past prices, then paper-trade them with fake money."}
        right={
          canEdit && (
            <button type="button" onClick={() => setBuilding("new")} className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold inline-flex items-center gap-2">
              <Plus className="h-4 w-4" aria-hidden /> New strategy
            </button>
          )
        }
      />
      <Tiles
        chosen={prefs.tiles_by_page.strategies}
        tiles={[
          { id: "tracked", label: "Strategies tracked", value: String(rows.length) },
          { id: "paper", label: "Paper trading on", value: String(rows.filter((r) => r.paper).length) },
          { id: "gate", label: "Passed the backtest gate", value: `${tested.filter((r) => r.last_test?.gate?.passed).length} of ${tested.length}` },
          {
            id: "best",
            label: "Best paper record (5+ trades)",
            value: best ? `${best.name.slice(0, 18)} ${pct(best.paper_stats?.avg_pct)}` : "—",
            tone: tone(best?.paper_stats?.avg_pct),
          },
          { id: "live", label: "Live trading", value: "Locked" },
        ]}
      />
      <Note tone="warn">
        <Lock className="inline h-3.5 w-3.5 me-1 -mt-0.5" aria-hidden />
        {data?.live_locked_reason ?? "Live trading is locked: every trade is paper."}
      </Note>
      {error && <Note tone="bad">{error}</Note>}
      {flash && <Note tone="bad">{flash}</Note>}

      <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="Filters">
        {["all", "built-in", "lab", "research", "exit settings"].map((k) => (
          <Chip key={k} active={kind === k} onClick={() => setKind(k)}>
            {k === "all" ? "All" : KIND_LABEL[k]}
          </Chip>
        ))}
        {isOwner && people.length > 1 && (
          <select className="min-h-9 rounded-full border border-border bg-background px-3 text-sm" value={who} onChange={(e) => setWho(e.target.value)} aria-label="Profile">
            <option value="all">Everyone</option>
            {people.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        )}
      </div>

      {!data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : shown.length === 0 ? (
        <Empty>No strategies here yet. Press "New strategy" to build one, or import one you found online.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="text-start font-medium px-3 py-2">Strategy</th>
                <th className="text-end font-medium px-3 py-2">Paper record</th>
                <th className="text-end font-medium px-3 py-2">Avg / trade</th>
                <th className="text-start font-medium px-3 py-2">Backtest</th>
                <th className="text-center font-medium px-3 py-2">Paper</th>
                <th className="text-center font-medium px-3 py-2">Live</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={`${r.profile ?? "owner"}:${r.id}`} className="border-t border-border align-top">
                  <td className="px-3 py-2">
                    <button type="button" onClick={() => setOpen(r)} className="text-start hover:underline">
                      <span className="font-semibold">{r.name}</span>
                    </button>
                    <div className="text-xs text-muted-foreground">
                      {KIND_LABEL[r.kind] ?? r.kind}
                      {isOwner && ` · ${r.profile_name}`}
                      {r.rules_text?.[0] ? ` · ${r.rules_text[0].slice(0, 80)}${r.rules_text[0].length > 80 ? "…" : ""}` : ""}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-end font-mono tabular-nums whitespace-nowrap">
                    <TrackCell s={r.paper_stats} />
                  </td>
                  <td className={cn("px-3 py-2 text-end font-mono tabular-nums", tone(r.paper_stats?.avg_pct))}>{r.paper_stats?.trades ? pct(r.paper_stats.avg_pct) : "—"}</td>
                  <td className="px-3 py-2 text-xs">
                    {r.last_test ? (
                      <span className={r.last_test.gate?.passed ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-300"}>
                        {r.last_test.gate?.passed ? "Passed" : "Failed"} · <span className="font-mono">{pct(r.last_test.summary?.avg_pct)}</span> over{" "}
                        <span className="font-mono">{r.last_test.summary?.trades ?? 0}</span> trades
                      </span>
                    ) : r.kind === "lab" ? (
                      <span className="text-muted-foreground">Not tested yet</span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {r.paper === null ? (
                      <span className="text-xs text-muted-foreground">n/a</span>
                    ) : (
                      <Switch
                        on={!!r.paper}
                        label={`Paper trading for ${r.name}`}
                        disabled={!canEdit || busy === r.id + r.profile}
                        onChange={() => void togglePaper(r)}
                      />
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <button
                      type="button"
                      onClick={() => setOpen(r)}
                      className="min-h-9 px-2 rounded-md text-xs text-muted-foreground inline-flex items-center gap-1 hover:bg-muted"
                      aria-label={`Live trading for ${r.name} is locked. See what it needs.`}
                    >
                      <Lock className="h-3.5 w-3.5" aria-hidden /> Locked
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && (
        <StrategyPanel
          row={open}
          isOwner={isOwner}
          canEdit={canEdit}
          lockedReason={data?.live_locked_reason ?? ""}
          onClose={() => setOpen(null)}
          onChanged={load}
          onEdit={() => {
            setBuilding(open);
            setOpen(null);
          }}
        />
      )}
      {building && data && (
        <BuilderPanel
          existing={building === "new" ? null : building}
          catalogue={data}
          isOwner={isOwner}
          onClose={() => setBuilding(null)}
          onSaved={() => {
            setBuilding(null);
            load();
          }}
        />
      )}
    </Page>
  );
}

function TrackCell({ s }: { s?: TrackStats | null }) {
  if (!s || !s.trades) return <span className="text-muted-foreground">{s?.open ? `${s.open} open` : "No trades yet"}</span>;
  return (
    <span>
      {s.trades} · {s.win_rate}% won
      {s.pnl_inr !== undefined && s.pnl_inr !== 0 && <span className={cn("block text-xs font-mono", tone(s.pnl_inr))}>{rupees(s.pnl_inr, 0, true)}</span>}
    </span>
  );
}

function Switch({ on, onChange, label, disabled }: { on: boolean; onChange?: () => void; label: string; disabled?: boolean }) {
  return (
    <span
      role="switch"
      aria-checked={on}
      aria-label={label}
      aria-disabled={disabled}
      tabIndex={disabled ? -1 : 0}
      onClick={() => !disabled && onChange?.()}
      onKeyDown={(e) => {
        if (!disabled && (e.key === " " || e.key === "Enter")) {
          e.preventDefault();
          onChange?.();
        }
      }}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors motion-reduce:transition-none",
        on ? "bg-emerald-600 border-emerald-600" : "bg-muted border-border",
        disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer",
      )}
    >
      <span className={cn("inline-block h-4 w-4 rounded-full bg-background shadow transition-transform motion-reduce:transition-none", on ? "translate-x-6" : "translate-x-1")} />
    </span>
  );
}

function Panel({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" role="dialog" aria-modal="true" aria-label={title}>
      <div className={cn("w-full h-full overflow-y-auto bg-card border-s border-border p-5 space-y-4", wide ? "max-w-2xl" : "max-w-xl")}>
        <div className="flex justify-between items-center gap-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="h-9 w-9 grid place-items-center rounded hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Checklist({ items }: { items: GateCheck[] }) {
  return (
    <ul className="space-y-1 text-sm">
      {items.map((c) => (
        <li key={c.label} className="flex gap-2">
          <span aria-hidden className={c.ok ? "text-emerald-600" : "text-rose-600"}>
            {c.ok ? "✓" : "✗"}
          </span>
          <span>
            <span className="sr-only">{c.ok ? "Passed: " : "Not yet: "}</span>
            {c.label}
            {c.value !== undefined && c.value !== null && (
              <span className="font-mono text-xs text-muted-foreground"> ({Array.isArray(c.value) ? c.value.map((v) => String(v ?? "—")).join(" / ") : String(c.value)})</span>
            )}
            {c.manual && <span className="text-xs text-muted-foreground"> · you confirm this</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Sparkline({ points }: { points: Array<{ cum_pct: number }> }) {
  if (points.length < 2) return null;
  const ys = points.map((p) => p.cum_pct);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const W = 520;
  const H = 120;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / span) * H;
  const d = ys.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  return (
    <figure className="space-y-1">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28" role="img" aria-label={`Running total of trade results, ending at ${last.toFixed(1)}%`}>
        <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="stroke-border" strokeDasharray="4 4" />
        <path d={d} fill="none" strokeWidth={2} className={last >= 0 ? "stroke-emerald-600" : "stroke-rose-600"} />
      </svg>
      <figcaption className="text-xs text-muted-foreground">Running total of every trade's % result, oldest to newest (dashed line = zero).</figcaption>
    </figure>
  );
}

const ROBUST_LABEL: Record<string, string> = {
  first_half: "First half of trades",
  second_half: "Second half",
  double_costs: "At double costs",
  holdout_last_30pct: "Last 30% (hold-out)",
  random_entries: "Same exits, random entry days",
};

function ResultView({ r }: { r: TestResult }) {
  const s = r.summary;
  const trades = r.trades ?? r.trades_sample ?? [];
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[
          ["Trades", String(s.trades ?? 0)],
          ["Win rate", s.trades ? `${s.win_rate}%` : "—"],
          ["Avg per trade", pct(s.avg_pct)],
          ["Worst run (sum %)", pct(s.max_drawdown_pct, 1)],
        ].map(([k, v]) => (
          <div key={k} className="rounded-md bg-muted/50 p-2">
            <dt className="text-xs text-muted-foreground">{k}</dt>
            <dd className="font-mono tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="text-xs text-muted-foreground">
        {r.years ? `${r.years} years of daily prices` : "Daily prices"} · {r.symbols_tested ?? "?"} stocks ({r.symbols_with_trades ?? 0} traded) · costs {r.cost_pct ?? 0.25}% a trade · entry at
        the next open; if a day touched both the stop and the target, the stop is assumed first.
      </p>
      {r.equity && <Sparkline points={r.equity} />}
      {r.gate && (
        <section className="space-y-1">
          <h4 className={cn("text-sm font-semibold", r.gate.passed ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-300")}>
            Backtest gate: {r.gate.passed ? "passed" : "failed"}
          </h4>
          <Checklist items={r.gate.checks} />
        </section>
      )}
      {r.robustness && (
        <table className="w-full text-sm">
          <tbody>
            {Object.entries(r.robustness).map(([k, v]) => (
              <tr key={k} className="border-t border-border">
                <td className="py-1">{ROBUST_LABEL[k] ?? k}</td>
                <td className={cn("py-1 text-end font-mono tabular-nums", tone(v.avg_pct))}>{v.trades ? `${pct(v.avg_pct)} · ${v.trades} trades` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {trades.length > 0 && (
        <details>
          <summary className="cursor-pointer text-sm">Latest {Math.min(20, trades.length)} trades</summary>
          <ul className="mt-2 space-y-0.5 text-xs font-mono">
            {trades
              .slice(-20)
              .reverse()
              .map((t, i) => (
                <li key={`${t.symbol}${t.entry_date}${i}`} className="flex justify-between gap-2">
                  <span>
                    {t.symbol} {t.entry_date} → {t.exit_date} ({t.reason})
                  </span>
                  <span className={tone(t.net_pct)}>{pct(t.net_pct)}</span>
                </li>
              ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function useTestRunner() {
  const [running, setRunning] = useState(false);
  const [secs, setSecs] = useState(0);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState("");
  const stop = useRef({ stop: false });
  useEffect(() => () => void (stop.current.stop = true), []);
  const run = async (start: () => Promise<{ job_id: string }>) => {
    setRunning(true);
    setError("");
    setResult(null);
    setSecs(0);
    try {
      const { job_id } = await start();
      const job = await waitForJob(job_id, setSecs, stop.current);
      if (job.status === "failed") setError(job.error ?? "The test failed.");
      else setResult(job.result);
    } catch (e) {
      if (!stop.current.stop) setError(errText(e, "The test failed."));
    } finally {
      setRunning(false);
    }
  };
  return { running, secs, result, error, run };
}

function StrategyPanel({
  row,
  isOwner,
  canEdit,
  lockedReason,
  onClose,
  onChanged,
  onEdit,
}: {
  row: StrategyRow;
  isOwner: boolean;
  canEdit: boolean;
  lockedReason: string;
  onClose: () => void;
  onChanged: () => void;
  onEdit: () => void;
}) {
  const [years, setYears] = useState(3);
  const [tests, setTests] = useState<TestResult[]>([]);
  const [msg, setMsg] = useState("");
  const tr = useTestRunner();
  const profile = isOwner ? row.profile : null;
  const lab = row.kind === "lab";

  useEffect(() => {
    if (!lab) return;
    strategiesApi
      .detail(row.id, profile)
      .then((r) => setTests(r.data.tests))
      .catch(() => setTests([]));
  }, [row.id, profile, lab, tr.result]);

  const latest = tr.result ?? tests[tests.length - 1] ?? null;
  const scan = async () => {
    setMsg("");
    try {
      const r = await strategiesApi.scanNow(row.id, profile);
      setMsg(r.data.note ?? (r.data.opened.length ? `Opened practice trades: ${r.data.opened.map((o) => o.symbol).join(", ")}.` : "No stock meets every condition today."));
      onChanged();
    } catch (e) {
      setMsg(errText(e, "Scan failed."));
    }
  };
  const archive = async () => {
    try {
      await strategiesApi.edit(row.id, { archived: true }, profile);
      onChanged();
      onClose();
    } catch (e) {
      setMsg(errText(e, "Couldn't archive."));
    }
  };

  return (
    <Panel title={row.name} onClose={onClose} wide>
      <p className="text-xs text-muted-foreground">
        {KIND_LABEL[row.kind] ?? row.kind} · {row.profile_name}
        {row.author ? ` · made by ${row.author}` : ""}
        {row.source && (
          <>
            {" · "}
            {/^https?:\/\//.test(row.source) ? (
              <a href={row.source} target="_blank" rel="noopener noreferrer nofollow" className="underline">
                source
              </a>
            ) : (
              row.source
            )}
          </>
        )}
      </p>
      {row.rules_text && (
        <ul className="list-disc ps-5 text-sm space-y-0.5">
          {row.rules_text.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
      {row.notes && <p className="text-sm whitespace-pre-wrap text-muted-foreground">{row.notes}</p>}

      <section className="space-y-1">
        <h3 className="text-sm font-semibold">Paper record (fake money)</h3>
        {row.paper_stats?.trades ? (
          <p className="text-sm font-mono tabular-nums">
            {row.paper_stats.trades} finished · {row.paper_stats.win_rate}% won · {pct(row.paper_stats.avg_pct)} a trade ·{" "}
            <span className={tone(row.paper_stats.pnl_inr)}>{rupees(row.paper_stats.pnl_inr, 0, true)}</span>
            {row.paper_stats.open ? ` · ${row.paper_stats.open} open` : ""}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">No finished paper trades yet{row.paper_stats?.open ? ` (${row.paper_stats.open} open)` : ""}.</p>
        )}
        {row.paper && lab && <p className="text-xs text-muted-foreground">Checked after every close (15:45). It opens practice trades with the same sizing and exits as the rest of the desk.</p>}
        {row.last_scan && (
          <p className="text-xs text-muted-foreground">
            Last check {row.last_scan.at.replace("T", " ").slice(0, 16)}: {row.last_scan.opened} opened
          </p>
        )}
      </section>

      {lab && (
        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold me-auto">Test on past prices</h3>
            <select className="min-h-9 rounded-md border border-border bg-background px-2 text-sm" value={years} onChange={(e) => setYears(Number(e.target.value))} aria-label="Years of history">
              {[1, 2, 3, 5].map((y) => (
                <option key={y} value={y}>
                  {y} year{y > 1 ? "s" : ""}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={tr.running || !canEdit}
              onClick={() => void tr.run(() => strategiesApi.test(row.id, years, profile).then((r) => ({ job_id: r.job_id })))}
              className="min-h-9 px-3 rounded-md border border-border text-sm inline-flex items-center gap-1 disabled:opacity-60"
            >
              <FlaskConical className="h-4 w-4" aria-hidden /> {tr.running ? `Testing… ${tr.secs}s` : "Run test"}
            </button>
          </div>
          {tr.error && <Note tone="bad">{tr.error}</Note>}
          {latest ? <ResultView r={latest} /> : <p className="text-sm text-muted-foreground">Not tested yet. Paper trading unlocks after the first test.</p>}
        </section>
      )}

      <section className="space-y-1">
        <h3 className="text-sm font-semibold flex items-center gap-1">
          <Lock className="h-3.5 w-3.5" aria-hidden /> Live trading: locked
        </h3>
        <p className="text-xs text-muted-foreground">{lockedReason}</p>
        {row.gate.length > 0 && <Checklist items={row.gate} />}
      </section>

      {msg && <Note>{msg}</Note>}
      {lab && canEdit && (
        <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
          <button type="button" onClick={onEdit} className="min-h-10 px-4 rounded-md border border-border text-sm">
            Edit rules
          </button>
          {row.paper && (
            <button type="button" onClick={() => void scan()} className="min-h-10 px-4 rounded-md border border-border text-sm">
              Check today's prices now
            </button>
          )}
          <button type="button" onClick={() => void archive()} className="min-h-10 px-4 rounded-md border border-rose-700/40 text-rose-700 dark:text-rose-400 text-sm ms-auto">
            Archive
          </button>
        </div>
      )}
    </Panel>
  );
}

const EXIT_FIELDS: Array<{ key: keyof Rules["exit"]; label: string; group: string }> = [
  { key: "stop_pct", label: "Stop %", group: "stop" },
  { key: "stop_atr", label: "Stop × ATR", group: "stop" },
  { key: "target_pct", label: "Target %", group: "target" },
  { key: "target_atr", label: "Target × ATR", group: "target" },
  { key: "trail_atr", label: "Trailing stop × ATR (optional)", group: "trail" },
  { key: "max_hold_days", label: "Max days held", group: "time" },
];

function defaultsFor(cat: Catalogue, kind: string): Condition {
  const spec = cat.conditions[kind]?.params ?? {};
  return { kind, ...Object.fromEntries(Object.entries(spec).map(([k, [, , d]]) => [k, d])) };
}

function BuilderPanel({
  existing,
  catalogue,
  isOwner,
  onClose,
  onSaved,
}: {
  existing: StrategyRow | null;
  catalogue: Catalogue;
  isOwner: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const kinds = Object.keys(catalogue.conditions);
  const start: Rules = existing?.rules ?? { side: "LONG", entry: [defaultsFor(catalogue, "breakout_high")], exit: { stop_pct: 2, target_pct: 4, max_hold_days: 10 }, universe: "nifty50", symbols: null };
  const [name, setName] = useState(existing?.name ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [source, setSource] = useState(existing?.source ?? "");
  const [rules, setRules] = useState<Rules>(start);
  const [symbolsText, setSymbolsText] = useState((start.symbols ?? []).join(", "));
  const [importText, setImportText] = useState("");
  const [importUrl, setImportUrl] = useState("");
  const [importNote, setImportNote] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [years, setYears] = useState(3);
  const tr = useTestRunner();

  const finalRules = (): Rules => {
    const syms = symbolsText
      .split(/[\s,]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    return { ...rules, symbols: syms.length ? syms : null, universe: syms.length ? null : rules.universe ?? "nifty50" };
  };
  const setCond = (i: number, c: Condition) => setRules((r) => ({ ...r, entry: r.entry.map((x, j) => (j === i ? c : x)) }));
  const setExit = (key: keyof Rules["exit"], v: string) =>
    setRules((r) => {
      const next = { ...r.exit };
      if (v === "") delete next[key];
      else next[key] = Number(v);
      return { ...r, exit: next };
    });

  const doImport = async () => {
    setImporting(true);
    setImportNote([]);
    setError("");
    try {
      const r = await strategiesApi.importDraft({ text: importText, url: importUrl.trim() || undefined });
      const d = r.data;
      if (!name) setName(d.name);
      if (importUrl.trim()) setSource(importUrl.trim());
      if (d.rules) {
        setRules({ ...d.rules, universe: rules.universe, symbols: null });
      }
      setImportNote([
        d.rules ? "Rules drafted below. Check every line before you test or save." : `Couldn't use the AI's rules: ${d.problem}`,
        ...d.unsupported.map((u) => `Not covered by the rule builder: ${u}`),
      ]);
    } catch (e) {
      setImportNote([errText(e, "Import failed.")]);
    } finally {
      setImporting(false);
    }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (existing) {
        await strategiesApi.edit(existing.id, { name, notes, rules: finalRules() }, isOwner ? existing.profile : null);
      } else {
        await strategiesApi.create({ name, notes, rules: finalRules(), source_url: source || null });
      }
      onSaved();
    } catch (err) {
      setError(errText(err, "Couldn't save."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Panel title={existing ? `Edit ${existing.name}` : "New strategy"} onClose={onClose} wide>
      {!existing && isOwner && (
        <details className="rounded-md border border-border p-3" open={!!importText || !!importUrl}>
          <summary className="cursor-pointer text-sm font-semibold">Import one you found online</summary>
          <div className="mt-3 space-y-2">
            <input className={field} placeholder="Link to the article or post (optional)" value={importUrl} onChange={(e) => setImportUrl(e.target.value)} type="url" />
            <textarea className={cn(field, "min-h-24 py-2")} placeholder="Or paste the strategy's description" value={importText} onChange={(e) => setImportText(e.target.value)} maxLength={12000} />
            <button
              type="button"
              disabled={importing || (!importText.trim() && !importUrl.trim())}
              onClick={() => void doImport()}
              className="min-h-9 px-3 rounded-md border border-border text-sm disabled:opacity-60"
            >
              {importing ? "Reading…" : "Draft the rules"}
            </button>
            <p className="text-xs text-muted-foreground">The AI only fills in the form. Nothing is saved or traded until you check it and press Save.</p>
            {importNote.map((n) => (
              <p key={n} className="text-xs text-amber-700 dark:text-amber-300">
                {n}
              </p>
            ))}
          </div>
        </details>
      )}
      <form onSubmit={save} className="space-y-4">
        <label className="block text-sm space-y-1">
          <span>Name</span>
          <input className={field} value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} />
        </label>
        <fieldset className="text-sm space-y-1">
          <legend className="mb-1">Direction</legend>
          <div className="flex gap-2">
            {(["LONG", "SHORT"] as const).map((s) => (
              <Chip key={s} active={rules.side === s} onClick={() => setRules((r) => ({ ...r, side: s }))}>
                {s === "LONG" ? "Buy" : "Short"}
              </Chip>
            ))}
          </div>
        </fieldset>
        <fieldset className="space-y-2">
          <legend className="text-sm mb-1">Enter at the next open when every one of these is true at the close</legend>
          {rules.entry.map((c, i) => (
            <div key={i} className="flex flex-wrap items-end gap-2 rounded-md border border-border p-2">
              <label className="text-xs space-y-1 grow">
                <span>Condition {i + 1}</span>
                <select className={field} value={c.kind} onChange={(e) => setCond(i, defaultsFor(catalogue, e.target.value))}>
                  {kinds.map((k) => (
                    <option key={k} value={k}>
                      {catalogue.conditions[k].text.replace(/\{(\w+)\}/g, "…")}
                    </option>
                  ))}
                </select>
              </label>
              {Object.entries(catalogue.conditions[c.kind]?.params ?? {}).map(([p, [lo, hi]]) => (
                <label key={p} className="text-xs space-y-1 w-24">
                  <span>{p === "n" ? "Days" : p === "pct" ? "%" : p === "mult" ? "× average" : p}</span>
                  <input className={field} type="number" step="any" min={lo} max={hi} value={c[p]} onChange={(e) => setCond(i, { ...c, [p]: Number(e.target.value) })} required />
                </label>
              ))}
              {rules.entry.length > 1 && (
                <button type="button" aria-label={`Remove condition ${i + 1}`} onClick={() => setRules((r) => ({ ...r, entry: r.entry.filter((_, j) => j !== i) }))} className="h-10 w-10 grid place-items-center rounded hover:bg-muted">
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </div>
          ))}
          {rules.entry.length < 6 && (
            <button type="button" onClick={() => setRules((r) => ({ ...r, entry: [...r.entry, defaultsFor(catalogue, "above_sma")] }))} className="min-h-9 px-3 rounded-md border border-dashed border-border text-sm">
              Add a condition
            </button>
          )}
        </fieldset>
        <fieldset className="space-y-2">
          <legend className="text-sm mb-1">Exit (a stop is required; use % or ATR, not both)</legend>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {EXIT_FIELDS.map((f) => {
              const [lo, hi] = catalogue.exit_limits[f.key] ?? [0, 100];
              return (
                <label key={f.key} className="text-xs space-y-1">
                  <span>{f.label}</span>
                  <input className={field} type="number" step="any" min={lo} max={hi} value={rules.exit[f.key] ?? ""} onChange={(e) => setExit(f.key, e.target.value)} />
                </label>
              );
            })}
          </div>
        </fieldset>
        <fieldset className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <legend className="text-sm mb-1">Stocks to trade</legend>
          <label className="text-xs space-y-1">
            <span>List</span>
            <select className={field} value={rules.universe ?? "nifty50"} onChange={(e) => setRules((r) => ({ ...r, universe: e.target.value }))} disabled={!!symbolsText.trim()}>
              {UNIVERSE_OPTIONS.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.label}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs space-y-1">
            <span>Or these symbols (up to 100)</span>
            <input className={field} placeholder="RELIANCE, TCS, INFY" value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} />
          </label>
        </fieldset>
        <label className="block text-sm space-y-1">
          <span>Notes (optional)</span>
          <textarea className={cn(field, "min-h-16 py-2")} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} />
        </label>
        {!existing && (
          <label className="block text-sm space-y-1">
            <span>Where you found it (optional link)</span>
            <input className={field} type="url" value={source ?? ""} onChange={(e) => setSource(e.target.value)} />
          </label>
        )}

        <section className="rounded-md border border-border p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold me-auto">Try it before saving</h3>
            <select className="min-h-9 rounded-md border border-border bg-background px-2 text-sm" value={years} onChange={(e) => setYears(Number(e.target.value))} aria-label="Years of history">
              {[1, 2, 3, 5].map((y) => (
                <option key={y} value={y}>
                  {y} year{y > 1 ? "s" : ""}
                </option>
              ))}
            </select>
            <button type="button" disabled={tr.running} onClick={() => void tr.run(() => strategiesApi.tryRules(finalRules(), years))} className="min-h-9 px-3 rounded-md border border-border text-sm inline-flex items-center gap-1 disabled:opacity-60">
              <FlaskConical className="h-4 w-4" aria-hidden /> {tr.running ? `Testing… ${tr.secs}s` : "Test on past prices"}
            </button>
          </div>
          {tr.error && <Note tone="bad">{tr.error}</Note>}
          {tr.result && <ResultView r={tr.result} />}
        </section>

        {existing && <p className="text-xs text-muted-foreground">Changing the rules clears old test results and turns paper trading off until you test again.</p>}
        {error && (
          <p role="alert" className="text-sm text-rose-600">
            {error}
          </p>
        )}
        <button type="submit" disabled={saving} className="w-full min-h-11 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60">
          {saving ? "Saving…" : "Save strategy"}
        </button>
      </form>
    </Panel>
  );
}
