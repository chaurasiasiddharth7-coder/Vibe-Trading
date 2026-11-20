import { useEffect, useRef, useState } from "react";
import { Link, Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { BarChart3, ChevronDown, EyeOff, Eye, LogOut, Settings, Sun, SlidersHorizontal, KeyRound, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { setThemeMode } from "@/hooks/useDarkMode";
import { BottomNav } from "@/components/shell/BottomNav";
import { IndexStrip } from "@/components/shell/IndexStrip";
import { SessionProvider, menuPages, pageForPath, useSession, APP_PAGES } from "@/lib/session";
import { desk } from "@/lib/deskApi";
import { setApiAuthKey } from "@/lib/apiAuth";
import { ChangePassword, Login } from "@/pages/Login";

/** The hosted app frame, Kite style: one top menu, one focused page. */
export function DeskShell() {
  return (
    <SessionProvider>
      <ShellBody />
    </SessionProvider>
  );
}

function ShellBody() {
  const { state, refresh } = useSession();
  if (state === "loading") return <div className="h-screen grid place-items-center text-sm text-muted-foreground">Loading…</div>;
  if (state === "signed_out") return <Login onDone={() => void refresh()} />;
  if (state === "must_change") return <ChangePassword forced onDone={() => void refresh()} />;
  return <Frame />;
}

function Frame() {
  const { me, prefs, allowed, state, savePrefs, refresh } = useSession();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const pages = menuPages(allowed, prefs);
  const current = pageForPath(pathname);
  const noAccess = current !== null && !allowed.includes(current);
  const password = me?.signed_in_with === "password";

  // The theme you picked in Settings follows you to every device you sign in on.
  useEffect(() => {
    if (state === "ready") setThemeMode(prefs.theme);
  }, [state, prefs.theme]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menuRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  const signOut = async () => {
    setOpen(false);
    try {
      await desk.logout();
    } catch {
      // already signed out
    }
    if (!password) setApiAuthKey("");
    await refresh();
    navigate("/");
  };

  return (
    <div className="min-h-screen bg-background" data-privacy={prefs.privacy_mode ? "on" : "off"}>
      <header className="sticky top-0 z-30 bg-background/95 backdrop-blur pt-[env(safe-area-inset-top)] md:border-b md:border-border md:bg-card/95">
        <IndexStrip className="md:hidden" />
        <div className="hidden md:flex items-center gap-2 px-3 sm:px-4 h-12">
          <Link to="/" className="flex items-center gap-2 font-semibold shrink-0 me-2">
            <BarChart3 className="h-5 w-5 text-primary" aria-hidden />
            <span className="hidden sm:inline">Vibe Desk</span>
          </Link>
          <nav aria-label="Pages" className="flex-1 min-w-0 flex gap-1 overflow-x-auto">
            {pages.map((p) => (
              <Link
                key={p.id}
                to={p.to}
                aria-current={current === p.id ? "page" : undefined}
                className={cn(
                  "min-h-9 px-3 inline-flex items-center rounded-md text-sm whitespace-nowrap",
                  current === p.id ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {p.label}
              </Link>
            ))}
          </nav>
          <span className="hidden md:inline text-xs rounded border border-border px-2 py-0.5 text-muted-foreground" title="Kite is used for prices only. No real orders are ever sent.">
            Paper only
          </span>
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-haspopup="menu"
              className="min-h-9 px-2 inline-flex items-center gap-1 rounded-md text-sm hover:bg-muted"
            >
              <span className="max-w-28 truncate">{me?.name ?? "Account"}</span>
              <ChevronDown className="h-4 w-4" aria-hidden />
            </button>
            {open && (
              <div role="menu" className="absolute end-0 mt-1 w-60 rounded-md border border-border bg-card shadow-lg p-1 text-sm">
                <p className="px-3 py-2 text-xs text-muted-foreground">
                  {me?.role ?? "owner"} · signed in with {password ? "password" : "key"}
                </p>
                <MenuItem icon={SlidersHorizontal} onClick={() => { setOpen(false); navigate("/layout"); }}>My layout</MenuItem>
                <MenuItem icon={prefs.privacy_mode ? Eye : EyeOff} onClick={() => void savePrefs({ privacy_mode: !prefs.privacy_mode })}>
                  {prefs.privacy_mode ? "Show amounts" : "Hide amounts (privacy)"}
                </MenuItem>
                <MenuItem icon={Settings} onClick={() => { setOpen(false); navigate("/account"); }}>Settings (theme, dark mode)</MenuItem>
                {allowed.includes("users") && (
                  <MenuItem icon={Users} onClick={() => { setOpen(false); navigate("/users"); }}>Users</MenuItem>
                )}
                {password && (
                  <MenuItem icon={KeyRound} onClick={() => { setOpen(false); setChanging(true); }}>Change password</MenuItem>
                )}
                {password && (
                  <MenuItem icon={LogOut} onClick={async () => { setOpen(false); await desk.logoutAll().catch(() => null); await refresh(); }}>
                    Sign out everywhere
                  </MenuItem>
                )}
                <MenuItem icon={LogOut} onClick={() => void signOut()}>{password ? "Sign out" : "Forget key on this browser"}</MenuItem>
              </div>
            )}
          </div>
        </div>
        <IndexStrip className="hidden md:grid max-w-md border-t border-border" />
      </header>
      {state === "offline" && (
        <p className="px-4 py-2 text-sm bg-amber-50 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          Can't reach the server right now. Pages will fill in when it's back.
        </p>
      )}
      {changing ? (
        <ChangePassword onDone={() => setChanging(false)} onCancel={() => setChanging(false)} />
      ) : noAccess ? (
        <main className="p-6 text-sm space-y-2">
          <h1 className="text-xl font-semibold">No access to this page</h1>
          <p className="text-muted-foreground">Ask the owner to give you this page.</p>
          <Link className="underline" to="/">Go to your start page</Link>
        </main>
      ) : (
        <Outlet />
      )}
      <BottomNav allowed={allowed} current={pathname.startsWith("/account") ? "account" : current} accountLabel={me?.username ?? "Account"} />
    </div>
  );
}

function MenuItem({ icon: Icon, onClick, children }: { icon: typeof Sun; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" role="menuitem" onClick={onClick} className="w-full flex items-center gap-2 px-3 min-h-10 rounded hover:bg-muted text-start">
      <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
      {children}
    </button>
  );
}

/** "/" sends each person to the landing page they picked (or the first page they may see). */
export function Landing() {
  const { prefs, allowed } = useSession();
  const pages = menuPages(allowed, prefs);
  const want = APP_PAGES.find((p) => p.id === prefs.landing_page && pages.some((x) => x.id === p.id));
  return <Navigate to={(want ?? pages[0] ?? APP_PAGES[0]).to} replace />;
}
