import { useEffect, useState, type FormEvent } from "react";
import { api, isAuthRequiredError } from "@/lib/api";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";

/**
 * A connect link looks like `/dashboard#key=...`. The part after `#` is never sent to a server.
 * Store the key, remove it from the address bar, and report whether a key was taken.
 */
export function takeKeyFromHash(): boolean {
  const m = window.location.hash.match(/^#key=([^&]+)/);
  if (!m) return false;
  try {
    setApiAuthKey(decodeURIComponent(m[1]));
  } finally {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return true;
}

/**
 * Shows a key box when the server says a key is required (hosted setup).
 * Renders nothing when the server is local or the stored key already works.
 */
export function ApiKeyGate({ onNeedsKey }: { onNeedsKey?: (needs: boolean) => void }) {
  const [needsKey, setNeedsKey] = useState(false);
  const [value, setValue] = useState("");
  const [clipError, setClipError] = useState(false);
  const wrong = getApiAuthKey() !== "";

  useEffect(() => {
    if (takeKeyFromHash()) {
      window.location.reload();
      return;
    }
    let alive = true;
    api.getUniverses().catch((e) => {
      if (alive && isAuthRequiredError(e)) {
        setNeedsKey(true);
        onNeedsKey?.(true);
      }
    });
    return () => {
      alive = false;
    };
  }, [onNeedsKey]);

  if (!needsKey) return null;

  const store = (key: string) => {
    if (!key.trim()) return;
    setApiAuthKey(key);
    window.location.reload();
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    store(value);
  };

  const paste = async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (text) store(text);
      else setClipError(true);
    } catch {
      setClipError(true);
    }
  };

  return (
    <section aria-label="Connect to your server" className="rounded-lg border border-amber-700/60 bg-amber-950/20 p-4 space-y-2">
      <h2 className="text-base font-semibold">Connect to your server</h2>
      <p className="text-sm text-muted-foreground">
        This dashboard talks to your private server, which needs your key. Paste it once. It stays in this browser only.
      </p>
      {wrong && <p role="alert" className="text-sm text-rose-300">The saved key was not accepted. Paste the key again.</p>}
      {clipError && (
        <p role="alert" className="text-sm text-amber-300">
          Couldn't read the clipboard. Paste the key into the box instead.
        </p>
      )}
      <form onSubmit={save} className="flex flex-wrap gap-2">
        <label className="sr-only" htmlFor="api-key">
          Server key
        </label>
        <input
          id="api-key"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Server key"
          className="h-10 w-80 max-w-full rounded-md border border-border bg-background px-3 font-mono text-sm"
        />
        <button
          type="submit"
          disabled={!value.trim()}
          className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-500 disabled:opacity-50"
        >
          Save and reload
        </button>
        <button type="button" onClick={() => void paste()} className="min-h-10 px-4 rounded-md border border-border text-sm hover:bg-muted">
          Paste from clipboard
        </button>
      </form>
    </section>
  );
}
