import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadBundle } from "./helpers.js";

beforeAll(async () => {
  await loadBundle();
});

const ALARM_ENTITY = "sensor.watch_alarms";
const SILENT_ENTITY = "sensor.watch_silents";

const ALARMS = [
  { id: "a1", status: "ENABLE", start: "07:00", weekdays: ["mon", "tue"], name: "School" },
  { id: "a2", status: "DISABLE", start: "08:30", weekdays: ["sat"] },
];
const SILENTS = [{ id: "s1", status: "ENABLE", start: "08:00", end: "15:00", weekdays: ["mon"] }];

// `_kind()` returns "silent" only when the bound entity carries a `silent` attribute; an alarm card
// must NOT have that key. `_base()` reads `entry_id`/`wuid` straight off the entity attributes.
function makeHass(entityId, listKey, list, calls) {
  return {
    states: {
      [entityId]: {
        entity_id: entityId,
        state: String(list.length),
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: "2026-06-27T10:00:00Z",
        attributes: { entry_id: "entry1", wuid: "watch1", friendly_name: "Dana", [listKey]: list },
      },
    },
    locale: { language: "en" },
    callService: async (...args) => calls.push(args),
  };
}

function mount(entityId, listKey, list, calls) {
  const el = document.createElement("xplora-watch-card");
  el.setConfig({ entity: entityId });
  document.body.appendChild(el);
  el.hass = makeHass(entityId, listKey, list, calls);
  return el;
}

const q = (el, sel) => el.shadowRoot.querySelector(sel);
// Open a row's copy menu (kebab) then return the now-rendered menu item for `act`.
function openRowMenu(el, id, act) {
  q(el, `[data-act="menu-row"][data-id="${id}"]`).click();
  return q(el, `.menu [data-act="${act}"]`);
}
function openBulkMenu(el, act) {
  q(el, '[data-act="menu-bulk"]').click();
  return q(el, `.menu [data-act="${act}"]`);
}

let clip;
beforeEach(() => {
  clip = vi.fn().mockResolvedValue(undefined);
  // Override navigator.clipboard for the copy-path assertions (jsdom has no real clipboard).
  Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText: clip }, configurable: true });
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("alarm/silent card — copy menu", () => {
  it("the row shows a single kebab, not three inline copy buttons", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    expect(q(el, '[data-act="menu-row"][data-id="a1"]')).not.toBeNull();
    // Copy actions only exist once the menu is opened.
    expect(q(el, '[data-act="copy-id"]')).toBeNull();
  });

  it("copy-id writes the bare entry id", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    openRowMenu(el, "a1", "copy-id").click();
    expect(clip).toHaveBeenCalledWith("a1");
  });

  it("copy-call writes a paste-ready set_alarm_enabled YAML block with id + state", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    openRowMenu(el, "a1", "copy-call").click();
    const yaml = clip.mock.calls[0][0];
    expect(yaml).toContain("action: xplora_watch.set_alarm_enabled");
    expect(yaml).toContain('alarm_id: "a1"');
    expect(yaml).toContain("enabled: true"); // a1 status is ENABLE
    expect(yaml).toContain("entity_id:"); // device target via the bound entity (_base())
    expect(yaml).toContain('- "sensor.watch_alarms"');
  });

  it("copy-call uses set_silent_enabled + silent_id for a silent card", () => {
    const el = mount(SILENT_ENTITY, "silent", SILENTS, []);
    openRowMenu(el, "s1", "copy-call").click();
    const yaml = clip.mock.calls[0][0];
    expect(yaml).toContain("action: xplora_watch.set_silent_enabled");
    expect(yaml).toContain('silent_id: "s1"');
  });

  it("copy-payload writes valid create JSON service-data (alarm keeps name, no end)", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    openRowMenu(el, "a1", "copy-payload").click();
    const data = JSON.parse(clip.mock.calls[0][0]);
    expect(data).toMatchObject({ entity_id: ["sensor.watch_alarms"], start: "07:00", weekdays: ["mon", "tue"], name: "School" });
    expect(data.end).toBeUndefined();
  });

  it("copy-payload for a silent carries end and no name", () => {
    const el = mount(SILENT_ENTITY, "silent", SILENTS, []);
    openRowMenu(el, "s1", "copy-payload").click();
    const data = JSON.parse(clip.mock.calls[0][0]);
    expect(data).toMatchObject({ start: "08:00", end: "15:00", weekdays: ["mon"] });
    expect(data.name).toBeUndefined();
  });

  it("copying closes the menu", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    openRowMenu(el, "a1", "copy-id").click();
    expect(q(el, ".menu")).toBeNull();
  });

  it("clicking the backdrop closes the menu without copying", () => {
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, []);
    q(el, '[data-act="menu-row"][data-id="a1"]').click();
    expect(q(el, ".menu")).not.toBeNull();
    q(el, '[data-act="menu-close"]').click();
    expect(q(el, ".menu")).toBeNull();
    expect(clip).not.toHaveBeenCalled();
  });
});

describe("alarm/silent card — header bulk menu", () => {
  it("Enable all calls turn_all_alarms_on with the device target (bound entity)", () => {
    const calls = [];
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, calls);
    openBulkMenu(el, "bulk-on").click();
    expect(calls).toContainEqual([
      "xplora_watch",
      "turn_all_alarms_on",
      { entity_id: ["sensor.watch_alarms"] },
      undefined,
      false,
    ]);
  });

  it("Disable all calls turn_all_alarms_off", () => {
    const calls = [];
    const el = mount(ALARM_ENTITY, "alarm", ALARMS, calls);
    openBulkMenu(el, "bulk-off").click();
    expect(calls.map((c) => c[1])).toContain("turn_all_alarms_off");
  });

  it("silent card bulk menu calls the turn_all_silents_* services", () => {
    const calls = [];
    const el = mount(SILENT_ENTITY, "silent", SILENTS, calls);
    openBulkMenu(el, "bulk-on").click();
    openBulkMenu(el, "bulk-off").click();
    expect(calls.map((c) => c[1])).toEqual(["turn_all_silents_on", "turn_all_silents_off"]);
  });

  it("the header bulk kebab is hidden when the list is empty", () => {
    const el = mount(ALARM_ENTITY, "alarm", [], []);
    expect(q(el, '[data-act="menu-bulk"]')).toBeNull();
  });
});
