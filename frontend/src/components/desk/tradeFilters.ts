/** Filtering and date-wise grouping of practice trades by the day they were invested (opened), India time. */

const IST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });

/** The IST calendar date (YYYY-MM-DD) a trade was opened; "" when the timestamp cannot be read. */
export function investedDate(t: { opened_at: string }): string {
  const d = new Date(t.opened_at);
  return Number.isNaN(d.getTime()) ? "" : IST.format(d);
}

/** True when the invested date is inside the inclusive range; an empty bound is open-ended. A trade
 *  with an unreadable date only passes when no range is set. */
export function inInvestedRange(t: { opened_at: string }, from: string, to: string): boolean {
  if (!from && !to) return true;
  const d = investedDate(t);
  if (!d) return false;
  return (!from || d >= from) && (!to || d <= to);
}

export const backwards = (from: string, to: string): boolean => !!from && !!to && from > to;

/** Trades under their invested date, newest date first; order inside a date is kept. */
export function groupByInvested<T extends { opened_at: string }>(trades: T[]): Array<{ date: string; trades: T[] }> {
  const map = new Map<string, T[]>();
  for (const t of trades) {
    const d = investedDate(t);
    map.set(d, [...(map.get(d) ?? []), t]);
  }
  return [...map.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([date, list]) => ({ date, trades: list }));
}
