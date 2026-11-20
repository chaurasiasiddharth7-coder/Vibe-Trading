import { describe, expect, it, vi } from "vitest";
import { pollJob } from "@/lib/pollJob";

describe("pollJob phases", () => {
  it("passes the scan phase to the page so price ideas can show before the AI forecast", async () => {
    const api = await import("@/lib/api");
    const jobs = [
      { id: "j", kind: "scan", status: "running", done: 200, total: 200, phase: "prices", result: null, error: null },
      { id: "j", kind: "scan", status: "running", done: 10, total: 200, phase: "forecast", result: null, error: null },
      { id: "j", kind: "scan", status: "done", done: 200, total: 200, phase: "forecast", result: { ok: 1 }, error: null },
    ];
    vi.spyOn(api.api, "getBoardJob").mockImplementation(async () => ({ ok: true, job: jobs.shift() as never }));
    const seen: Array<string | undefined> = [];
    const res = await pollJob("j", (p) => seen.push(p?.phase), () => true, async () => {});
    expect(res).toEqual({ ok: 1 });
    expect(seen).toEqual(["prices", "forecast", "forecast"]);
  });
});
