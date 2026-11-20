import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDownRight, ArrowUpRight, ExternalLink, Newspaper, Sparkles, Target } from "lucide-react";
import { cn } from "@/lib/utils";
import { StockLink } from "@/components/desk/StockSheet";
import { briefApi, fmtPrice, type Brief, type BriefNews, type Mover } from "@/lib/watchlistApi";

const pct = (p: number | null | undefined) => (p == null ? "" : `${p > 0 ? "+" : ""}${p.toFixed(2)}%`);
const toneOf = (p: number | null | undefined) => (p == null || p === 0 ? "text-muted-foreground" : p > 0 ? "text-success" : "text-danger");

function ago(ts: number | null): string {
  if (!ts) return "";
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (m < 60) return `${m || 1}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

/** The "Today" tab: daily insight, market pulse, top picks and the news worth knowing. */
export function TodayBrief() {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState<string | null>(null);
  const liveRef = useRef(false);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = async () => {
      if (document.visibilityState === "visible") {
        try {
          const b = await briefApi.get();
          if (!alive) return;
          liveRef.current = b.live;
          setBrief(b);
          setError(null);
        } catch (e) {
          if (alive) setError(e instanceof Error ? e.message : "Couldn't load today's brief");
        }
      }
      // Refresh every minute while live, every 5 minutes otherwise (news is cached for 10 on the server).
      if (alive) timer = window.setTimeout(load, liveRef.current ? 60_000 : 300_000);
    };
    void load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  if (error && !brief) return <p role="alert" className="px-4 py-6 text-sm text-danger">{error}</p>;
  if (!brief) return <BriefSkeleton />;

  const updated = new Date(brief.generated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="space-y-4 px-3 pt-3">
      <section aria-labelledby="insight" className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" aria-hidden />
          <h2 id="insight" className="font-semibold">Daily insight</h2>
          <span className="ms-auto text-[11px] text-muted-foreground">
            {brief.status.label} · {updated}
            {brief.insight.by === "ai" ? " · AI summary" : ""}
          </span>
        </div>
        <ul className="mt-3 space-y-2">
          {brief.insight.bullets.map((b, i) => (
            <li key={i} className="flex gap-2 text-[14px] leading-snug">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      </section>

      {brief.breadth.advancers != null && <Pulse b={brief.breadth} />}

      <Picks picks={brief.picks} />

      <News items={brief.news} />

      <p className="px-1 pb-2 text-[11px] text-muted-foreground">{brief.disclaimer}</p>
    </div>
  );
}

function Pulse({ b }: { b: Brief["breadth"] }) {
  const adv = b.advancers ?? 0;
  const dec = b.decliners ?? 0;
  const unch = b.unchanged ?? 0;
  const total = Math.max(1, adv + dec + unch);
  return (
    <section aria-labelledby="pulse" className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-baseline gap-2">
        <h2 id="pulse" className="font-semibold">Market pulse</h2>
        <span className="ms-auto text-[11px] text-muted-foreground">
          {b.universe} · {b.source === "kite" ? `live ${b.as_of ?? ""}` : `close ${b.as_of ?? ""}`}
        </span>
      </div>
      <div>
        <div className="flex h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${adv} up, ${dec} down, ${unch} unchanged`}>
          <div className="bg-success" style={{ width: `${(adv / total) * 100}%` }} />
          <div className="bg-muted-foreground/30" style={{ width: `${(unch / total) * 100}%` }} />
          <div className="bg-danger" style={{ width: `${(dec / total) * 100}%` }} />
        </div>
        <div className="mt-1 flex justify-between text-xs tabular-nums">
          <span className="text-success">{adv} up</span>
          <span className="text-danger">{dec} down</span>
        </div>
      </div>
      {(b.sectors_up?.length || b.sectors_down?.length) ? (
        <div className="flex flex-wrap gap-1.5">
          {[...(b.sectors_up ?? []), ...(b.sectors_down ?? [])]
            .filter((s, i, all) => all.findIndex((x) => x.sector === s.sector) === i)
            .map((s) => (
              <span key={s.sector} className={cn("rounded-full border px-2 py-0.5 text-xs", s.change_pct >= 0 ? "border-success/40 text-success" : "border-danger/40 text-danger")}>
                {s.sector} {pct(s.change_pct)}
              </span>
            ))}
        </div>
      ) : null}
      <div className="grid grid-cols-2 gap-4">
        <MoverList title="Top gainers" icon={ArrowUpRight} rows={b.gainers ?? []} />
        <MoverList title="Top losers" icon={ArrowDownRight} rows={b.losers ?? []} />
      </div>
    </section>
  );
}

