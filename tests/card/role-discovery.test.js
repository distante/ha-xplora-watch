import { beforeAll, describe, expect, it } from "vitest";

import { loadBundle } from "./helpers.js";

// The overview card discovers a watch's entities by their integration-emitted `xplora_role` state
// attribute keyed on (entity domain, role) -- NOT by parsing the entity_id (ADR 0005). These tests
// pin the two guarantees that string-suffix matching could not make:
//   1. Role discovery is independent of the entity_id: an id a user has renamed to something with no
//      role word still resolves, because only the attribute decides the role.
//   2. `safezone` is emitted by BOTH a binary_sensor (the in/out tile) and device_tracker per-zone
//      entities; the domain scoping means the tile reads the binary_sensor, not a device_tracker.
// Teeth: break `roleOf` (return undefined) or drop the domain from the ROLE_BY_DOMAIN_ROLE key and
// these go red (blank status / wrong safezone value) -- assertion failures, not import errors.

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

// A registry entry + state pair for one entity, tagged with its integration role.
function ent(states, entities, entityId, role, state) {
  states[entityId] = { entity_id: entityId, state, last_changed: "2026-06-27T09:00:00Z", last_updated: "2026-06-27T10:00:00Z", attributes: { xplora_role: role } };
  entities[entityId] = { entity_id: entityId, device_id: DEV };
}

beforeAll(async () => {
  await loadBundle();
});

describe("overview role discovery (attribute-based, ADR 0005)", () => {
  it("resolves roles from xplora_role even when the entity_id has been renamed to have no role word", () => {
    const states = {};
    const entities = {};
    // Ids a user renamed to arbitrary strings -- none ends in (or even contains) its role word.
    ent(states, entities, "sensor.grandmas_gift", "battery", "80");
    ent(states, entities, "binary_sensor.by_the_door", "state", "on");

    const el = mount(states, entities);

    // The battery sensor was found purely via its role attribute -> its % renders in the status row.
    const batt = el.shadowRoot.querySelector(".batt");
    expect(batt).toBeTruthy();
    expect(batt.textContent).toContain("80%");
    // The online binary_sensor was found the same way -> "Online" shows.
    expect(el.shadowRoot.textContent).toContain("Online");
  });

  it("the safezone tile reads the binary_sensor, not a device_tracker that shares the safezone role", () => {
    const states = {};
    const entities = {};
    // Both carry xplora_role "safezone"; only the binary_sensor is the in/out tile source. A role-only
    // (non-domain-scoped) match could let the device_tracker shadow it and show the wrong value.
    ent(states, entities, "binary_sensor.zone_flag", "safezone", "on"); // on -> "Inside"
    ent(states, entities, "device_tracker.zone_home", "safezone", "not_home");

    const el = mount(states, entities);

    const tileValues = [...el.shadowRoot.querySelectorAll(".tile-value")].map((t) => t.textContent);
    expect(tileValues).toContain("Inside"); // from the binary_sensor "on"
    expect(tileValues).not.toContain("Outside"); // the device_tracker did NOT shadow it
  });
});
