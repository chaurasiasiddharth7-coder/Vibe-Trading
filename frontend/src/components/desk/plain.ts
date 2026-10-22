import type { BoardRow, BoardSide, ExitPlan, HistoryStats, TrackRecord } from "@/lib/api";

export const inr = (n: number | null | undefined, digits = 2) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: digits })}`;

export const SIDE_LABEL: Record<BoardSide, string> = {
  LONG: "Buy idea",
  SHORT: "Short idea",
  STEER_CLEAR: "Stay away",
  NO_EDGE: "No clear signal",
};

/** "₹11 gained for every ₹100 risked" from an average R multiple. */
export function perHundred(avgR: number | null | undefined): string {
  if (avgR == null) return "no result yet";
  const amt = Math.abs(Math.round(avgR * 100));
  if (amt === 0) return "about break-even";
  return avgR > 0
    ? `made about ₹${amt} for every ₹100 risked`
    : `lost about ₹${amt} for every ₹100 risked`;
}

export function pct(n: number | null | undefined): string {
  return n == null ? "—" : `${Math.round(n * 100)}%`;
}

export type Verdict = { label: string; tone: "good" | "mixed" | "bad" | "unknown" };

export function historyVerdict(h: HistoryStats | null | undefined): Verdict {
  if (!h || h.trades < 5 || h.avg_r == null) return { label: "Too few past examples", tone: "unknown" };
  if (h.avg_r >= 0.1) return { label: "Worked in the past", tone: "good" };
  if (h.avg_r > -0.05) return { label: "Roughly break-even in the past", tone: "mixed" };
  return { label: "Lost money in the past", tone: "bad" };
}

export function trustSentence(tr: TrackRecord): string {
  if (tr.verdict === "untested")
    return "Not proven yet. No practice trades have finished, so treat every idea as a guess to test.";
  if (tr.verdict === "too_few_trades")
    return `Still testing: ${tr.closed_trades} of 30 practice trades finished. Too early to trust.`;
  if (tr.verdict === "positive")
    return `${tr.closed_trades} practice trades finished and they ${perHundred(tr.expectancy_r)}.`;
  return `${tr.closed_trades} practice trades finished and they ${perHundred(tr.expectancy_r)}. Don't use real money on this.`;
}

/** Plain sentences for what each vote means. */
export type Evidence = Pick<BoardRow, "votes" | "kronos_change_pct" | "rsi" | "score">;

export function whatWeSee(row: Evidence): string[] {
  const out: string[] = [];
  const v = row.votes;
  out.push(
    v.trend > 0
      ? "The price trend is pointing up."
      : v.trend < 0
        ? "The price trend is pointing down."
        : "The price trend is flat.",
  );
  out.push(
    v.structure > 0
      ? "It trades above its 50-day and 200-day average prices, which is a healthy sign."
      : v.structure < 0
        ? "It trades below its 50-day and 200-day average prices, which is a weak sign."
        : "It's mixed against its long-term average prices.",
  );
  out.push(
    v.momentum > 0
      ? "Buying pressure has been picking up lately."
      : v.momentum < 0
        ? "Selling pressure has been picking up lately."
        : "Buying and selling pressure are balanced.",
  );
  const k = row.kronos_change_pct;
  out.push(
    k == null
      ? Math.abs(row.score) < 2
        ? "The AI forecast was skipped because the price checks don't lean clearly either way."
        : "The AI forecast isn't available for this one."
      : k >= 1.5
        ? `The AI forecast expects a rise of about ${k.toFixed(1)}% over the next 3 weeks.`
        : k <= -1.5
          ? `The AI forecast expects a fall of about ${Math.abs(k).toFixed(1)}% over the next 3 weeks.`
          : "The AI forecast expects little change over the next 3 weeks.",
  );
  if (row.rsi != null && row.rsi >= 70)
    out.push("It has already risen a lot recently, so buying now would be chasing.");
  if (row.rsi != null && row.rsi <= 30)
    out.push("It has already fallen a lot recently, so shorting now would be late.");
  return out;
}

export type TradePlanNumbers = {
  qty: number;
  invest: number;
  lossIfWrong: number;
  gainIfRight: number;
};

/** Size so that hitting the stop loses about ``riskInr``. */
export function planFor(row: BoardRow, riskInr: number): TradePlanNumbers | null {
  if (row.entry == null || row.stop == null || row.target == null) return null;
  const perShare = Math.abs(row.entry - row.stop);
  if (perShare <= 0) return null;
  const qty = Math.max(1, Math.floor(riskInr / perShare));
  return {
    qty,
    invest: qty * row.entry,
    lossIfWrong: qty * perShare,
    gainIfRight: qty * Math.abs(row.target - row.entry),
  };
}

const VOTE_NAMES: Record<keyof BoardRow["votes"], string> = {
  trend: "price trend",
  structure: "long-term averages",
  momentum: "buying pressure",
  forecast: "AI forecast",
};

/** Names nearest to a buy signal (most up-votes), for days with no buy ideas. */
export function closestToBuy(rows: BoardRow[], limit = 3): BoardRow[] {
  return rows
    .filter((r) => r.side !== "LONG" && r.score > 0)
    .sort((a, b) => b.score - a.score || (b.kronos_change_pct ?? 0) - (a.kronos_change_pct ?? 0))
    .slice(0, limit);
}

/** Plain sentence of what still has to turn up for a buy signal. */
export function missingForBuy(row: BoardRow): string {
  const keys = Object.keys(row.votes) as Array<keyof BoardRow["votes"]>;
  const ups = keys.filter((k) => row.votes[k] > 0).length;
  const downs = keys.filter((k) => row.votes[k] < 0).map((k) => VOTE_NAMES[k]);
  const flat = keys.filter((k) => row.votes[k] === 0).map((k) => VOTE_NAMES[k]);
  if (row.score >= 3 && row.rsi != null && row.rsi >= 70)
    return "Enough checks point up, but it has already risen a lot. Wait for a dip.";
  const parts = [`${ups} of 4 checks point up`];
  if (downs.length) parts.push(`${downs.join(" and ")} ${downs.length > 1 ? "point" : "points"} down, which cancels one out`);
  if (flat.length) parts.push(`${flat.join(" and ")} ${flat.length > 1 ? "have" : "has"} no opinion yet`);
  return `${parts.join("; ")}.`;
}

export const EXIT_PLANS: Array<{ key: ExitPlan; label: string; explain: string }> = [
  {
    key: "fixed",
    label: "Fixed cut-loss and take-profit",
    explain: "Both prices stay where they were set when you entered.",
  },
  {
    key: "breakeven",
    label: "Protect at break-even",
    explain:
      "Once the trade is up by as much as you risked, the cut-loss moves to your entry price, so it can't turn into a loss.",
  },
  {
    key: "trail",
    label: "Trailing cut-loss",
    explain:
      "The cut-loss follows the best price so far, staying 1.5× the stock's usual daily move behind it. It never moves back. Take-profit stays.",
  },
  {
    key: "trail_run",
    label: "Trailing, let winners run",
    explain:
      "No take-profit. The cut-loss follows 2× the usual daily move behind the best price, and you exit when it's hit (or after 20 trading days).",
  },
];

export const planLabel = (p: ExitPlan | undefined) =>
  EXIT_PLANS.find((x) => x.key === (p ?? "fixed"))?.label ?? "Fixed cut-loss and take-profit";

export const isTrailing = (p: ExitPlan | undefined) => p === "trail" || p === "trail_run";
