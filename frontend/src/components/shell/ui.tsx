import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface Tile {
  id: string;
  label: string;
  value: string;
  tone?: string;
  hint?: string;
}

/** At most 4 number tiles on top of every page (the one-glance rule). */
export function Tiles({ tiles, chosen }: { tiles: Tile[]; chosen?: string[] }) {
  const pick = chosen && chosen.length ? chosen.map((id) => tiles.find((t) => t.id === id)).filter((t): t is Tile => !!t) : tiles;
  const shown = pick.slice(0, 4);
  return (
    <dl className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {shown.map((t) => (
        <div key={t.id} className="rounded-md bg-muted/50 p-3" title={t.hint}>
          <dt className="text-xs text-muted-foreground">{t.label}</dt>
          <dd className={cn("font-mono text-lg tabular-nums", t.tone)}>{t.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PageHeader({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{title}</h1>
        {sub && <p className="text-sm text-muted-foreground max-w-2xl">{sub}</p>}
      </div>
      {right}
    </header>
  );
}

export function Note({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" | "bad" }) {
  return (
    <p
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        tone === "info" && "border-border text-muted-foreground",
        tone === "warn" && "border-amber-700/60 bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200",
        tone === "bad" && "border-rose-700/60 bg-rose-50 text-rose-800 dark:bg-rose-950/30 dark:text-rose-200",
      )}
    >
      {children}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">{children}</p>;
}

export function Page({ children }: { children: ReactNode }) {
  return <main className="p-4 sm:p-6 space-y-5 max-w-6xl">{children}</main>;
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "min-h-9 px-3 rounded-full border text-sm whitespace-nowrap",
        active ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
