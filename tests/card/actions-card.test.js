import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { loadBundle } from "./helpers.js";

beforeAll(async () => {
  await loadBundle();
});

// The "update" action awaits the backend last_update sensor (a 250ms poll loop up to 5s). Fake
// timers keep that loop from running for real -- the callService we assert on is issued *before* the
// first await, so it's already recorded by the time the click handler returns.
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

const ENTITIES = ["button.kid_update", "button.kid_refresh_functions", "button.kid_reboot"];

function mountActions(calls) {
  const el = document.createElement("xplora-watch-actions-card");
  el.setConfig({ entities: ENTITIES });
  document.body.appendChild(el);
  el.hass = {
    states: Object.fromEntries(
      ENTITIES.map((id) => [id, { state: "unknown", attributes: { friendly_name: id } }]),
    ),
    entities: {},
    locale: { language: "en" },
    callService: async (...args) => calls.push(args),
  };
  return el;
}

const pressed = (calls) => calls.filter((c) => c[0] === "button" && c[1] === "press").map((c) => c[2].entity_id);

describe("actions card button -> service call", () => {
  it("the Update button presses its button entity (no confirm)", () => {
    vi.useFakeTimers();
    const calls = [];
    const el = mountActions(calls);

    el.shadowRoot.querySelector('[data-entity="button.kid_update"]').click();

    expect(calls).toContainEqual(["button", "press", { entity_id: "button.kid_update" }, undefined, false]);
  });

  it("the Refresh-functions button presses immediately (no confirm)", () => {
    vi.useFakeTimers();
    const calls = [];
    const el = mountActions(calls);

    el.shadowRoot.querySelector('[data-entity="button.kid_refresh_functions"]').click();

    expect(pressed(calls)).toEqual(["button.kid_refresh_functions"]);
  });

  it("a destructive action (Restart) presses only after the confirm dialog", () => {
    vi.useFakeTimers();
    const calls = [];
    const el = mountActions(calls);

    el.shadowRoot.querySelector('[data-entity="button.kid_reboot"]').click();
    // Nothing sent yet -- a confirmation panel is shown instead.
    expect(calls).toHaveLength(0);
    const confirmBtn = el.shadowRoot.querySelector('[data-act="confirm"]');
    expect(confirmBtn).not.toBeNull();

    confirmBtn.click();
    expect(pressed(calls)).toEqual(["button.kid_reboot"]);
  });
});
