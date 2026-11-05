import { describe, expect, it } from "vitest";
import { DEFAULT_PREFS, TABS, TILES, normalizePrefs, visibleTiles } from "../deskPrefs";

describe("normalizePrefs", () => {
  it("falls back to the standard view", () => {
    expect(normalizePrefs(null)).toEqual(DEFAULT_PREFS);
    expect(normalizePrefs({ layout: "weird" as never, density: 3 as never })).toEqual(DEFAULT_PREFS);
  });

  it("drops unknown and repeated ids", () => {
    const p = normalizePrefs({ tiles: ["best", "nope", "best", "winning"], hidden_rows: ["qty", "price_qty"], hidden_filters: ["dates", 4 as never] });
    expect(p.tiles).toEqual(["best", "winning"]);
    expect(p.hidden_rows).toEqual(["price_qty"]);
    expect(p.hidden_filters).toEqual(["dates"]);
  });

  it("never lets every tab be hidden", () => {
    expect(normalizePrefs({ hidden_tabs: TABS.map((t) => t.id) }).hidden_tabs).toEqual([]);
    expect(normalizePrefs({ hidden_tabs: ["chart"] }).hidden_tabs).toEqual(["chart"]);
  });

  it("shows the default tiles until a choice is made, and keeps an empty choice empty", () => {
    expect(visibleTiles(DEFAULT_PREFS)).toEqual(TILES.filter((t) => t.on).map((t) => t.id));
    expect(visibleTiles(normalizePrefs({ tiles: [] }))).toEqual([]);
  });
});
