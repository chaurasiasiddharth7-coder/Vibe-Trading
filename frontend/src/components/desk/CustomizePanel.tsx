import { useState } from "react";
import { ArrowDown, ArrowUp, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { MultiSelect } from "@/components/desk/MultiSelect";
import { FILTERS, ROWS, TABS, TILES, useDeskPrefs, visibleTiles, type DeskPrefs } from "@/lib/deskPrefs";

function Choice<T extends string>({
  legend,
  value,
  options,
  onChange,
}: {
  legend: string;
  value: T;
  options: Array<{ value: T; label: string; hint: string }>;
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs text-muted-foreground">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            title={o.hint}
            className={cn(
              "min-h-10 px-3 rounded-md border text-sm",
              value === o.value ? "bg-emerald-600/20 border-emerald-600" : "border-border text-muted-foreground hover:bg-muted",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Full tile order: the chosen tiles first (in order), then the rest in catalog order. */
function tileOrder(p: DeskPrefs): string[] {
  const on = visibleTiles(p);
  return [...on, ...TILES.map((t) => t.id).filter((id) => !on.includes(id))];
}

const SAVE_TEXT = { idle: "", saving: "Saving…", saved: "Saved to your profile", failed: "Could not save. Your changes last until you close this page." };

export function CustomizePanel() {
  const { prefs, update, reset, saveState, me } = useDeskPrefs();
  const [open, setOpen] = useState(false);
  const order = tileOrder(prefs);
  const on = new Set(visibleTiles(prefs));
  const label = (id: string) => TILES.find((t) => t.id === id)?.label ?? id;

  const setTiles = (ids: string[], keep: Set<string>) => update({ tiles: ids.filter((id) => keep.has(id)) });
  const move = (id: string, by: -1 | 1) => {
    const i = order.indexOf(id);
    const j = i + by;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    setTiles(next, on);
  };
  const flipTile = (id: string) => {
    const keep = new Set(on);
    if (keep.has(id)) keep.delete(id);
    else keep.add(id);
    setTiles(order, keep);
  };

  const hideFrom = (key: "hidden_tabs" | "hidden_filters" | "hidden_rows", all: Array<{ id: string }>) => (shown: Set<string>) =>
    update({ [key]: all.map((o) => o.id).filter((id) => !shown.has(id)) } as Partial<DeskPrefs>);
  const shownOf = (key: "hidden_tabs" | "hidden_filters" | "hidden_rows", all: Array<{ id: string }>) =>
    new Set(all.map((o) => o.id).filter((id) => !prefs[key].includes(id)));

  return (
    <section aria-label="Customize this dashboard" className="rounded-lg border border-border">
      <div className="flex flex-wrap items-center justify-between gap-2 p-3">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="customize-body"
          onClick={() => setOpen(!open)}
          className="min-h-10 px-3 rounded-md border border-border text-sm flex items-center gap-2 hover:bg-muted"
        >
          <SlidersHorizontal className="h-4 w-4" aria-hidden />
          Customize
        </button>
        <p className="text-xs text-muted-foreground" role="status" aria-live="polite">
          {me && !me.is_owner ? `${me.name}'s own view. ` : ""}
          {SAVE_TEXT[saveState]}
        </p>
      </div>

      {open && (
        <div id="customize-body" className="border-t border-border p-4 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Choice
              legend="How to show the page"
              value={prefs.layout}
              onChange={(layout) => update({ layout })}
              options={[
                { value: "tabs", label: "Tabs", hint: "One section at a time" },
                { value: "stacked", label: "One page", hint: "All sections in one scroll" },
              ]}
            />
            <Choice
              legend="Spacing"
              value={prefs.density}
              onChange={(density) => update({ density })}
              options={[
                { value: "comfortable", label: "Comfortable", hint: "More room" },
                { value: "compact", label: "Compact", hint: "More on screen" },
              ]}
            />
            <Choice
              legend="Summary tiles"
              value={prefs.tile_style}
              onChange={(tile_style) => update({ tile_style })}
              options={[
                { value: "cards", label: "Cards", hint: "A grid of boxes" },
                { value: "strip", label: "One line", hint: "A single compact row" },
              ]}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">Sections</span>
              <MultiSelect label="Sections" options={TABS} shown={shownOf("hidden_tabs", TABS)} onChange={hideFrom("hidden_tabs", TABS)} />
            </div>
            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">Filters</span>
              <MultiSelect label="Filters" options={FILTERS} shown={shownOf("hidden_filters", FILTERS)} onChange={hideFrom("hidden_filters", FILTERS)} />
            </div>
            <div className="space-y-1">
              <span className="text-xs text-muted-foreground">Information rows</span>
              <MultiSelect label="Information rows" options={ROWS} shown={shownOf("hidden_rows", ROWS)} onChange={hideFrom("hidden_rows", ROWS)} alignRight />
            </div>
          </div>

          <fieldset className="space-y-2">
            <legend className="text-xs text-muted-foreground">Summary tiles: tick to show, arrows to reorder</legend>
            <ul className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {order.map((id, i) => (
                <li key={id} className="flex items-center gap-2 rounded-md border border-border px-2">
                  <label className="flex items-center gap-2 flex-1 min-h-11 text-sm cursor-pointer">
                    <input type="checkbox" checked={on.has(id)} onChange={() => flipTile(id)} className="h-4 w-4" />
                    <span>
                      {label(id)}
                      <span className="block text-xs text-muted-foreground">{TILES.find((t) => t.id === id)?.hint}</span>
                    </span>
                  </label>
                  <button
                    type="button"
                    aria-label={`Move ${label(id)} up`}
                    disabled={i === 0}
                    onClick={() => move(id, -1)}
                    className="h-10 w-10 grid place-items-center rounded-md hover:bg-muted disabled:opacity-30"
                  >
                    <ArrowUp className="h-4 w-4" aria-hidden />
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${label(id)} down`}
                    disabled={i === order.length - 1}
                    onClick={() => move(id, 1)}
                    className="h-10 w-10 grid place-items-center rounded-md hover:bg-muted disabled:opacity-30"
                  >
                    <ArrowDown className="h-4 w-4" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </fieldset>

          <button type="button" onClick={reset} className="min-h-10 px-3 rounded-md border border-border text-sm hover:bg-muted">
            Back to the standard view
          </button>
        </div>
      )}
    </section>
  );
}
