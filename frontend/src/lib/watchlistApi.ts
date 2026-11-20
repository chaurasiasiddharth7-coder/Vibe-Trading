import { request } from "@/lib/api";

export interface WatchList {
  name: string;
  items: string[];
}

export interface Quote {
  symbol: string;
  exchange: string;
  ltp: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  as_of?: string;
  volume?: number | null;
}

export interface QuotesResponse {
  /** true: Kite live prices (owner's desk only). false: end-of-day prices from the NSE store. */
  live: boolean;
  source: "kite" | "nse_eod";
  at: number;
  indices: Quote[];
  quotes: Record<string, Quote>;
}

const enc = encodeURIComponent;

export const watchlistApi = {
  lists: () => request<{ lists: WatchList[]; max_items: number }>("/api/desk/watchlists"),
  add: (i: number, symbol: string) =>
    request<{ lists: WatchList[] }>(`/api/desk/watchlists/${i}/items`, { method: "POST", body: JSON.stringify({ symbol }) }),
  remove: (i: number, symbol: string) =>
    request<{ lists: WatchList[] }>(`/api/desk/watchlists/${i}/items/${enc(symbol)}`, { method: "DELETE" }),
  rename: (i: number, name: string) =>
    request<{ lists: WatchList[] }>(`/api/desk/watchlists/${i}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  quotes: (symbols: string[], indices = true) =>
    request<QuotesResponse>(`/api/desk/quotes?symbols=${enc(symbols.join(","))}&indices=${indices ? 1 : 0}`),
  search: (q: string) => request<{ results: Array<{ symbol: string; exchange: string }> }>(`/api/desk/symbols?q=${enc(q)}`),
};

const inr = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function fmtPrice(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? "—" : inr.format(n);
}

export function fmtChange(q: Pick<Quote, "change" | "change_pct">): string {
  if (q.change == null) return "";
  const sign = q.change > 0 ? "+" : "";
  const pct = q.change_pct == null ? "" : ` (${q.change_pct > 0 ? "+" : ""}${q.change_pct.toFixed(2)}%)`;
  return `${sign}${inr.format(q.change)}${pct}`;
}

export function tone(change: number | null | undefined): string {
  if (change == null || change === 0) return "text-foreground";
  return change > 0 ? "text-success" : "text-danger";
}
