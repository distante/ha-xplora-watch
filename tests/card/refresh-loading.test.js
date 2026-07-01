import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MIN_REFRESH_SPIN_MS } from "../../custom_components/xplora_watch/www/xplora-watch-card.js";
import { loadBundle } from "./helpers.js";

// Loading indicator on the auto-refresh-on-render path (issue #16). When the user enables
// "refresh on card render", a card refreshes its data on first show; while that on-render refresh is
// in flight the card must surface a spinner and clear it once the shared `trackInflight` handle
// settles -- silently on failure (the coordinator's fail-loud outcome surfaces separately). Fake
// timers drive the min-visible floor without real waits; every test uses a unique watch id so the
// module-level dedup registry can't leak between tests.

beforeAll(async () => {
  await loadBundle();
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ overview */

// An overview watch: a bound alarm list sensor (drives the functions refresh) + an `_update` button
// (drives the location refresh) + a `last_update` sensor (drives the resolved status chip). `tag`
// makes the dedup keys unique per test. `callService` captures + can delay the `button.press` call
// so a test can prove the indicator waits for the SLOWER of the two refreshes.
function makeOverviewHass({ tag, lastUpdate = "ok", callService } = {}) {
  const PRIMARY = "sensor.watch_alarms";
  const BTN = `button.${tag}_update`;
  const LU = "sensor.watch_last_update";
  const DEV = `dev-${tag}`;
  return {
    states: {
      [PRIMARY]: {
        entity_id: PRIMARY,
        state: "2",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {
          entry_id: `e-${tag}`,
          wuid: `w-${tag}`,
          friendly_name: "Dana",
          alarm: [{ id: "a1", status: "ENABLE", start: "07:00", weekdays: ["mon"] }],
          refresh_on_card_render: true,
        },
      },
      [BTN]: { entity_id: BTN, state: "2026-06-27T10:00:00Z", attributes: {} },
      [LU]: {
        entity_id: LU,
        state: lastUpdate,
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {},
      },
    },
    entities: {
      [PRIMARY]: { entity_id: PRIMARY, device_id: DEV },
      [BTN]: { entity_id: BTN, device_id: DEV },
      [LU]: { entity_id: LU, device_id: DEV },
    },
    devices: { [DEV]: { name: "Dana" } },
    locale: { language: "en" },
    callService: callService || (async () => {}),
  };
}

function mountOverview(hass) {
  const el = document.createElement("xplora-watch-overview-card");
  el.setConfig({ entity: "sensor.watch_alarms" });
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

describe("overview card — auto-refresh loading indicator", () => {
  it("shows an Updating… spinner in the status row while refreshing and keeps current data visible", () => {
    const el = mountOverview(makeOverviewHass({ tag: "ov-show" }));
    const upd = el.shadowRoot.querySelector(".upd.refreshing");
    expect(upd).toBeTruthy();
    expect(upd.textContent).toContain("Updating");
    expect(upd.querySelector("ha-icon.spin")).toBeTruthy();
    // Non-destructive: the watch's tiles are still rendered beneath the spinner.
    expect(el.shadowRoot.querySelector("[data-card]")).toBeTruthy();
  });

  it("clears the spinner and shows the resolved last-update chip once both refreshes settle", async () => {
    const el = mountOverview(makeOverviewHass({ tag: "ov-clear", lastUpdate: "ok" }));
    expect(el.shadowRoot.querySelector(".upd.refreshing")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
    expect(el.shadowRoot.querySelector(".upd.refreshing")).toBeNull();
    // The persisted `last_update` chip (state "ok" -> success) reappears in its place.
    expect(el.shadowRoot.querySelector(".upd.success")).toBeTruthy();
  });

  it("keeps the spinner until the SLOWER of the functions + location refreshes settles", async () => {
    // The location refresh (button.press) resolves well after the min-floor; the indicator must
    // stay until BOTH settle, not clear when the faster functions refresh does.
    const calls = [];
    const el = mountOverview(
      makeOverviewHass({
        tag: "ov-both",
        callService: (domain, service, data) => {
          calls.push([domain, service, data]);
          if (service === "press") return new Promise((r) => setTimeout(r, MIN_REFRESH_SPIN_MS * 4));
          return Promise.resolve();
        },
      }),
    );
    expect(el.shadowRoot.querySelector(".upd.refreshing")).toBeTruthy();
    // Past the floor: the functions refresh has settled but the location one has not -> still shown.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
    expect(el.shadowRoot.querySelector(".upd.refreshing")).toBeTruthy();
    // Once the slow location refresh settles too, the indicator clears.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS * 4);
    expect(el.shadowRoot.querySelector(".upd.refreshing")).toBeNull();
    expect(calls.map((c) => c[1]).sort()).toEqual(["press", "refresh_functions"]);
  });
});

/* -------------------------------------------------------------- alarm/silent */

function makeAlarmHass({ tag, callService } = {}) {
  const ENTITY = "sensor.watch_alarms";
  return {
    states: {
      [ENTITY]: {
        entity_id: ENTITY,
        state: "1",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {
          entry_id: `e-${tag}`,
          wuid: `w-${tag}`,
          friendly_name: "Dana",
          alarm: [{ id: "a1", status: "ENABLE", start: "07:00", weekdays: ["mon"] }],
          refresh_on_card_render: true,
        },
      },
    },
    locale: { language: "en" },
    callService: callService || (async () => {}),
  };
}

function mountAlarm(hass) {
  const el = document.createElement("xplora-watch-card");
  el.setConfig({ entity: "sensor.watch_alarms" });
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

const spinning = (el) => el.shadowRoot.querySelector(".refresh-btn ha-icon").classList.contains("spin");

describe("alarm/silent card — auto-refresh loading indicator", () => {
  it("drives the header refresh spinner from the on-render refresh and clears it once settled", async () => {
    const el = mountAlarm(makeAlarmHass({ tag: "al-show" }));
    expect(spinning(el)).toBe(true);
    // The min-visible floor holds the spinner even for an instant (cached) read.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS - 1);
    expect(spinning(el)).toBe(true);
    await vi.advanceTimersByTimeAsync(1);
    expect(spinning(el)).toBe(false);
  });

  it("clears the spinner silently (no toast) when the on-render refresh fails", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const toasts = [];
    const onNotif = (e) => toasts.push(e);
    window.addEventListener("hass-notification", onNotif);
    try {
      const el = mountAlarm(makeAlarmHass({ tag: "al-fail", callService: () => Promise.reject(new Error("boom")) }));
      expect(spinning(el)).toBe(true);
      await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
      expect(spinning(el)).toBe(false); // cleared
      expect(toasts).toEqual([]); // silently -- no error toast
    } finally {
      window.removeEventListener("hass-notification", onNotif);
      errSpy.mockRestore();
    }
  });
});
