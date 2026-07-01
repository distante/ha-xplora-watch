import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MIN_REFRESH_SPIN_MS } from "../../custom_components/xplora_watch/www/xplora-watch-card.js";
import { loadBundle } from "./helpers.js";

// Cross-card in-sync loading (issue #17). When an overview card and an alarm/silent card for the
// SAME watch share one dashboard view, a single on-render refresh must drive BOTH cards' loading
// indicators: they spin together while the shared `trackInflight` run is live and clear together
// when it settles -- crucially INCLUDING the card whose call was deduplicated. The deduped card
// never fired the service call itself; it only joined the shared run via the in-flight registry, so
// it is the case a naive "only the firing card spins" design would silently break. Each test uses a
// unique `tag` so the module-level dedup registry can't leak across tests; fake timers drive the
// shared min-visible floor without real waits.

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

const ALARMS_ENTITY = "sensor.xw_alarms";

// One watch exposed as the entities both cards resolve against: an `_alarms` list sensor (the
// alarm/silent card binds to it; the overview discovers it as its "alarms" role and keys the shared
// functions refresh off it), an `_update` button (the overview's location refresh) and a
// `_last_update` sensor (the overview's status row). All three share one device so the overview's
// registry-based discovery groups them. The `_alarms` sensor carries the entry_id/wuid both cards
// build the `refresh_functions|<entry>|<wuid>` dedup key from -> the runs coalesce onto one.
function makeSharedHass({ tag, callService } = {}) {
  const BTN = `button.xw_${tag}_update`;
  const LU = "sensor.xw_last_update";
  const DEV = `dev-${tag}`;
  const attrs = {
    entry_id: `e-${tag}`,
    wuid: `w-${tag}`,
    friendly_name: "Dana",
    alarm: [{ id: "a1", status: "ENABLE", start: "07:00", weekdays: ["mon"] }],
    refresh_on_card_render: true,
  };
  return {
    states: {
      [ALARMS_ENTITY]: {
        entity_id: ALARMS_ENTITY,
        state: "2",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: attrs,
      },
      [BTN]: { entity_id: BTN, state: "2026-06-27T10:00:00Z", attributes: {} },
      [LU]: {
        entity_id: LU,
        state: "ok",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {},
      },
    },
    entities: {
      [ALARMS_ENTITY]: { entity_id: ALARMS_ENTITY, device_id: DEV },
      [BTN]: { entity_id: BTN, device_id: DEV },
      [LU]: { entity_id: LU, device_id: DEV },
    },
    devices: { [DEV]: { name: "Dana" } },
    locale: { language: "en" },
    callService: callService || (async () => {}),
  };
}

// Overview refreshing = the "Updating…" chip in the last-update status row.
const overviewSpinning = (el) => !!el.shadowRoot.querySelector(".upd.refreshing");
// Alarm/silent refreshing = the header refresh button's spinning icon.
const alarmSpinning = (el) => {
  const icon = el.shadowRoot.querySelector(".refresh-btn ha-icon");
  return !!icon && icon.classList.contains("spin");
};

// Mount both cards for the one watch WITHOUT hydrating them, so the test controls the order in which
// each receives `hass` -- i.e. which card FIRES the shared run and which JOINS it deduped.
function mountBoth() {
  const overview = document.createElement("xplora-watch-overview-card");
  overview.setConfig({ entity: ALARMS_ENTITY });
  document.body.appendChild(overview);

  const alarm = document.createElement("xplora-watch-card");
  alarm.setConfig({ entity: ALARMS_ENTITY });
  document.body.appendChild(alarm);

  return { overview, alarm };
}

// trackInflight defers the actual service call to a microtask (so `fn` can't throw into the caller);
// flush it -- without advancing the min-floor/watchdog timers -- before asserting on captured calls.
const flushMicrotasks = () => vi.advanceTimersByTimeAsync(0);

describe("cross-card in-sync loading (overview + alarm/silent, one watch)", () => {
  it("both cards spin during one shared on-render refresh; the functions call fires only once", async () => {
    const calls = [];
    const hass = makeSharedHass({
      tag: "xc-both",
      callService: (_domain, service) => {
        calls.push(service);
        return Promise.resolve();
      },
    });
    const { overview, alarm } = mountBoth();
    // Overview receives hass first -> it FIRES the shared functions run (+ its own location press).
    overview.hass = hass;
    // The alarm/silent card then joins the SAME functions run deduped -- it fires no call of its own.
    alarm.hass = hass;

    // Both indicators are up synchronously -- the deduped card included.
    expect(overviewSpinning(overview)).toBe(true);
    expect(alarmSpinning(alarm)).toBe(true);

    await flushMicrotasks();
    // The functions refresh coalesced onto one run: exactly one call, fired by the overview. The
    // deduped alarm card is spinning off the SHARED run, not a call of its own.
    expect(calls.filter((s) => s === "refresh_functions")).toHaveLength(1);
    expect(calls.filter((s) => s === "press")).toHaveLength(1);
    // Still spinning after the microtask flush (the min-visible floor has not elapsed yet).
    expect(overviewSpinning(overview)).toBe(true);
    expect(alarmSpinning(alarm)).toBe(true);
  });

  it("both indicators clear together once the shared refresh settles -- including the deduped card", async () => {
    const hass = makeSharedHass({ tag: "xc-clear" });
    const { overview, alarm } = mountBoth();
    overview.hass = hass; // fires the shared functions run + location press
    alarm.hass = hass; // joins the functions run deduped

    expect(overviewSpinning(overview)).toBe(true);
    expect(alarmSpinning(alarm)).toBe(true);

    // Both runs settle instantly, but the shared min-visible floor holds the spinners until
    // MIN_REFRESH_SPIN_MS; only once it elapses do BOTH cards clear -- in sync, off the one run.
    await vi.advanceTimersByTimeAsync(MIN_REFRESH_SPIN_MS);
    expect(overviewSpinning(overview)).toBe(false);
    expect(alarmSpinning(alarm)).toBe(false);
  });
});
