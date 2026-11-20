import { useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { APP_PAGES, menuPages, useSession } from "@/lib/session";
import { Page, PageHeader, Note } from "@/components/shell/ui";
import { PAGE_TILES } from "@/lib/pageTiles";
import { cn } from "@/lib/utils";

/** Each person shapes their own app: menu order, hidden pages, start page, tiles, privacy. */
export function MyLayout() {
  const { prefs, allowed, savePrefs } = useSession();
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const mine = APP_PAGES.filter((p) => allowed.includes(p.id));
  const ordered = menuPages(allowed, { pages_order: prefs.pages_order, hidden_pages: [] });
  const hidden = new Set(prefs.hidden_pages);

  const save = async (patch: Parameters<typeof savePrefs>[0]) => {
    setError("");
    setSaved(false);
    try {
      await savePrefs(patch);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save.");
    }
  };

  const move = (id: string, dir: -1 | 1) => {
    const ids: string[] = ordered.map((p) => p.id);
    const i = ids.indexOf(id);
    const j = i + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    void save({ pages_order: ids });
  };

  const toggleHidden = (id: string) => {
    const next = new Set(hidden);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    if (mine.every((p) => next.has(p.id) || p.id === "users")) return setError("Keep at least one page in the menu.");
    void save({ hidden_pages: [...next] });
  };

  const setTiles = (page: string, id: string) => {
    const cur = prefs.tiles_by_page[page] ?? PAGE_TILES[page].slice(0, 4).map((t) => t.id);
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    if (next.length > 4) return setError("Pick up to 4 tiles per page.");
    if (next.length === 0) return setError("Keep at least one tile.");
    void save({ tiles_by_page: { ...prefs.tiles_by_page, [page]: next } });
  };

  return (
    <Page>
      <PageHeader title="My layout" sub="Changes save for you only and follow you to any device." />
      {error && <Note tone="bad">{error}</Note>}
      {saved && !error && <p className="text-xs text-muted-foreground" role="status">Saved.</p>}

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Menu</h2>
        <ul className="divide-y divide-border rounded-md border border-border">
          {ordered.map((p, i) => (
            <li key={p.id} className="flex items-center gap-2 px-3 min-h-12">
              <span className={cn("flex-1 text-sm", hidden.has(p.id) && "text-muted-foreground line-through")}>{p.label}</span>
              <button type="button" aria-label={`Move ${p.label} up`} disabled={i === 0} onClick={() => move(p.id, -1)} className="h-9 w-9 grid place-items-center rounded hover:bg-muted disabled:opacity-30">
                <ArrowUp className="h-4 w-4" />
              </button>
              <button type="button" aria-label={`Move ${p.label} down`} disabled={i === ordered.length - 1} onClick={() => move(p.id, 1)} className="h-9 w-9 grid place-items-center rounded hover:bg-muted disabled:opacity-30">
                <ArrowDown className="h-4 w-4" />
              </button>
              {p.id !== "users" && (
                <label className="flex items-center gap-1 text-xs">
                  <input type="checkbox" checked={!hidden.has(p.id)} onChange={() => toggleHidden(p.id)} />
                  Show
                </label>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Start page</h2>
        <select
          className="min-h-11 rounded-md border border-border bg-background px-3 text-sm"
          value={prefs.landing_page}
          onChange={(e) => void save({ landing_page: e.target.value })}
        >
          {ordered.filter((p) => !hidden.has(p.id)).map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
      </section>

      <section className="space-y-2">
        <h2 className="text-base font-semibold">Privacy</h2>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={prefs.privacy_mode} onChange={() => void save({ privacy_mode: !prefs.privacy_mode })} />
          Blur every number (for screen sharing)
        </label>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold">Tiles on each page (up to 4)</h2>
        {Object.entries(PAGE_TILES)
          .filter(([page]) => allowed.includes(page))
          .map(([page, tiles]) => {
            const cur = prefs.tiles_by_page[page] ?? tiles.slice(0, 4).map((t) => t.id);
            return (
              <fieldset key={page} className="rounded-md border border-border p-3">
                <legend className="px-1 text-sm font-medium">{APP_PAGES.find((p) => p.id === page)?.label}</legend>
                <div className="flex flex-wrap gap-3">
                  {tiles.map((t) => (
                    <label key={t.id} className="flex items-center gap-1 text-sm">
                      <input type="checkbox" checked={cur.includes(t.id)} onChange={() => setTiles(page, t.id)} />
                      {t.label}
                    </label>
                  ))}
                </div>
              </fieldset>
            );
          })}
        <p className="text-xs text-muted-foreground">The Stocks page keeps its own "Customize" panel for its tiles and lists.</p>
      </section>
    </Page>
  );
}
