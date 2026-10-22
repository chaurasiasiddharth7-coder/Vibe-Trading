import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { JobLostError, friendlyError, pollJob } from "../pollJob";

const apiMock = vi.hoisted(() => ({ getBoardJob: vi.fn() }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

const running = (done: number) => ({ job: { status: "running", done, total: 10, result: null, error: null } });
const done = { job: { status: "done", done: 10, total: 10, result: { rows: 1 }, error: null } };
const instant = () => Promise.resolve();

describe("pollJob", () => {
  it("survives a few dropped polls and finishes", async () => {
    apiMock.getBoardJob
      .mockResolvedValueOnce(running(3))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 502", 502))
      .mockResolvedValueOnce(done);
    const progress = vi.fn();
    await expect(pollJob("j", progress, () => true, instant)).resolves.toEqual({ rows: 1 });
    expect(progress).toHaveBeenCalledWith({ done: 3, total: 10 });
  });

  it("gives up after too many failures in a row", async () => {
    apiMock.getBoardJob.mockReset().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(pollJob("j", vi.fn(), () => true, instant)).rejects.toBeInstanceOf(TypeError);
    expect(apiMock.getBoardJob).toHaveBeenCalledTimes(9);
  });

  it("reports a job the server forgot, and does not retry auth or client errors", async () => {
    apiMock.getBoardJob.mockReset().mockRejectedValueOnce(new ApiError("HTTP 404", 404));
    await expect(pollJob("j", vi.fn(), () => true, instant)).rejects.toBeInstanceOf(JobLostError);
    apiMock.getBoardJob.mockReset().mockRejectedValueOnce(new ApiError("HTTP 401", 401));
    await expect(pollJob("j", vi.fn(), () => true, instant)).rejects.toMatchObject({ status: 401 });
    expect(apiMock.getBoardJob).toHaveBeenCalledTimes(1);
  });

  it("stops quietly when the page moved on, and surfaces a failed scan", async () => {
    apiMock.getBoardJob.mockReset();
    await expect(pollJob("j", vi.fn(), () => false, instant)).resolves.toBeNull();
    apiMock.getBoardJob.mockResolvedValueOnce({ job: { status: "failed", done: 0, total: 1, result: null, error: "boom" } });
    await expect(pollJob("j", vi.fn(), () => true, instant)).rejects.toThrow("boom");
  });
});

describe("friendlyError", () => {
  it("replaces the raw browser message", () => {
    expect(friendlyError(new TypeError("Failed to fetch"))).toMatch(/Couldn't reach the server/);
    expect(friendlyError(new JobLostError())).toMatch(/restarted/);
    expect(friendlyError(new Error("plain"))).toBe("plain");
  });
});
