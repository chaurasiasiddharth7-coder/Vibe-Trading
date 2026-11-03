import { describe, expect, it } from "vitest";
import { backwards, groupByInvested, inInvestedRange, investedDate } from "../tradeFilters";

const at = (iso: string) => ({ opened_at: iso });

describe("date invested (India time)", () => {
  it("uses the IST calendar day, not the UTC one", () => {
    expect(investedDate(at("2026-10-22T18:57:33Z"))).toBe("2026-10-23"); // 00:27 IST next day
    expect(investedDate(at("2026-10-22T18:29:00Z"))).toBe("2026-10-22"); // 23:59 IST
    expect(investedDate(at("not a date"))).toBe("");
  });

  it("filters inclusively and treats an empty bound as open-ended", () => {
    const t = at("2026-10-14T10:00:00Z");
    expect(inInvestedRange(t, "", "")).toBe(true);
    expect(inInvestedRange(t, "2026-10-14", "2026-10-14")).toBe(true);
    expect(inInvestedRange(t, "2026-10-15", "")).toBe(false);
    expect(inInvestedRange(t, "", "2026-10-13")).toBe(false);
    expect(inInvestedRange(t, "2026-10-01", "")).toBe(true);
    expect(inInvestedRange(at("garbage"), "2026-10-01", "")).toBe(false);
    expect(inInvestedRange(at("garbage"), "", "")).toBe(true);
  });

  it("spots a backwards range", () => {
    expect(backwards("2026-10-15", "2026-10-14")).toBe(true);
    expect(backwards("2026-10-14", "2026-10-14")).toBe(false);
    expect(backwards("", "2026-10-14")).toBe(false);
  });

  it("groups newest date first and keeps the order within a date", () => {
    const trades = [
      { id: "a", opened_at: "2026-10-13T10:00:00Z" },
      { id: "b", opened_at: "2026-10-14T10:00:00Z" },
      { id: "c", opened_at: "2026-10-13T11:00:00Z" },
    ];
    const g = groupByInvested(trades);
    expect(g.map((x) => x.date)).toEqual(["2026-10-14", "2026-10-13"]);
    expect(g[1].trades.map((t) => t.id)).toEqual(["a", "c"]);
    expect(groupByInvested([])).toEqual([]);
  });
});
