import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { fixAgeStatus, locationAgePhrase } from "../../custom_components/xplora_watch/www/xplora-watch-card.js";
import { loadBundle } from "./helpers.js";

// The shown position's age is the WATCH's fix time, not our poll time (ADR 0007). The header chip
// and the map popup banner both surface it, driven by one shared helper so they can't drift: the
// STATUS is the poll outcome (`last_update` sensor), the TIME is the tracker's `last tracking` fix.

const NOW = "2026-06-27T10:00:00Z";
const OLD_FIX = "2026-06-27T09:37:00Z"; // 23 minutes before NOW
const FRESH_FIX = "2026-06-27T09:59:30Z"; // 30s before NOW -> "just now"

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
  vi.restoreAllMocks();
});

/* --------------------------------------------------------------- pure helpers */

describe("locationAgePhrase", () => {
  it("is empty when the fix time is unknown", () => {
    expect(locationAgePhrase(null)).toBe("");
    expect(locationAgePhrase("")).toBe("");
    expect(locationAgePhrase("not-a-date")).toBe("");
  });

  it("says 'location just now' for a fresh fix", () => {
    expect(locationAgePhrase(FRESH_FIX)).toBe("location just now");
  });

  it("anchors an older fix to the location: 'location from 23m ago'", () => {
    expect(locationAgePhrase(OLD_FIX)).toBe("location from 23m ago");
  });
});

describe("fixAgeStatus", () => {
  it("takes STATUS from the last_update sensor and the fix time from the tracker", () => {
    const hass = {
      states: {
        "sensor.lu": { state: "no_response", attributes: {} },
        "device_tracker.t": { attributes: { "last tracking": OLD_FIX } },
      },
    };
    expect(fixAgeStatus(hass, { lastupdate: "sensor.lu", tracker: "device_tracker.t" })).toEqual({
      status: "warning",
      fixIso: OLD_FIX,
    });
  });

  it("is null-safe: no sensor -> null status, no tracker -> null fix", () => {
    expect(fixAgeStatus({ states: {} }, {})).toEqual({ status: null, fixIso: null });
  });
});

/* ----------------------------------------------------------------- overview UI */

// A watch device carrying a device_tracker (role `tracker`, with a `last tracking` ISO fix) and a
// `last_update` sensor (role `last_update`, the poll outcome). `luUpdated` is deliberately == NOW so
// a poll-time chip would read "just now" -- the tests assert we show the OLD fix age instead.
function makeHass({ tag, lastUpdate = "no_response", fixIso = OLD_FIX, luUpdated = NOW } = {}) {
  const TRK = `device_tracker.watch_${tag}`;
  const LU = `sensor.watch_last_update_${tag}`;
  const DEV = `dev-${tag}`;
  return {
    states: {
      [TRK]: {
        entity_id: TRK,
        state: "not_home",
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: NOW,
        attributes: {
          xplora_role: "tracker",
          friendly_name: "Dana",
          latitude: 52.53,
          longitude: 13.41,
          address: "Somewhere",
          "Home Distance (m)": 1200,
          "last tracking": fixIso,
        },
      },
      [LU]: {
        entity_id: LU,
        state: lastUpdate,
        last_changed: "2026-06-27T09:00:00Z",
        last_updated: luUpdated,
        attributes: { xplora_role: "last_update" },
      },
    },
    entities: {
      [TRK]: { entity_id: TRK, device_id: DEV },
      [LU]: { entity_id: LU, device_id: DEV },
    },
    devices: { [DEV]: { name: "Dana" } },
    locale: { language: "en" },
    callService: async () => {},
  };
}

function mountOverview(hass, configEntity) {
  const el = document.createElement("xplora-watch-overview-card");
  el.setConfig({ entity: configEntity });
  document.body.appendChild(el);
  el.hass = hass;
  return el;
}

