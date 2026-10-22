import { useEffect, useState, type FormEvent } from "react";
import { api, isAuthRequiredError } from "@/lib/api";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";

/**
 * Shows a key box when the server says a key is required (hosted setup).
 * Renders nothing when the server is local or the stored key already works.
 */
export function ApiKeyGate() {
  const [needsKey, setNeedsKey] = useState(false);
  const [value, setValue] = useState("");
  const wrong = getApiAuthKey() !== "";

  useEffect(() => {
    let alive = true;
    api
      .getUniverses()
      .catch((e) => alive && isAuthRequiredError(e) && setNeedsKey(true));
    return () => {
      alive = false;
    };
  }, []);

  if (!needsKey) return null;

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setApiAuthKey(value);
    window.location.reload();
  };

  return (
    <section aria-label="Connect to your server" className="rounded-lg border border-amber-700/60 bg-amber-950/20 p-4 space-y-2">
      <h2 className="text-base font-semibold">Connect to your server</h2>
      <p className="text-sm text-muted-foreground">
        This dashboard is talking to a private server that needs your key. Paste the key once. It stays in this browser only.
      </p>
      {wrong && <p role="alert" className="text-sm text-rose-300">The saved key was not accepted. Paste the key again.</p>}
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
      </form>
    </section>
  );
}
