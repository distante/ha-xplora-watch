import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadBundle } from "./helpers.js";

// The Controls popup (actions card) shares the overview's rule (ADR 0007): the TIME on its status
// line is the watch's fix age, not the poll time -- otherwise it visibly disagrees with the overview
// chip in exactly ADR 0007's target scenario (watch responds, fix is stale).

const NOW = "2026-06-27T10:00:00Z";
const OLD_FIX = "2026-06-27T09:37:00Z"; // 23 minutes before NOW

beforeAll(async () => {
  await loadBundle();
});
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(NOW));
});
afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

const UPDATE = "button.kid_update_mom";
const LU = "sensor.kid_last_update_mom";
const TRK = "device_tracker.kid_mom";
const DEV = "dev-actions";

function makeHass() {
  return {
    states: {
      [UPDATE]: { entity_id: UPDATE, state: NOW, attributes: { xplora_role: "update", friendly_name: "Update" } },
      [LU]: { entity_id: LU, state: "no_response", last_changed: NOW, last_updated: NOW, attributes: { xplora_role: "last_update" } },
      [TRK]: {
        entity_id: TRK,
        state: "not_home",
        last_changed: NOW,
        last_updated: NOW,
        attributes: { xplora_role: "tracker", "last tracking": OLD_FIX },
      },
    },
    entities: {
      [UPDATE]: { entity_id: UPDATE, device_id: DEV },
      [LU]: { entity_id: LU, device_id: DEV },
      [TRK]: { entity_id: TRK, device_id: DEV },
    },
    devices: { [DEV]: { name: "Kid" } },
    locale: { language: "en" },
    callService: async () => {},
  };
}

function mount() {
  const el = document.createElement("xplora-watch-actions-card");
  el.setConfig({ entities: [UPDATE] });
  document.body.appendChild(el);
  el.hass = makeHass();
  return el;
}

describe("controls (actions) card — status line uses fix age", () => {
  it("shows the fix age, not the poll time", () => {
    const el = mount();
    const when = el.shadowRoot.querySelector(".last-status .ls-when");
    expect(when).toBeTruthy();
    expect(when.textContent).toBe("23m ago"); // fix age (09:37 -> 10:00), not the just-now poll
    expect(el.shadowRoot.querySelector(".last-status.warning")).toBeTruthy(); // poll outcome
  });
});