describe("overview card — fix age surfaces", () => {
  it("header chip shows how old the FIX is, not when we last polled", () => {
    const el = mountOverview(makeHass({ tag: "chip" }), "device_tracker.watch_chip");
    const chip = el.shadowRoot.querySelector(".upd");
    expect(chip).toBeTruthy();
    // Poll outcome drives the colour/icon...
    expect(chip.classList.contains("warning")).toBe(true);
    // ...but the time is the fix age (09:37 -> 10:00), NOT the just-now poll timestamp.
    expect(chip.textContent).toContain("23m ago");
    expect(chip.textContent).not.toContain("just now");
  });

  it("map popup banner reports the fix age anchored to the location", async () => {
    const el = mountOverview(makeHass({ tag: "banner" }), "device_tracker.watch_banner");
    const row = el.shadowRoot.querySelector(".row.location");
    expect(row).toBeTruthy();
    row.click();
    await Promise.resolve(); // let _openPopup paint the banner (the map card build can fail in jsdom)
    const banner = el.shadowRoot.querySelector(".map-banner-text");
    expect(banner).toBeTruthy();
    expect(banner.textContent).toBe("Watch didn't respond · location from 23m ago");
    expect(el.shadowRoot.querySelector(".map-banner.warning")).toBeTruthy();
  });

  it("omits the time entirely when the fix time is unknown", async () => {
    const el = mountOverview(makeHass({ tag: "unknown", fixIso: null }), "device_tracker.watch_unknown");
    // Chip: status icon only, no age text.
    expect(el.shadowRoot.querySelector(".upd").textContent.trim()).toBe("");
    // Banner: status label with no " · location …" suffix.
    el.shadowRoot.querySelector(".row.location").click();
    await Promise.resolve();
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Watch didn't respond");
  });

  it("keeps the map banner live as fresh state arrives while the popup stays open", async () => {
    const el = mountOverview(makeHass({ tag: "live", lastUpdate: "no_response", fixIso: OLD_FIX }), "device_tracker.watch_live");
    el.shadowRoot.querySelector(".row.location").click();
    await Promise.resolve();
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Watch didn't respond · location from 23m ago");
    // A background poll lands while the popup is open: the watch responded with a fresh fix.
    el.hass = makeHass({ tag: "live", lastUpdate: "ok", fixIso: FRESH_FIX });
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Updated · location just now");
    expect(el.shadowRoot.querySelector(".map-banner.success")).toBeTruthy();
  });

  it("shows a neutral 'Location' banner (never a false 'Updated') when there is no poll outcome", async () => {
    // `last_update` in an unmapped state (e.g. HA's `unknown` right after setup, before the first
    // poll resolves) yields no poll outcome. ADR 0007 "no false success": the banner must NOT claim
    // "Updated" — it shows a neutral label carrying just the fix age.
    const el = mountOverview(makeHass({ tag: "noout", lastUpdate: "unknown", fixIso: OLD_FIX }), "device_tracker.watch_noout");
    el.shadowRoot.querySelector(".row.location").click();
    await Promise.resolve();
    const banner = el.shadowRoot.querySelector(".map-banner-text");
    expect(banner.textContent).toBe("Location · 23m ago");
    expect(el.shadowRoot.querySelector(".map-banner.unknown")).toBeTruthy();
    expect(el.shadowRoot.querySelector(".map-banner.success")).toBeFalsy();
    expect(banner.textContent).not.toContain("Updated");
  });

  it("location row shows no timestamp when fix time is unknown (no last_changed fallback)", () => {
    // `last_changed` (the entity's zone-transition time) is neither the fix time nor the poll time;
    // falling back to it would re-introduce the "which time is this?" ambiguity ADR 0007 removes.
    const el = mountOverview(makeHass({ tag: "rowunknown", fixIso: null }), "device_tracker.watch_rowunknown");
    const sub = el.shadowRoot.querySelector(".row.location .row-sub");
    expect(sub).toBeTruthy();
    expect(sub.textContent).not.toContain("2026"); // no absolute date leaked from last_changed
  });
});
