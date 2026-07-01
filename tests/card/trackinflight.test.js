import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MIN_REFRESH_SPIN_MS,
  REFRESH_WATCHDOG_MS,
  markRefreshed,
  trackInflight,
} from "../../custom_components/xplora_watch/www/xplora-watch-card.js";

// Direct unit tests for the `trackInflight(key, fn)` primitive that backs the cards' on-render
// auto-refresh. It de-duplicates concurrent runs for a key, exposes a shared awaitable promise, and
// enforces a minimum visible duration + a watchdog so a never-settling call can't wedge downstream
// loading state. Every test uses a distinct `key` so the module-level registry can't leak between
// them. Fake timers drive the min-floor / watchdog without real waits.

beforeAll(async () => {
  // Registers the custom elements as a side effect; also gives us the module's live registry so
  // markRefreshed/trackInflight share the same Map the cards use.
  await import("../../custom_components/xplora_watch/www/xplora-watch-card.js");
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

// Flush the microtask that trackInflight uses to defer `fn` (it wraps the call in a resolved
// promise so it can't throw into the caller) without advancing the min-floor/watchdog timers.
const flushMicrotasks = () => vi.advanceTimersByTimeAsync(0);

describe("trackInflight — sharing & suppression", () => {
  it("shares one in-flight run across concurrent callers for the same key", async () => {
    let fired = 0;
    const fn = () => {
      fired++;
      return new Promise(() => {}); // stays pending: the run is still in flight
    };
    const h1 = trackInflight("share-key", fn);
    const h2 = trackInflight("share-key", fn);

    expect(h1.inflight).toBe(true);
    expect(h2.inflight).toBe(true);
    expect(h2.promise).toBe(h1.promise); // same shared promise, not a second run

    await flushMicrotasks();
    expect(fired).toBe(1); // the concurrent caller did NOT re-fire fn
  });

  it("returns inflight:false for a caller arriving after the run settled but within TTL", async () => {
    let fired = 0;
    const fn = () => {
      fired++;
      return Promise.resolve();
    };
    const h1 = trackInflight("settled-key", fn);
    expect(h1.inflight).toBe(true);

    // Let the first run settle (past the min-floor) and clear from the in-flight registry.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
    await h1.promise;

    // A later caller inside the dedup TTL is suppressed: nothing live to await -> no loading.
    const h2 = trackInflight("settled-key", fn);
    expect(h2.inflight).toBe(false);

    await flushMicrotasks();
    expect(fired).toBe(1); // still only the original fire
  });

  it("markRefreshed suppresses a subsequent trackInflight for the same key", async () => {
    let fired = 0;
    const fn = () => {
      fired++;
      return Promise.resolve();
    };
    // Records the dedup window against the shared registry without firing (explicit-tap path).
    markRefreshed("marked-key");

    const h = trackInflight("marked-key", fn);
    expect(h.inflight).toBe(false);

    await flushMicrotasks();
    expect(fired).toBe(0); // suppressed by markRefreshed's window
  });
});

describe("trackInflight — lifecycle floor & watchdog", () => {
  it("holds the shared promise for at least the minimum visible duration", async () => {
    const fn = () => Promise.resolve(); // settles instantly
    const h = trackInflight("floor-key", fn);
    let resolved = false;
    h.promise.then(() => {
      resolved = true;
    });

    // Just before the floor elapses the shared promise is still pending.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS - 1);
    expect(resolved).toBe(false);

    // The floor is the primitive's job -> it resolves exactly at MIN_REFRESH_SPIN_MS.
    await vi.advanceTimersByTimeAsync(1);
    expect(resolved).toBe(true);
  });

  it("clears the shared promise via the watchdog when the run never settles", async () => {
    const fn = () => new Promise(() => {}); // never settles
    const h = trackInflight("watchdog-key", fn);
    let resolved = false;
    h.promise.then(() => {
      resolved = true;
    });

    await vi.advanceTimersByTimeAsync(REFRESH_WATCHDOG_MS - 1);
    expect(resolved).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(resolved).toBe(true); // watchdog force-cleared it
  });

  it("resolves (never rejects) when the run rejects", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const fn = () => Promise.reject(new Error("boom"));
    const h = trackInflight("reject-key", fn);

    // The shared promise resolves so downstream loading clears; the error is swallowed + logged.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
    await expect(h.promise).resolves.toBeUndefined();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
