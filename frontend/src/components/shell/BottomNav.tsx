import { Link } from "react-router-dom";
import { Bookmark, Briefcase, CircleUserRound, Lightbulb, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "watchlist", to: "/watchlist", label: "Watchlist", icon: Bookmark },
  { id: "stocks", to: "/dashboard", label: "Ideas", icon: Lightbulb },
  { id: "portfolio", to: "/portfolio", label: "Portfolio", icon: Briefcase },
  { id: "strategies", to: "/strategies", label: "Strategies", icon: FlaskConical },
] as const;

/** Phone navigation, app style: four tabs and your account. Pages you can't open are left out. */
export function BottomNav({ allowed, current, accountLabel }: { allowed: string[]; current: string | null; accountLabel: string }) {
  const tabs = TABS.filter((t) => t.id === "watchlist" || allowed.includes(t.id));
  const item = (active: boolean) =>
    cn("flex-1 min-w-0 flex flex-col items-center justify-center gap-1 min-h-14 text-[11px]", active ? "text-primary" : "text-foreground/70");
  return (
    <nav
      aria-label="Main"
      className="md:hidden fixed bottom-0 inset-x-0 z-30 border-t border-border bg-card/95 backdrop-blur flex pb-[env(safe-area-inset-bottom)]"
    >
      {tabs.map(({ id, to, label, icon: Icon }) => (
        <Link key={id} to={to} aria-current={current === id ? "page" : undefined} className={item(current === id)}>
          <Icon className="h-6 w-6" aria-hidden strokeWidth={1.75} />
          {label}
        </Link>
      ))}
      <Link to="/account" aria-current={current === "account" ? "page" : undefined} className={item(current === "account")}>
        <CircleUserRound className="h-6 w-6" aria-hidden strokeWidth={1.75} />
        <span className="max-w-full truncate px-1">{accountLabel}</span>
      </Link>
    </nav>
  );
}
