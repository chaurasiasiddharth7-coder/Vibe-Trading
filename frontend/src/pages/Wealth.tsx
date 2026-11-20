import { useEffect, useState } from "react";
import { desk, rupees, tone, type Holding, type WealthData } from "@/lib/deskApi";
import { useSession } from "@/lib/session";
import { Empty, Note, Page, PageHeader, Tiles } from "@/components/shell/ui";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "gold", label: "Gold & silver", hint: "Gold and silver ETFs, sovereign gold bonds" },
  { id: "equity_etf", label: "Equity ETF", hint: "Index and sector ETFs" },
  { id: "debt_etf", label: "Debt ETF", hint: "Liquid, gilt and bond ETFs" },
  { id: "mf", label: "Mutual funds", hint: "Funds held through Coin" },
] as const;
type TabId = (typeof TABS)[number]["id"];

/** Long-term holdings by type. SIPs are read-only here: Coin manages them. */
export function Wealth() {
  const { prefs } = useSession();
  const [data, setData] = useState<WealthData | null>(null);
  const [tab, setTab] = useState<TabId>("gold");
  const [error, setError] = useState("");

  useEffect(() => {
    desk
      .wealth()
      .then((r) => {
        setData(r.data);
        const first = TABS.find((t) => r.data.tabs[t.id].length);
        if (first) setTab(first.id);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load."));
  }, []);

  const rows: Holding[] = data ? data.tabs[tab] : [];
  const biggest = data ? Object.entries(data.allocation_pct).sort((a, b) => b[1] - a[1])[0] : null;
  const labelOf = (id: string) => TABS.find((t) => t.id === id)?.label ?? id;

  return (
    <Page>
      <PageHeader title="Gold · ETF · MF" sub="Your long-term money, split by type, apart from direct stocks." />
      <Tiles
        chosen={prefs.tiles_by_page.wealth}
        tiles={[
          { id: "value", label: "Total value", value: rupees(data?.total_value ?? null) },
          {
            id: "return",
            label: "Return",
            value: data?.return_pct == null ? "—" : `${data.return_pct > 0 ? "+" : ""}${data.return_pct}%`,
            tone: tone(data?.return_pct),
            hint: "Gain on money put in. Annual return (XIRR) needs your purchase dates, which Kite doesn't give.",
          },
          { id: "sips", label: "SIPs due this week", value: data ? `${data.sips_due_week.length} · ${rupees(data.sips_due_amount)}` : "—" },
          { id: "alloc", label: "Largest share", value: biggest && biggest[1] ? `${labelOf(biggest[0])} ${biggest[1]}%` : "—" },
          { id: "pnl", label: "Total P&L", value: rupees(data?.total_pnl ?? null, 0, true), tone: tone(data?.total_pnl) },
        ]}
      />
      {data && data.total_value > 0 && (
        <div className="space-y-1" aria-label="Allocation">
          <div className="flex h-3 rounded overflow-hidden bg-muted">
            {TABS.map((t, i) => (
              <span
                key={t.id}
                title={`${t.label} ${data.allocation_pct[t.id]}%`}
                className={cn("h-full", ["bg-amber-500", "bg-sky-500", "bg-slate-500", "bg-violet-500"][i])}
                style={{ width: `${data.allocation_pct[t.id]}%` }}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground flex flex-wrap gap-3">
            {TABS.map((t) => (
              <span key={t.id}>{t.label} <span className="font-mono">{data.allocation_pct[t.id]}%</span></span>
            ))}
          </p>
        </div>
      )}
      {data?.note && <Note>{data.note}</Note>}
      {error && <Note tone="bad">{error}</Note>}

      <div role="tablist" aria-label="Holding types" className="flex gap-1 border-b border-border overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            title={t.hint}
            onClick={() => setTab(t.id)}
            className={cn("min-h-11 px-4 text-sm whitespace-nowrap border-b-2 -mb-px", tab === t.id ? "border-foreground font-semibold" : "border-transparent text-muted-foreground")}
          >
            {t.label}
            <span className="ms-2 text-xs font-mono text-muted-foreground">{data?.tabs[t.id].length ?? 0}</span>
          </button>
        ))}
      </div>

      {!data ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <Empty>Nothing here yet.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border" role="tabpanel">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="text-start px-3 py-2">{tab === "mf" ? "Fund" : "Instrument"}</th>
                <th className="text-end px-3 py-2">{tab === "mf" ? "Units" : "Qty"}</th>
                <th className="text-end px-3 py-2">{tab === "mf" ? "NAV" : "LTP"}</th>
                <th className="text-end px-3 py-2">Value</th>
                <th className="text-end px-3 py-2">P&amp;L</th>
                {tab === "mf" && <th className="text-start px-3 py-2">Next SIP</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((h) => (
                <tr key={h.id}>
                  <td className="px-3 py-2">
                    <div className="font-medium">{h.name}</div>
                    {(h.source === "paper" || h.name !== h.symbol) && (
                      <div className="text-xs text-muted-foreground">{h.source === "paper" ? "practice trade" : h.symbol}</div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-end font-mono">{h.qty}</td>
                  <td className="px-3 py-2 text-end font-mono">{rupees(h.ltp, 2)}</td>
                  <td className="px-3 py-2 text-end font-mono">{rupees(h.value)}</td>
                  <td className={cn("px-3 py-2 text-end font-mono", tone(h.pnl))}>
                    {rupees(h.pnl, 0, true)} {h.pnl_pct != null && <span className="text-xs">({h.pnl_pct}%)</span>}
                  </td>
                  {tab === "mf" && (
                    <td className="px-3 py-2 text-xs font-mono">{h.next_sip ? `${h.next_sip.next_date ?? "—"} · ${rupees(h.next_sip.amount)}` : "—"}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.sips.length > 0 && (
        <p className="text-xs text-muted-foreground">SIP dates and amounts come from Coin and can only be changed there.</p>
      )}
    </Page>
  );
}
