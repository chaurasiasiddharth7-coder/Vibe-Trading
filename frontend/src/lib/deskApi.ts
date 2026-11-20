import { request } from "@/lib/api";

/** One holding, in the same shape for Kite and paper trades. */
export interface Holding {
  id: string;
  symbol: string;
  exchange: string;
  name: string;
  asset_type: "stock" | "fno" | "equity_etf" | "debt_etf" | "gold" | "mf";
  source: "kite" | "paper";
  side: "LONG" | "SHORT";
  qty: number;
  avg: number;
  ltp: number;
  value: number;
  invested: number;
  pnl: number;
  pnl_pct: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  stop?: number | null;
  target?: number | null;
  next_sip?: Sip | null;
  folio?: string | null;
}

export interface KiteState {
  connected: boolean;
  expires_at_ist?: string | null;
  api_key_set?: boolean;
}

export interface GroupLine {
  holding_id: string;
  symbol: string;
  qty: number;
  value: number;
  pnl: number;
}

export interface Group {
  id: string;
  name: string;
  colour: string;
  goal_amount: number | null;
  goal_date: string | null;
  asset_types: string[];
  lines: GroupLine[];
  /** Allocations whose holding couldn't be read this time (Kite logged out): kept, not counted. */
  waiting?: Array<{ holding_id: string; symbol: string; qty: number }>;
  value: number;
  invested: number;
  pnl: number;
  day_change: number;
  goal_progress_pct: number | null;
}

export interface GroupsData {
  groups: Group[];
  ungrouped: Array<{ holding_id: string; symbol: string; qty: number; value: number }>;
  ungrouped_pct: number | null;
  notes: string[];
  colours: string[];
  holdings: Holding[];
  kite: KiteState;
  note: string | null;
  as_of: string;
}

export interface FnoRow {
  symbol: string;
  underlying: string;
  qty: number;
  side: "LONG" | "SHORT";
  entry: number;
  ltp: number | null;
  pnl: number | null;
  m2m: number | null;
  expiry: string | null;
  source: "kite" | "paper";
  stop: number | null;
  target: number | null;
  state: string | null;
  locked: number | null;
  one_r: number | null;
  r_now: number | null;
}

export interface FnoData {
  rows: FnoRow[];
  day_mtm: number | null;
  floor: string | number | null;
  peak: string | number | null;
  margin_used: number | null;
  nearest_expiry_days: number | null;
  expiry_today: boolean;
  kite: KiteState;
  note: string | null;
}

export interface ChainLeg {
  symbol: string;
  ltp: number;
  oi: number;
  oi_change: number | null;
  volume: number;
  lot: number;
}

export interface ChainData {
  available: boolean;
  reason?: string;
  underlying?: string;
  expiry?: string;
  expiries?: string[];
  spot?: number | null;
  atm?: number;
  rows?: Array<{ strike: number; ce?: ChainLeg; pe?: ChainLeg }>;
  pcr?: number | null;
  max_pain?: number | null;
}

export interface Sip {
  fund: string;
  symbol: string;
  amount: number;
  frequency: string;
  next_date: string | null;
}

export interface WealthData {
  tabs: { gold: Holding[]; equity_etf: Holding[]; debt_etf: Holding[]; mf: Holding[] };
  sips: Sip[];
  sips_due_week: Sip[];
  sips_due_amount: number;
  allocation: Record<string, number>;
  allocation_pct: Record<string, number>;
  total_value: number;
  total_pnl: number;
  return_pct: number | null;
  kite: KiteState;
  note: string | null;
}

export interface Suggestion {
  id: string;
  symbol: string;
  name: string;
  asset_type: string;
  strategy: string;
  action: "buy" | "short" | "avoid";
  score: number;
  reason: string;
  risk: string | null;
  entry: number | null;
  stop: number | null;
  target: number | null;
  track: string;
  as_of: string | null;
  stale: boolean;
}

export interface GuardPlan {
  id: string;
  strategy: string;
  symbol: string;
  side: string;
  qty: number;
  entry: number;
  stop: number;
  target: number | null;
  state: string;
  realized: number;
  carry: boolean;
  is_option: boolean;
  gtt: boolean;
  mode: string;
}

export interface GuardStatus {
  available: boolean;
  reason: string | null;
  kite?: KiteState;
  open?: GuardPlan[];
  day?: {
    peak_mtm?: string;
    floor?: string | null;
    armed?: boolean;
    loss_cap_hit?: boolean;
    lock_hit?: boolean;
    last_mtm?: string;
    updated_ms?: number;
  } | null;
  capital_inr?: number | null;
  paper_only?: boolean;
}

