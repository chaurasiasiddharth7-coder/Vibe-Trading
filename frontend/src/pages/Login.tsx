import { useState, type FormEvent } from "react";
import { BarChart3 } from "lucide-react";
import { desk } from "@/lib/deskApi";
import { setApiAuthKey } from "@/lib/apiAuth";

const field = "w-full min-h-11 rounded-md border border-border bg-background px-3 text-sm";

/** Sign in with a username and password. A connect key still works for scripts and old links. */
export function Login({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [keyMode, setKeyMode] = useState(false);
  const [key, setKey] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await desk.login(username.trim(), password);
      // A key saved earlier in this browser would win over the new sign-in (the server prefers the key header).
      setApiAuthKey("");
      setPassword("");
      onDone();
    } catch (err) {
      setError(err instanceof Error && !/needs your key/.test(err.message) ? err.message : "Wrong username or password.");
    } finally {
      setBusy(false);
    }
  };

  const useKey = (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setApiAuthKey(key);
    window.location.reload();
  };

  return (
    <main className="min-h-screen grid place-items-center p-6 bg-background">
      <div className="w-full max-w-sm space-y-5">
        <div className="flex items-center gap-2 text-lg font-semibold">
          <BarChart3 className="h-5 w-5 text-primary" aria-hidden />
          Vibe Desk
        </div>
        {!keyMode ? (
          <form onSubmit={submit} className="space-y-3" aria-label="Sign in">
            <h1 className="text-xl font-semibold">Sign in</h1>
            <label className="block text-sm space-y-1">
              <span>Username</span>
              <input className={field} autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </label>
            <label className="block text-sm space-y-1">
              <span>Password</span>
              <input className={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && (
              <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy} className="w-full min-h-11 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60">
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="text-xs text-muted-foreground">
              Forgot your password? Ask the owner to reset it. Five wrong tries lock the account for 15 minutes.
            </p>
            <button type="button" onClick={() => setKeyMode(true)} className="text-xs underline text-muted-foreground">
              I have a connect key instead
            </button>
          </form>
        ) : (
          <form onSubmit={useKey} className="space-y-3" aria-label="Use a connect key">
            <h1 className="text-xl font-semibold">Use a connect key</h1>
            <input className={field} type="password" placeholder="Paste the key" value={key} onChange={(e) => setKey(e.target.value)} />
            <button type="submit" className="w-full min-h-11 rounded-md bg-emerald-600 text-white text-sm font-semibold">
              Connect
            </button>
            <button type="button" onClick={() => setKeyMode(false)} className="text-xs underline text-muted-foreground">
              Back to sign in
            </button>
          </form>
        )}
        <p className="text-xs text-muted-foreground">Practice desk: every trade here uses fake money.</p>
      </div>
    </main>
  );
}

/** First sign-in, or after a reset: the one-time password must be replaced. */
export function ChangePassword({ onDone, forced, onCancel }: { onDone: () => void; forced?: boolean; onCancel?: () => void }) {
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== again) return setError("The two new passwords don't match.");
    if (next.length < 12) return setError("Use at least 12 characters.");
    setBusy(true);
    setError("");
    try {
      await desk.changePassword(old, next);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't change it.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-[70vh] grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-3" aria-label="Change password">
        <h1 className="text-xl font-semibold">{forced ? "Pick your own password" : "Change password"}</h1>
        {forced && <p className="text-sm text-muted-foreground">You signed in with a one-time password. Replace it to continue.</p>}
        <label className="block text-sm space-y-1">
          <span>{forced ? "One-time password" : "Current password"}</span>
          <input className={field} type="password" autoComplete="current-password" value={old} onChange={(e) => setOld(e.target.value)} required />
        </label>
        <label className="block text-sm space-y-1">
          <span>New password (12+ characters)</span>
          <input className={field} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label className="block text-sm space-y-1">
          <span>New password again</span>
          <input className={field} type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        </label>
        {error && (
          <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <button type="submit" disabled={busy} className="flex-1 min-h-11 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-60">
            {busy ? "Saving…" : "Save password"}
          </button>
          {onCancel && (
            <button type="button" onClick={onCancel} className="min-h-11 px-4 rounded-md border border-border text-sm">
              Cancel
            </button>
          )}
        </div>
      </form>
    </main>
  );
}
