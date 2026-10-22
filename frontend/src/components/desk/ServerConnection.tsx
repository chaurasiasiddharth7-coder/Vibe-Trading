import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";

type Status = "checking" | "connected" | "refused" | "unreachable";

/** Hosted-site settings: which server this page talks to, whether the key works, and how to change or forget it. */
export function ServerConnection() {
  const [status, setStatus] = useState<Status>("checking");
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const key = getApiAuthKey();

  useEffect(() => {
    let alive = true;
    api
      .getUniverses()
      .then(() => alive && setStatus("connected"))
      .catch((e: unknown) => {
        const code = (e as { status?: number })?.status;
        if (alive) setStatus(code === 401 || code === 403 ? "refused" : "unreachable");
      });
    return () => {
      alive = false;
    };
  }, []);

  const label: Record<Status, string> = {
    checking: "Checking…",
    connected: "Connected. Your key works.",
    refused: key ? "The server refused the saved key." : "No key saved yet.",
    unreachable: "Couldn't reach the server. Try again in a minute.",
  };

  return (
    <section aria-label="Server connection" className="rounded-lg border border-border p-4 space-y-3">
      <h2 className="text-base font-semibold">Server connection</h2>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Status</dt>
          <dd role="status">{label[status]}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Saved key</dt>
          <dd className="font-mono">{key ? `••••${key.slice(-4)}` : "none"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Kept in</dt>
          <dd>This browser only</dd>
        </div>
      </dl>
      {editing ? (
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!value.trim()) return;
            setApiAuthKey(value);
            window.location.reload();
          }}
        >
          <label className="sr-only" htmlFor="new-key">
            New server key
          </label>
          <input
            id="new-key"
            type="password"
            autoComplete="off"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="New server key"
            className="h-10 w-80 max-w-full rounded-md border border-border bg-background px-3 font-mono text-sm"
          />
          <button type="submit" disabled={!value.trim()} className="min-h-10 px-4 rounded-md bg-emerald-600 text-white text-sm font-semibold disabled:opacity-50">
            Save and reload
          </button>
          <button type="button" onClick={() => setEditing(false)} className="min-h-10 px-4 rounded-md text-sm hover:bg-muted">
            Cancel
          </button>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setEditing(true)} className="min-h-10 px-4 rounded-md border border-border text-sm hover:bg-muted">
            Change key
          </button>
          {key && (
            <button
              type="button"
              onClick={() => {
                setApiAuthKey("");
                window.location.reload();
              }}
              className="min-h-10 px-4 rounded-md border border-border text-sm hover:bg-muted"
            >
              Forget key on this device
            </button>
          )}
        </div>
      )}
    </section>
  );
}
