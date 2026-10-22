import type { BoardRow, SignalBoard as Board } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  SIDE_LABEL,
  closestToBuy,
  historyVerdict,
  inr,
  perHundred,
  trustSentence,
  whatWeSee,
} from "@/components/desk/plain";

const VOTE_KEYS = [
  ["trend", "Trend"],
  ["structure", "Averages"],
  ["momentum", "Pressure"],
  ["forecast", "AI"],
] as const;

export function VoteStrip({ votes }: { votes: BoardRow["votes"] }) {
  return (
    <span className="inline-flex shrink-0 flex-wrap gap-0.5 text-[10px]">
      {VOTE_KEYS.map(([key, label]) => {
        const v = votes[key];
        return (
          <span
            key={key}
            title={`${label}: ${v > 0 ? "points up" : v < 0 ? "points down" : "no opinion"}`}
            className={cn(
              "px-1 rounded border border-border",
              v > 0 && "text-emerald-400",
              v < 0 && "text-rose-400",
              v === 0 && "text-muted-foreground",
            )}
          >
            {label} {v > 0 ? "↑" : v < 0 ? "↓" : "–"}
          </span>
        );
      })}
    </span>
  );
}

export function TrustStrip({ board }: { board: Board }) {
  const tr = board.track_record;
  const h = board.history;
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-xs space-y-1",
        tr.verdict === "positive" ? "border-border" : "border-amber-700/50 bg-amber-950/20 text-amber-100",
      )}
    >
      <p>
        <span className="font-semibold">Can you trust this yet?</span> {trustSentence(tr)}
      </p>
      {h && (
        <p>
          <span className="font-semibold">Past {h.period.replace("y", " years")}:</span> buy ideas{" "}
          {perHundred(h.long.avg_r)} ({h.long.trades} trades). Short ideas {perHundred(h.short.avg_r)} (
          {h.short.trades} trades).
        </p>
      )}
      <p className="text-muted-foreground">
        Prices as of {board.as_of ?? "—"}
        {(board.stale?.length ?? 0) > 0 && ` · ${board.stale.length} names may be out of date`}
        {!board.kronos_live && " · AI forecast is off, so only 3 price checks are counted"}
      </p>
    </div>
  );
}

function Row({
  row,
  selected,
  onSelect,
}: {
  row: BoardRow;
  selected: boolean;
  onSelect: (s: string) => void;
}) {
  const past = historyVerdict(row.history?.symbol_side);
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(row.symbol)}
        className={cn(
          "w-full min-h-11 text-left px-3 py-2 rounded-md transition-colors duration-150 ease-out motion-reduce:transition-none",
          selected ? "bg-muted" : "hover:bg-muted/50",
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="text-sm font-semibold">{row.symbol}</span>
          <span className="font-mono text-xs text-muted-foreground">{inr(row.price)}</span>
        </div>
        {row.entry != null ? (
          <div className="text-xs mt-1">
            Exit at <span className="font-mono text-emerald-400">{inr(row.target, 0)}</span> if right,{" "}
            <span className="font-mono text-rose-400">{inr(row.stop, 0)}</span> if wrong
          </div>
        ) : (
          <div className="text-xs mt-1 text-muted-foreground">{whatWeSee(row)[0]}</div>
        )}
        {(row.side === "LONG" || row.side === "SHORT") && (
          <div
            className={cn(
              "text-[11px] mt-0.5",
              past.tone === "good" && "text-emerald-300",
              past.tone === "bad" && "text-rose-300",
              (past.tone === "mixed" || past.tone === "unknown") && "text-muted-foreground",
            )}
          >
            {past.label}
          </div>
        )}
        {row.stale && <div className="text-[11px] text-amber-300 mt-0.5">Price may be out of date</div>}
      </button>
    </li>
  );
}