function MoverList({ title, icon: Icon, rows }: { title: string; icon: typeof ArrowUpRight; rows: Mover[] }) {
  return (
    <div className="min-w-0">
      <p className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <Icon className="h-3.5 w-3.5" aria-hidden /> {title}
      </p>
      <ul className="space-y-1">
        {rows.map((m) => (
          <li key={m.symbol} className="flex items-baseline justify-between gap-2 text-[13px]">
            <StockLink symbol={m.symbol} className="truncate no-underline" />
            <span className={cn("tabular-nums text-xs", toneOf(m.change_pct))}>{pct(m.change_pct)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Picks({ picks }: { picks: Brief["picks"] }) {
  const when = picks.scanned_at ? new Date(picks.scanned_at) : null;
  const old = when ? Date.now() - when.getTime() > 20 * 3600 * 1000 : true;
  return (
    <section aria-labelledby="picks" className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <Target className="h-4 w-4 text-primary" aria-hidden />
        <h2 id="picks" className="font-semibold">Top picks for the day</h2>
        <Link to="/dashboard" className="ms-auto text-xs text-primary">All ideas</Link>
      </div>
      {picks.rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-4 text-sm text-muted-foreground">
          No buy ideas yet. <Link to="/dashboard" className="text-primary">Run today's scan</Link> on Ideas and the strongest ones show up here.
        </div>
      ) : (
        <>
          <p className="px-1 text-[11px] text-muted-foreground">
            From your {picks.universe ?? ""} scan{when ? `, ${when.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` : ""}
            {old ? " (older than today: re-run for fresh picks)" : ""} · strongest signal first
          </p>
          <div className="-mx-3 flex gap-3 overflow-x-auto no-scrollbar px-3 pb-1 snap-x">
            {picks.rows.map((p) => (
              <article key={p.symbol} className="snap-start shrink-0 w-64 rounded-xl border border-border bg-card p-3 space-y-2">
                <div className="flex items-baseline justify-between gap-2">
                  <StockLink symbol={p.symbol} className="text-[15px] font-semibold" />
                  <span className="rounded bg-success/15 px-1.5 py-0.5 text-[11px] font-semibold text-success">Score {p.score ?? "—"}</span>
                </div>
                <p className="tabular-nums text-sm">
                  ₹{fmtPrice(p.price)} <span className={cn("text-xs", toneOf(p.change_percent))}>{pct(p.change_percent)}</span>
                </p>
                <dl className="grid grid-cols-3 gap-1 text-[11px]">
                  <div><dt className="text-muted-foreground">Buy near</dt><dd className="tabular-nums">{fmtPrice(p.entry)}</dd></div>
                  <div><dt className="text-muted-foreground">Stop</dt><dd className="tabular-nums text-danger">{fmtPrice(p.stop)}</dd></div>
                  <div><dt className="text-muted-foreground">Target</dt><dd className="tabular-nums text-success">{fmtPrice(p.target)}</dd></div>
                </dl>
                {p.reasons.length > 0 && (
                  <ul className="space-y-0.5 text-[12px] text-muted-foreground">
                    {p.reasons.map((r) => (
                      <li key={r} className="line-clamp-1">· {r}</li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function News({ items }: { items: BriefNews[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, 6);
  return (
    <section aria-labelledby="news" className="rounded-xl border border-border bg-card">
      <div className="flex items-center gap-2 px-4 pt-4">
        <Newspaper className="h-4 w-4 text-primary" aria-hidden />
        <h2 id="news" className="font-semibold">News to know</h2>
        <span className="ms-auto text-[11px] text-muted-foreground">Ranked by market impact</span>
      </div>
      {items.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted-foreground">No headlines right now. They refresh every few minutes.</p>
      ) : (
        <ul className="mt-2 divide-y divide-border">
          {shown.map((n) => (
            <li key={n.url}>
              <a href={n.url} target="_blank" rel="noopener noreferrer" className="flex gap-3 px-4 py-3 hover:bg-muted/50">
                <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.important ? "bg-primary" : "bg-muted-foreground/30")} aria-label={n.important ? "Important" : undefined} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] leading-snug">{n.title}</span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                    <span>{n.sources.join(", ")}</span>
                    {n.ts ? <span>· {ago(n.ts)}</span> : null}
                    {n.watchlist.map((s) => (
                      <span key={s} className="rounded bg-primary/15 px-1.5 py-0.5 font-medium text-primary">On your watchlist: {s}</span>
                    ))}
                  </span>
                </span>
                <ExternalLink className="mt-1 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              </a>
            </li>
          ))}
        </ul>
      )}
      {items.length > 6 && (
        <button type="button" onClick={() => setAll((a) => !a)} className="w-full min-h-11 border-t border-border text-sm text-primary">
          {all ? "Show fewer" : `Show ${items.length - 6} more`}
        </button>
      )}
    </section>
  );
}

function BriefSkeleton() {
  return (
    <div className="space-y-4 px-3 pt-3" aria-busy="true" aria-label="Loading today's brief">
      {[120, 160, 140, 220].map((h, i) => (
        <div key={i} className="animate-pulse rounded-xl bg-muted motion-reduce:animate-none" style={{ height: h }} />
      ))}
    </div>
  );
}
