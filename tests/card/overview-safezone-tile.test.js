import { beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

// The overview card's safe-zone tile merges TWO entities (ADR 0006):
//   - binary_sensor role "safezone" decides in/out. It is a SAFETY alert: "on" == OUTSIDE every
//     safezone. (The tile used to map on -> "Inside" -- the inverted-tile bug this pins.)
//   - sensor role "current_safezone" (card role "safezone_label") names the watch-reported zone
//     while inside; unknown means "inside, but no zone name known" -- NOT an error.

const DEV = "dev1";

function mount(states, entities) {
  const el = document.createElement("xplora-watch-overview-card");
  el.setConfig({ device: DEV });
  document.body.appendChild(el);
  el.hass = {
    states,
    entities,
    devices: { [DEV]: { name: "Kid Watch" } },
    locale: { language: "en" },
    callService: async () => {},
  };
  return el;
}

function ent(states, entities, entityId, role, state) {
  states[entityId] = {
    entity_id: entityId,
    state,
    last_changed: "2026-07-07T09:00:00Z",
    last_updated: "2026-07-07T10:00:00Z",
    attributes: { xplora_role: role },
  };
  entities[entityId] = { entity_id: entityId, device_id: DEV };
}

function tileValues(el) {
  return [...el.shadowRoot.querySelectorAll(".tile-value")].map((t) => t.textContent);
}

beforeAll(async () => {
  await loadBundle();
});

describe("overview safe-zone tile (binary decides in/out, label sensor names the zone)", () => {
  it('shows "Outside" when the safety binary sensor is on (on == outside-alert)', () => {
    const states = {};
    const entities = {};
    ent(states, entities, "binary_sensor.zone_flag", "safezone", "on");

    const el = mount(states, entities);

    expect(tileValues(el)).toContain("Outside");
    expect(tileValues(el)).not.toContain("Inside");
  });

  it("shows the watch-reported zone name when inside and the label sensor knows it", () => {
    const states = {};
    const entities = {};
    ent(states, entities, "binary_sensor.zone_flag", "safezone", "off");
    ent(states, entities, "sensor.zone_label", "current_safezone", "Grandma");

    const el = mount(states, entities);

    expect(tileValues(el)).toContain("Grandma");
    expect(tileValues(el)).not.toContain("Outside");
  });

  it('falls back to "Inside" when inside but the label sensor is unknown', () => {
    const states = {};
    const entities = {};
    ent(states, entities, "binary_sensor.zone_flag", "safezone", "off");
    // The label sensor exists but reports unknown (watch inside, no zone name known).
    ent(states, entities, "sensor.zone_label", "current_safezone", "unknown");

    const el = mount(states, entities);

    expect(tileValues(el)).toContain("Inside");
  });

  it('falls back to "Inside" when inside and no label sensor is enabled at all', () => {
    const states = {};
    const entities = {};
    ent(states, entities, "binary_sensor.zone_flag", "safezone", "off");

    const el = mount(states, entities);

    expect(tileValues(el)).toContain("Inside");
  });
});
