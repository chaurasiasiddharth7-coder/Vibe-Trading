import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Copy, X } from "lucide-react";
import { desk, type DeskUser } from "@/lib/deskApi";
import { APP_PAGES, useSession } from "@/lib/session";
import { Page, PageHeader, Tiles, Note, Empty } from "@/components/shell/ui";
import { cn } from "@/lib/utils";

const GRANTABLE = APP_PAGES.filter((p) => p.id !== "users");
const field = "w-full min-h-11 rounded-md border border-border bg-background px-3 text-sm";
const when = (ts: number | null) =>
  ts ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(new Date(ts * 1000)) : "Never";

/** Owner only: who can sign in, and what each person can see. Passwords are never shown, only reset. */
export function UsersPage() {
  const { prefs } = useSession();
  const [list, setList] = useState<DeskUser[]>([]);
  const [counts, setCounts] = useState({ active: 0, signed_in_today: 0, locked: 0 });
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<null | "add" | DeskUser>(null);
  const [secret, setSecret] = useState<{ who: string; password: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await desk.users();
      setList(res.data.users);
      setCounts(res.data.counts);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load users.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    }
  };

  const reset = (u: DeskUser) =>
    act(async () => {
      const res = await desk.resetUser(u.id);
      setSecret({ who: u.username, password: res.data.one_time_password });
    });

  return (
    <Page>
      <PageHeader
        title="Users"
        sub="People who can sign in. Each one gets a private practice desk. Only you can see this page."
        right={
          <button type="button" onClick={() => setPanel("add")} className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold">
            Add user
          </button>
        }
      />
      <Tiles
        chosen={prefs.tiles_by_page.users}
        tiles={[
          { id: "active", label: "Active users", value: String(counts.active) },
          { id: "today", label: "Signed in today", value: String(counts.signed_in_today) },
          { id: "locked", label: "Locked", value: String(counts.locked), tone: counts.locked ? "text-amber-600" : undefined },
        ]}
      />
      {error && <Note tone="bad">{error}</Note>}
      {secret && <OneTimePassword who={secret.who} password={secret.password} onClose={() => setSecret(null)} />}
      <Note>
        Keep users to family or test accounts. Other people see only their own practice trades, never your Zerodha data, and
        charging others for these ideas needs SEBI registration.
      </Note>

      {list.length === 0 ? (
        <Empty>No users yet. Add one, or add yourself as an owner so you can sign in with a password.</Empty>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="text-start px-3 py-2">Name</th>
                <th className="text-start px-3 py-2">Role</th>
                <th className="text-start px-3 py-2">Pages</th>
                <th className="text-start px-3 py-2">Last sign-in</th>
                <th className="text-start px-3 py-2">Status</th>
                <th className="text-end px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {list.map((u) => (
                <tr key={u.id}>
                  <td className="px-3 py-2">
                    <div className="font-medium">{u.name}</div>
                    <div className="text-xs text-muted-foreground">{u.username}</div>
                  </td>
                  <td className="px-3 py-2 capitalize">{u.role}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground max-w-56">
                    {u.role === "owner" ? "All" : u.pages.map((p) => APP_PAGES.find((x) => x.id === p)?.label ?? p).join(", ")}
                  </td>
                  <td className="px-3 py-2 text-xs">{when(u.last_login_at)}</td>
                  <td className="px-3 py-2 text-xs">
                    <span
                      className={cn(
                        u.disabled ? "text-muted-foreground" : u.locked ? "text-amber-600" : u.must_change ? "text-sky-600" : "text-emerald-600",
                      )}
                    >
                      {u.disabled ? "Off" : u.locked ? "Locked" : u.must_change ? "Needs new password" : "Active"}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1 flex-wrap">
                      <RowButton onClick={() => setPanel(u)}>Edit</RowButton>
                      <RowButton onClick={() => void reset(u)}>Reset password</RowButton>
                      {u.locked && <RowButton onClick={() => void act(() => desk.userAction(u.id, "unlock"))}>Unlock</RowButton>}
                      <RowButton onClick={() => void act(() => desk.userAction(u.id, u.disabled ? "enable" : "disable"))}>
                        {u.disabled ? "Turn on" : "Turn off"}
                      </RowButton>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {panel && (
        <UserPanel
          user={panel === "add" ? null : panel}
          onClose={() => setPanel(null)}
          onSaved={async (pw, who) => {
            setPanel(null);
            if (pw) setSecret({ who, password: pw });
            await load();
          }}
        />
      )}
    </Page>
  );
}

function RowButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="min-h-9 px-2 rounded border border-border text-xs hover:bg-muted">
      {children}
    </button>
  );
}

