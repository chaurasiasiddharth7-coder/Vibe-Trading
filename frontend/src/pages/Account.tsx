import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight, Eye, EyeOff, KeyRound, LogOut, Monitor, Moon, SlidersHorizontal, Sun, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDarkMode, type ThemeMode } from "@/hooks/useDarkMode";
import { menuPages, useSession } from "@/lib/session";
import { desk } from "@/lib/deskApi";
import { setApiAuthKey } from "@/lib/apiAuth";
import { ChangePassword } from "@/pages/Login";

const THEMES: Array<{ id: ThemeMode; label: string; icon: typeof Sun }> = [
  { id: "light", label: "Light", icon: Sun },
  { id: "dark", label: "Dark", icon: Moon },
  { id: "system", label: "System", icon: Monitor },
];

/** Account and settings: theme, privacy, layout, every other page, and sign-out. */
export function Account() {
  const { me, prefs, allowed, savePrefs, refresh } = useSession();
  const { mode, setMode } = useDarkMode();
  const navigate = useNavigate();
  const [changing, setChanging] = useState(false);
  const password = me?.signed_in_with === "password";
  const pages = menuPages(allowed, prefs).filter((p) => !["watchlist", "stocks", "portfolio", "strategies", "users"].includes(p.id));

  const pickTheme = (t: ThemeMode) => {
    setMode(t);
    void savePrefs({ theme: t }); // follows you to your other devices
  };

  const signOut = async () => {
    try {
      await desk.logout();
    } catch {
      // already signed out
    }
    if (!password) setApiAuthKey("");
    await refresh();
    navigate("/");
  };

  if (changing) return <ChangePassword onDone={() => setChanging(false)} onCancel={() => setChanging(false)} />;

  return (
    <main className="max-w-xl mx-auto px-4 pt-4 pb-24 md:pb-8 space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">{me?.name ?? "Account"}</h1>
        <p className="text-sm text-muted-foreground">
          {me?.username ?? "owner"} · {me?.role ?? "owner"} · Paper trading only. Kite is used for prices, never orders.
        </p>
      </header>

      <section aria-labelledby="appearance" className="space-y-2">
        <h2 id="appearance" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Appearance</h2>
        <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2 rounded-lg bg-muted p-1">
          {THEMES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={mode === id}
              onClick={() => pickTheme(id)}
              className={cn(
                "min-h-11 rounded-md inline-flex items-center justify-center gap-2 text-sm",
                mode === id ? "bg-card text-primary font-semibold shadow-sm" : "text-foreground/80",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </button>
          ))}
        </div>
        <Row icon={prefs.privacy_mode ? Eye : EyeOff} onClick={() => void savePrefs({ privacy_mode: !prefs.privacy_mode })}>
          <span className="flex-1">Hide amounts (privacy)</span>
          <span className={cn("text-sm", prefs.privacy_mode ? "text-primary font-medium" : "text-muted-foreground")}>{prefs.privacy_mode ? "On" : "Off"}</span>
        </Row>
        <Row icon={SlidersHorizontal} to="/layout">My layout</Row>
      </section>

      {pages.length > 0 && (
        <section aria-labelledby="more" className="space-y-2">
          <h2 id="more" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">More pages</h2>
          <div className="rounded-lg border border-border bg-card divide-y divide-border">
            {pages.map((p) => (
              <Link key={p.id} to={p.to} className="flex items-center min-h-12 px-4 hover:bg-muted/50">
                <span className="flex-1">{p.label}</span>
                <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
              </Link>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="acct" className="space-y-2">
        <h2 id="acct" className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Account</h2>
        {allowed.includes("users") && <Row icon={Users} to="/users">Users</Row>}
        {password && <Row icon={KeyRound} onClick={() => setChanging(true)}>Change password</Row>}
        {password && (
          <Row icon={LogOut} onClick={async () => { await desk.logoutAll().catch(() => null); await refresh(); }}>Sign out everywhere</Row>
        )}
        <Row icon={LogOut} onClick={() => void signOut()} danger>{password ? "Sign out" : "Forget key on this browser"}</Row>
      </section>
    </main>
  );
}

function Row({ icon: Icon, to, onClick, danger, children }: {
  icon: typeof Sun;
  to?: string;
  onClick?: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  const cls = cn("w-full flex items-center gap-3 min-h-12 px-4 rounded-lg border border-border bg-card text-start hover:bg-muted/50", danger && "text-danger");
  const body = (
    <>
      <Icon className={cn("h-4 w-4", danger ? "text-danger" : "text-muted-foreground")} aria-hidden />
      {children}
    </>
  );
  return to ? <Link to={to} className={cls}>{body}</Link> : <button type="button" onClick={onClick} className={cls}>{body}</button>;
}
