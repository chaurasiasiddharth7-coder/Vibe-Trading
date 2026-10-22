import { ApiError, api } from "@/lib/api";

export type Progress = { done: number; total: number } | null;

/** Thrown when the server no longer knows the job (it restarted while the scan ran). */
export class JobLostError extends Error {
  constructor() {
    super("The server restarted during the check.");
    this.name = "JobLostError";
  }
}

const MAX_BLIPS = 8;

/** A dropped connection or a 5xx from the proxy while the server is busy or restarting. */
function isBlip(e: unknown): boolean {
  if (e instanceof ApiError) return e.status >= 500;
  return e instanceof TypeError; // fetch() rejects with TypeError when the network fails
}

/**
 * Poll a background scan. A few failed polls in a row are retried (with a growing pause)
 * instead of aborting the whole check, because the server may be restarting or the
 * connection may blip while a long scan runs.
 */
export async function pollJob<T>(
  jobId: string,
  onProgress: (p: Progress) => void,
  alive: () => boolean,
  pause: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<T | null> {
  let blips = 0;
  for (;;) {
    await pause(1500 + blips * 1500);
    if (!alive()) return null;
    try {
      const { job } = await api.getBoardJob<T>(jobId);
      blips = 0;
      onProgress({ done: job.done, total: job.total });
      if (job.status === "done") return job.result;
      if (job.status === "failed") throw new Error(job.error ?? "scan failed");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) throw new JobLostError();
      if (!isBlip(e) || ++blips > MAX_BLIPS) throw e;
    }
  }
}

/** Plain words for errors that would otherwise show as "Failed to fetch". */
export function friendlyError(e: unknown): string {
  if (e instanceof TypeError) return "Couldn't reach the server. Check your connection, then press Check prices again.";
  if (e instanceof JobLostError) return e.message + " Press Check prices again.";
  return e instanceof Error ? e.message : String(e);
}