export interface DeskUser {
  id: string;
  username: string;
  name: string;
  role: "owner" | "trader" | "viewer";
  pages: string[];
  must_change: boolean;
  disabled: boolean;
  locked: boolean;
  locked_until: number | null;
  created_at: number;
  last_login_at: number | null;
}

type Ok<T> = { ok: boolean; data: T };
const json = (body: unknown) => ({ body: JSON.stringify(body) });

export const desk = {
  session: () => request<Ok<DeskUser | null>>("/api/desk/auth/session"),
  login: (username: string, password: string) =>
    request<Ok<DeskUser>>("/api/desk/auth/login", { method: "POST", ...json({ username, password }) }),
  logout: () => request<{ ok: boolean }>("/api/desk/auth/logout", { method: "POST", body: "{}" }),
  logoutAll: () => request<Ok<{ ended: number }>>("/api/desk/auth/logout-all", { method: "POST", body: "{}" }),
  changePassword: (old: string, next: string) =>
    request<Ok<DeskUser>>("/api/desk/auth/change-password", { method: "POST", ...json({ old, new: next }) }),

  users: () =>
    request<Ok<{ users: DeskUser[]; counts: { active: number; signed_in_today: number; locked: number }; pages: string[]; roles: string[] }>>(
      "/api/desk/users",
    ),
  addUser: (body: { username: string; name: string; role: string; pages: string[] }) =>
    request<Ok<{ user: DeskUser; one_time_password: string }>>("/api/desk/users", { method: "POST", ...json(body) }),
  editUser: (id: string, body: Partial<{ name: string; role: string; pages: string[] }>) =>
    request<Ok<DeskUser>>(`/api/desk/users/${encodeURIComponent(id)}`, { method: "PATCH", ...json(body) }),
  resetUser: (id: string) =>
    request<Ok<{ one_time_password: string }>>(`/api/desk/users/${encodeURIComponent(id)}/reset`, { method: "POST", body: "{}" }),
  userAction: (id: string, action: "disable" | "enable" | "unlock") =>
    request<Ok<DeskUser>>(`/api/desk/users/${encodeURIComponent(id)}/${action}`, { method: "POST", body: "{}" }),

  guard: () => request<Ok<GuardStatus>>("/api/desk/guard/status"),
  groups: () => request<Ok<GroupsData>>("/api/desk/groups"),
  addGroup: (body: Partial<Group>) => request<Ok<Group>>("/api/desk/groups", { method: "POST", ...json(body) }),
  editGroup: (id: string, body: Partial<Group>) =>
    request<Ok<Group>>(`/api/desk/groups/${encodeURIComponent(id)}`, { method: "PATCH", ...json(body) }),
  deleteGroup: (id: string) => request<{ ok: boolean }>(`/api/desk/groups/${encodeURIComponent(id)}`, { method: "DELETE" }),
  allocate: (id: string, holding_id: string, qty: number) =>
    request<Ok<Group>>(`/api/desk/groups/${encodeURIComponent(id)}/allocations`, { method: "PUT", ...json({ holding_id, qty }) }),
  fno: () => request<Ok<FnoData>>("/api/desk/fno"),
  chain: (underlying: string, expiry?: string) =>
    request<Ok<ChainData>>(
      `/api/desk/fno/chain?underlying=${encodeURIComponent(underlying)}${expiry ? `&expiry=${encodeURIComponent(expiry)}` : ""}`,
    ),
  wealth: () => request<Ok<WealthData>>("/api/desk/wealth"),
  suggestions: () => request<Ok<{ cards: Suggestion[]; as_of: string | null; disclaimer: string }>>("/api/desk/suggestions"),
};

/** Signed rupees, Indian grouping, no decimals by default. */
export function rupees(v: number | null | undefined, digits = 0, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  const sign = v < 0 ? "−" : signed && v > 0 ? "+" : "";
  return `${sign}₹${s}`;
}

export const tone = (v: number | null | undefined) =>
  v === null || v === undefined || v === 0 ? "" : v > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400";

export const ASSET_LABEL: Record<string, string> = {
  stock: "Stock",
  fno: "F&O",
  equity_etf: "Equity ETF",
  debt_etf: "Debt ETF",
  gold: "Gold & silver",
  mf: "Mutual fund",
};
