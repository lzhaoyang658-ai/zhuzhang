import { describe, expect, it, vi } from "vitest";
import { createRecentLoginCoordinator } from "@/lib/reauth";

function deferred() {
  let resolve!: () => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<void>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe("recent-login coordinator", () => {
  it("shares one in-flight challenge across concurrent sensitive requests", async () => {
    const coordinator = createRecentLoginCoordinator();
    const challenge = deferred();
    const handler = vi.fn(() => challenge.promise);
    coordinator.registerRecentLoginHandler(handler);

    const first = coordinator.ensureRecentLogin(new Error("first request"));
    const second = coordinator.ensureRecentLogin(new Error("second request"));

    expect(second).toBe(first);
    await Promise.resolve();
    expect(handler).toHaveBeenCalledTimes(1);

    challenge.resolve();
    await expect(Promise.all([first, second])).resolves.toEqual([undefined, undefined]);
    expect(coordinator.recentLoginGeneration()).toBe(1);
  });

  it("advances the generation only after a successful challenge", async () => {
    const coordinator = createRecentLoginCoordinator();
    const handler = vi.fn().mockResolvedValue(undefined);
    coordinator.registerRecentLoginHandler(handler);

    expect(coordinator.recentLoginGeneration()).toBe(0);
    await coordinator.ensureRecentLogin(new Error("unavailable"));
    expect(coordinator.recentLoginGeneration()).toBe(1);
    await coordinator.ensureRecentLogin(new Error("unavailable"));
    expect(coordinator.recentLoginGeneration()).toBe(2);
  });

  it("clears a failed attempt so a later request can retry", async () => {
    const coordinator = createRecentLoginCoordinator();
    const failure = new Error("wrong code");
    const handler = vi.fn()
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce(undefined);
    coordinator.registerRecentLoginHandler(handler);

    await expect(coordinator.ensureRecentLogin(new Error("unavailable"))).rejects.toBe(failure);
    expect(coordinator.recentLoginGeneration()).toBe(0);

    await expect(coordinator.ensureRecentLogin(new Error("unavailable"))).resolves.toBeUndefined();
    expect(handler).toHaveBeenCalledTimes(2);
    expect(coordinator.recentLoginGeneration()).toBe(1);
  });

  it("reports the caller error when no dialog handler is mounted", async () => {
    const coordinator = createRecentLoginCoordinator();
    const unavailable = new Error("dialog unavailable");

    await expect(coordinator.ensureRecentLogin(unavailable)).rejects.toBe(unavailable);
    expect(coordinator.recentLoginGeneration()).toBe(0);
  });
});