function OneTimePassword({ who, password, onClose }: { who: string; password: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <section role="status" className="rounded-md border border-emerald-700/50 bg-emerald-50 dark:bg-emerald-950/30 p-3 space-y-2 text-sm">
      <div className="flex justify-between items-start gap-2">
        <p>
          One-time password for <strong>{who}</strong>. It shows only now. Send it privately; they must change it at first sign-in.
        </p>
        <button type="button" aria-label="Hide password" onClick={onClose} className="h-8 w-8 grid place-items-center rounded hover:bg-muted">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex gap-2 items-center">
        <code className="font-mono text-base px-2 py-1 rounded bg-background border border-border select-all">{password}</code>
        <button
          type="button"
          onClick={() => void navigator.clipboard.writeText(password).then(() => setCopied(true))}
          className="min-h-9 px-3 rounded border border-border inline-flex items-center gap-1 text-xs"
        >
          <Copy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </section>
  );
}

function UserPanel({ user, onClose, onSaved }: { user: DeskUser | null; onClose: () => void; onSaved: (pw: string | null, who: string) => void }) {
  const [username, setUsername] = useState(user?.username ?? "");
  const [name, setName] = useState(user?.name ?? "");
  const [role, setRole] = useState<DeskUser["role"]>(user?.role ?? "trader");
  const [pages, setPages] = useState<string[]>(user?.pages.filter((p) => p !== "users") ?? ["stocks", "portfolio", "suggestions"]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (user) {
        await desk.editUser(user.id, { name, role, pages });
        onSaved(null, user.username);
      } else {
        const res = await desk.addUser({ username, name, role, pages });
        onSaved(res.data.one_time_password, res.data.user.username);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" role="dialog" aria-modal="true" aria-label={user ? "Edit user" : "Add user"}>
      <form onSubmit={submit} className="w-full max-w-md h-full overflow-y-auto bg-card border-s border-border p-5 space-y-4">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">{user ? `Edit ${user.name}` : "Add user"}</h2>
          <button type="button" aria-label="Close" onClick={onClose} className="h-9 w-9 grid place-items-center rounded hover:bg-muted">
            <X className="h-4 w-4" />
          </button>
        </div>
        <label className="block text-sm space-y-1">
          <span>Name</span>
          <input className={field} value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} />
        </label>
        <label className="block text-sm space-y-1">
          <span>Username</span>
          <input
            className={field}
            value={username}
            onChange={(e) => setUsername(e.target.value.toLowerCase())}
            disabled={!!user}
            required
            pattern="[a-z][a-z0-9._-]{1,31}"
            title="2-32 lowercase letters, digits, dot, dash or underscore"
            autoCapitalize="none"
          />
        </label>
        <fieldset className="space-y-1 text-sm">
          <legend className="mb-1">Role</legend>
          {(
            [
              ["trader", "Trader: practice trades on their own desk"],
              ["viewer", "Viewer: can look, can't change anything"],
              ["owner", "Owner: everything, including this page"],
            ] as const
          ).map(([r, label]) => (
            <label key={r} className="flex items-center gap-2">
              <input type="radio" name="role" checked={role === r} onChange={() => setRole(r)} />
              {label}
            </label>
          ))}
        </fieldset>
        {role !== "owner" && (
          <fieldset className="space-y-1 text-sm">
            <legend className="mb-1">Pages they can open</legend>
            {GRANTABLE.map((p) => (
              <label key={p.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={pages.includes(p.id)}
                  onChange={() => setPages((cur) => (cur.includes(p.id) ? cur.filter((x) => x !== p.id) : [...cur, p.id]))}
                />
                {p.label}
              </label>
            ))}
          </fieldset>
        )}
        {!user && <p className="text-xs text-muted-foreground">A one-time password is made for you to send. They pick their own at first sign-in.</p>}
        {error && (
          <p role="alert" className="text-sm text-rose-600">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} className="w-full min-h-11 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60">
          {busy ? "Saving…" : user ? "Save" : "Add and make password"}
        </button>
      </form>
    </div>
  );
}
