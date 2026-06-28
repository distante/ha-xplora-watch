import { beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

// Map-track branch: when `ha-map` is registered, the popup builds an <ha-map> and feeds it a single
// path of {point:[lat,lng], timestamp:Date}. Kept in its own file so registering `ha-map` here can't
// leak into the list-fallback file (vitest isolates the custom-element registry per file).

const DEVICE = "dev1";
const HIST = "sensor.xplora_kid_watch_location_history";

function makeHass(attrPoints) {
  return {
    states: {
      [HIST]: {
        entity_id: HIST,
        state: String(attrPoints.length),
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: {
          entry_id: "entry1",
          wuid: "watch1",
          timezone: "UTC", // deterministic day keys in tests
          history_days: [],
          history_points: attrPoints,
          history_total_points: attrPoints.length,
          history_window_hours: 24,
          friendly_name: "Kid Watch Location History",
        },
      },
    },
    entities: { [HIST]: { entity_id: HIST, device_id: DEVICE } },
    devices: { [DEVICE]: { name: "Kid Watch" } },
    locale: { language: "en" },
    callService: async () => {},
  };
}

async function waitFor(fn, timeout = 1000) {
  const start = Date.now();
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() - start > timeout) throw new Error("waitFor timeout");
    await new Promise((r) => setTimeout(r, 5));
  }
}

beforeAll(async () => {
  // Register a stub `ha-map` so `_ensureHaMap()` short-circuits to true (it checks
  // customElements.get("ha-map") first, before touching loadCardHelpers).
  if (!customElements.get("ha-map")) {
    customElements.define(
      "ha-map",
      class extends HTMLElement {
        set paths(v) {
          this._paths = v;
        }
        get paths() {
          return this._paths;
        }
      },
    );
  }
  await loadBundle();
});

describe("history popup (map branch)", () => {
  it("renders an ha-map with a polyline path for the day (fallback to the bounded attribute)", async () => {
    // No callWS in this hass -> _fetchDay falls back to the bounded attribute points.
    const pts = [
      { tm: 1700000000000, lat: 52.52, lng: 13.405, addr: "A" },
      { tm: 1700000600000, lat: 52.53, lng: 13.41, addr: "B" },
    ];
    const el = document.createElement("xplora-watch-overview-card");
    el.setConfig({ device: DEVICE });
    document.body.appendChild(el);
    el.hass = makeHass(pts);

    el.shadowRoot.querySelector("[data-history]").click();
    const body = await waitFor(() => el.shadowRoot.querySelector(".hist-body[data-history-mode='map']"));

    expect(el.shadowRoot.querySelector(".hist-cal")).toBeTruthy();
    const map = body.querySelector("ha-map");
    expect(map).toBeTruthy();
    expect(map.autoFit).toBe(true);
    expect(typeof map.zoom).toBe("number"); // auto-fit zoom is capped
    expect(Array.isArray(map.paths)).toBe(true);
    const path = map.paths[0];
    expect(path.points.length).toBe(2);
    expect(path.points[0].point).toEqual([52.52, 13.405]);
    expect(path.points[0].timestamp instanceof Date).toBe(true);
  });
});
