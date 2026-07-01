import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

beforeAll(async () => {
  await loadBundle();
});

// Targeting moved to HA devices (ADR 0003): the service resolves the bound entity -> its device
// server-side, so the card no longer needs the sensor's `wuid`/`entry_id` to fire. That made the
// old attribute-based "sensor not ready" check moot; readiness is now simply the bound entity
// having a usable state. These tests pin that gate: nothing fires while the entity is
// missing/unavailable/unknown, and it fires once the state is usable.

const ENTITY = "sensor.watch_alarms";

// A bound alarm sensor in the given `state`. `refreshOnRender` toggles the user's "refresh data when
// cards are shown" preference; a distinct `wuid` per test keeps the module-level refresh-dedup from
// suppressing a later fire. `calls` captures every callService invocation.
function makeHass(state, { refreshOnRender = false, wuid = "watch1", calls } = {}) {
  return {
    states: {
      [ENTITY]: {
        entity_id: ENTITY,
        state,
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {
          entry_id: "entry1",
          wuid,
          friendly_name: "Dana",
          alarm: [{ id: "a1", status: "ENABLE", start: "07:00", weekdays: ["mon"] }],
          refresh_on_card_render: refreshOnRender,
        },
      },
    },
    locale: { language: "en" },
    callService: async (...args) => calls.push(args),
  };
}

function mount(hass) {
  const el = document.createElement("xplora-watch-card");
  el.setConfig({ entity: ENTITY });
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

const q = (el, sel) => el.shadowRoot.querySelector(sel);
// The auto-refresh path fires through `trackInflight`, which defers the call to a microtask; flush
// it before asserting. (The explicit-button path calls `callService` synchronously, so it needs no
// tick.)
const tick = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => {
  document.body.innerHTML = "";
});

describe("card readiness — auto-refresh on render", () => {
  it("does not fire while the bound entity is unavailable", async () => {
    const calls = [];
    mount(makeHass("unavailable", { refreshOnRender: true, wuid: "w-unavail", calls }));
    await tick();
    expect(calls).toEqual([]);
  });

  it("does not fire while the bound entity is missing entirely", async () => {
    const calls = [];
    const el = document.createElement("xplora-watch-card");
    el.setConfig({ entity: ENTITY });
    document.body.appendChild(el);
    el.hass = { states: {}, locale: { language: "en" }, callService: async (...a) => calls.push(a) };
    await tick();
    expect(calls).toEqual([]);
  });

  it("fires exactly once when the entity transitions to a usable state", async () => {
    const calls = [];
    const el = mount(makeHass("unknown", { refreshOnRender: true, wuid: "w-wake", calls }));
    await tick();
    expect(calls).toEqual([]); // still asleep on "unknown"
    el.hass = makeHass("2", { refreshOnRender: true, wuid: "w-wake", calls });
    await tick();
    expect(calls.map((c) => c[1])).toEqual(["refresh_functions"]);
  });
});

describe("card readiness — explicit refresh button", () => {
  it("the header refresh button is a no-op while the entity is unavailable", () => {
    const calls = [];
    const el = mount(makeHass("unavailable", { wuid: "w-btn-unavail", calls }));
    q(el, '[data-act="refresh"]').click();
    expect(calls).toEqual([]);
  });

  it("the header refresh button fires once the entity is usable", () => {
    const calls = [];
    const el = mount(makeHass("2", { wuid: "w-btn-ok", calls }));
    q(el, '[data-act="refresh"]').click();
    expect(calls.map((c) => c[1])).toEqual(["refresh_functions"]);
  });
});