function Column({
  title,
  hint,
  tone,
  rows,
  selected,
  onSelect,
  empty,
}: {
  title: string;
  hint: string;
  tone: "long" | "short" | "avoid";
  rows: BoardRow[];
  selected: string;
  onSelect: (s: string) => void;
  empty: string;
}) {
  return (
    <section className="rounded-lg border border-border p-3 space-y-2">
      <header>
        <h3
          className={cn(
            "text-sm font-semibold",
            tone === "long" && "text-emerald-400",
            tone === "short" && "text-rose-400",
            tone === "avoid" && "text-amber-400",
          )}
        >
          {title} <span className="font-mono text-muted-foreground">({rows.length})</span>
        </h3>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      </header>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground px-3 py-2">{empty}</p>
      ) : (
        <ul className="space-y-0.5 max-h-[28rem] overflow-y-auto overflow-x-hidden">
          {rows.map((r) => (
            <Row key={r.symbol} row={r} selected={r.symbol === selected} onSelect={onSelect} />
          ))}
        </ul>
      )}
    </section>
  );
}

export function BoardColumns({
  board,
  selected,
  onSelect,
}: {
  board: Board;
  selected: string;
  onSelect: (s: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Column
          title="Buy ideas"
          hint="Most checks point up, and the price hasn't already shot up."
          tone="long"
          rows={board.long}
          selected={selected}
          onSelect={onSelect}
          empty={
            closestToBuy(board.rows ?? []).length > 0
              ? `Nothing passes 3 of 4 checks today. Closest: ${closestToBuy(board.rows ?? [])
                  .map((r) => r.symbol)
                  .join(", ")}. Waiting is fine.`
              : "Nothing looks good to buy today. Waiting is fine."
          }
        />
        <Column
          title="Short ideas"
          hint="You profit if the price falls. Most checks point down. Same day only, unless you trade futures."
          tone="short"
          rows={board.short}
          selected={selected}
          onSelect={onSelect}
          empty="Nothing looks good to short today."
        />
        <Column
          title="Stay away"
          hint="Weak and falling. Don't buy these right now."
          tone="avoid"
          rows={board.steer_clear}
          selected={selected}
          onSelect={onSelect}
          empty="Nothing flagged."
        />
      </div>
      {board.no_edge.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          No clear signal, skip for now: {board.no_edge.join(", ")}
        </p>
      )}
      {board.failed.length > 0 && (
        <p className="text-[11px] text-rose-300">
          Couldn't load: {board.failed.map((f) => f.symbol).join(", ")}
        </p>
      )}
    </div>
  );
}

export function EvidencePanel({ row }: { row: BoardRow }) {
  const past = historyVerdict(row.history?.symbol_side);
  const hs = row.history?.symbol_side;
  return (
    <div className="rounded-lg border border-border p-4 space-y-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          className={cn(
            "font-semibold",
            row.side === "LONG" && "text-emerald-400",
            row.side === "SHORT" && "text-rose-400",
            row.side === "STEER_CLEAR" && "text-amber-400",
          )}
        >
          {SIDE_LABEL[row.side]}
        </span>
        <VoteStrip votes={row.votes} />
      </div>
      {row.entry != null && (
        <dl className="grid grid-cols-3 gap-2 text-xs">
          <div>
            <dt className="text-muted-foreground">{row.side === "SHORT" ? "Short at" : "Buy at"}</dt>
            <dd className="font-mono">{inr(row.entry)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Take profit</dt>
            <dd className="font-mono text-emerald-400">{inr(row.target)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Cut loss</dt>
            <dd className="font-mono text-rose-400">{inr(row.stop)}</dd>
          </div>
        </dl>
      )}
      <ul className="text-xs space-y-1 list-disc pl-4">
        {whatWeSee(row).map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      {hs && (
        <p className="text-xs">
          <span className="font-semibold">{past.label}.</span> This rule on {row.symbol} won{" "}
          {Math.round((hs.win_rate ?? 0) * hs.trades)} of {hs.trades} times and {perHundred(hs.avg_r)}.
        </p>
      )}
      {row.stale && (
        <p className="text-xs text-amber-300">
          Last price is from {row.as_of}, so it may be out of date. Check the live price first.
        </p>
      )}
      {row.short_note && (
        <p className="text-xs text-muted-foreground">
          A short in the normal cash market must be closed the same day. Holding it overnight needs futures or options.
        </p>
      )}
    </div>
  );
}
