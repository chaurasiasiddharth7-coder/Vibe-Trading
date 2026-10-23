import type { BoardRow, HistoryStats } from "@/lib/api";
import { historyVerdict } from "@/components/desk/plain";
import type { Direction, SignalRow } from "@/components/desk/analysisView";

export type ScoreTone = "strong" | "good" | "okay" | "weak" | "avoid";

export interface TradeScore {
  /** 0 to 10, one decimal. */
  score: number;
  tone: ScoreTone;
  verdict: string;
  advice: string;
  positives: string[];
  risks: string[];
}

export interface ScoreInput {
  side: "LONG" | "SHORT";
  votes: BoardRow["votes"];
  rsi?: number | null;
  kronosPct?: number | null;
  cautions?: string[];
  stale?: boolean;
  /** This rule on this stock. */
  history?: HistoryStats | null;
  /** This rule across all stocks. */
  sideHistory?: HistoryStats | null;
}

const BANDS: Array<{ min: number; tone: ScoreTone; verdict: string; advice: string }> = [
  { min: 8, tone: "strong", verdict: "Strong", advice: "One of the better setups." },
  { min: 6.5, tone: "good", verdict: "Good", advice: "Worth a practice trade." },
  { min: 5, tone: "okay", verdict: "Okay", advice: "Marginal. Keep it small." },
  { min: 3, tone: "weak", verdict: "Weak", advice: "Better to skip." },
  { min: 0, tone: "avoid", verdict: "Avoid", advice: "Stay away." },
];

export const round1 = (n: number) => Math.round(n * 10) / 10;

export function bandFor(score: number) {
  return BANDS.find((b) => score >= b.min) ?? BANDS[BANDS.length - 1];
}

/**
 * Score a trade idea out of 10 from evidence the board already has:
 * up to 6 for how many of the four checks agree, 2 for this stock's past record,
 * 1 for the rule's record across all stocks, 1 for not being stretched.
 * Checks pointing the other way, warnings and stale prices take points off.
 */
export function scoreIdea(i: ScoreInput): TradeScore {
  const want = i.side === "LONG" ? 1 : -1;
  const votes = Object.values(i.votes);
  const agree = votes.filter((v) => Math.sign(v) === want).length;
  const against = votes.filter((v) => Math.sign(v) === -want).length;
  const past = historyVerdict(i.history);
  const cautions = (i.cautions ?? []).filter((c) => c !== "no Kronos forecast");
  const stretched = i.rsi != null && (i.side === "LONG" ? i.rsi >= 70 : i.rsi <= 30);

  let score = agree * 1.5 - against * 0.75;
  score += past.tone === "good" ? 2 : past.tone === "mixed" ? 1 : past.tone === "unknown" ? 0.75 : 0;
  score += (i.sideHistory?.avg_r ?? 0) > 0 ? 1 : 0;
  score += stretched ? 0 : 1;
  score -= Math.min(1.5, cautions.length * 0.5);
  if (i.stale) score -= 1.5;
  score = round1(Math.max(0, Math.min(10, score)));

  const positives: string[] = [];
  const risks: string[] = [];
  if (agree >= 3) positives.push(`${agree} of 4 checks agree`);
  if (past.tone === "good" && i.history)
    positives.push(`Won ${Math.round((i.history.win_rate ?? 0) * i.history.trades)} of ${i.history.trades} times on this stock before`);
  if (i.kronosPct != null && Math.sign(i.kronosPct) === want && Math.abs(i.kronosPct) >= 1.5)
    positives.push(`AI forecast agrees (${i.kronosPct >= 0 ? "+" : "−"}${Math.abs(i.kronosPct).toFixed(1)}%)`);
  if ((i.sideHistory?.avg_r ?? 0) > 0) positives.push("This kind of idea has worked across stocks");

  if (agree < 3) risks.push(`Only ${agree} of 4 checks agree`);
  if (against > 0) risks.push(`${against} check${against === 1 ? "" : "s"} point the other way`);
  if (stretched) risks.push(i.side === "LONG" ? "Already risen a lot, so buying now is chasing" : "Already fallen a lot, so shorting now is late");
  if (past.tone === "bad") risks.push("Lost money on this stock before");
  if (past.tone === "unknown") risks.push("Too few past examples to trust");
  if (i.stale) risks.push("Price may be out of date");
  for (const c of cautions) risks.push(c.charAt(0).toUpperCase() + c.slice(1));

  const b = bandFor(score);
  return { score, tone: b.tone, verdict: b.verdict, advice: b.advice, positives: positives.slice(0, 2), risks: risks.slice(0, 3) };
}

/** How well today's chart signals line up with a trade's direction, out of 10. Null when there is nothing to judge. */
export function nowScore(rows: SignalRow[], side: "LONG" | "SHORT"): { score: number; tone: ScoreTone; verdict: string } | null {
  const counted = rows.filter((r) => r.counts);
  if (counted.length < 3) return null;
  const want: Direction = side === "LONG" ? "bull" : "bear";
  const wrong: Direction = side === "LONG" ? "bear" : "bull";
  const good = counted.filter((r) => r.direction === want).length;
  const bad = counted.filter((r) => r.direction === wrong).length;
  const score = round1(Math.max(0, Math.min(10, ((good - bad) / counted.length + 1) * 5)));
  const b = bandFor(score);
  return { score, tone: b.tone, verdict: b.verdict };
}
