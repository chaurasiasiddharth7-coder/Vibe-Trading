import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CatalogItem } from "@/lib/deskPrefs";

/**
 * A dropdown of tick boxes. `shown` holds the ids that are ticked (visible); ticking and unticking
 * calls `onChange` with the new set. Esc or a click outside closes it.
 */
export function MultiSelect({
  label,
  options,
  shown,
  onChange,
  alignRight = false,
}: {
  label: string;
  options: CatalogItem[];
  shown: Set<string>;
  onChange: (next: Set<string>) => void;
  /** Open towards the left so the list stays on screen when the button sits at the right edge. */
  alignRight?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const count = options.filter((o) => shown.has(o.id)).length;
  const flip = (id: string) => {
    const next = new Set(shown);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen(!open)}
        className="min-h-10 w-full sm:w-64 px-3 rounded-md border border-border bg-background text-sm flex items-center justify-between gap-2 hover:bg-muted"
      >
        <span className="truncate">{label}</span>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground font-mono whitespace-nowrap">
          {count} of {options.length} shown
        </span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform duration-150 motion-reduce:transition-none", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label={label}
          className={cn("absolute z-20 mt-1 w-full sm:w-80 max-h-80 overflow-auto rounded-md border border-border bg-background shadow-lg", alignRight && "right-0")}
        >
          <div className="flex gap-2 p-2 border-b border-border text-xs">
            <button type="button" onClick={() => onChange(new Set(options.map((o) => o.id)))} className="min-h-8 px-2 rounded hover:bg-muted">
              Show all
            </button>
            <button type="button" onClick={() => onChange(new Set())} className="min-h-8 px-2 rounded hover:bg-muted">
              Hide all
            </button>
          </div>
          <ul>
            {options.map((o) => {
              const on = shown.has(o.id);
              return (
                <li key={o.id}>
                  <label className="flex items-start gap-3 px-3 py-2 min-h-11 cursor-pointer hover:bg-muted/60">
                    <input type="checkbox" checked={on} onChange={() => flip(o.id)} className="sr-only peer" />
                    <span
                      aria-hidden
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0 rounded border grid place-items-center peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-500",
                        on ? "bg-emerald-600 border-emerald-600 text-white" : "border-border",
                      )}
                    >
                      {on && <Check className="h-3 w-3" />}
                    </span>
                    <span className="text-sm leading-tight">
                      {o.label}
                      {o.hint && <span className="block text-xs text-muted-foreground">{o.hint}</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
