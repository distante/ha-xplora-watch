import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { loadBundle } from "./helpers.js";

// The standalone watch-location map card (ADR 0008): one watch's current position rendered inline on
// any dashboard, carrying a fix-age banner (ADR 0007), a reload button that forces a fresh fix by
// pressing the watch's Update button, and an expand button that opens the SAME card full-screen. The
// same component is what the overview card's location-row popup shows (in fill mode). Tests assert
// EXTERNAL behaviour only -- rendered shadow DOM and captured `hass.callService` calls.

const NOW = "2026-06-27T10:00:00Z";
const OLD_FIX = "2026-06-27T09:37:00Z"; // 23 minutes before NOW
const FRESH_FIX = "2026-06-27T09:59:30Z"; // 30s before NOW -> "just now"

// Per-`tag` id set: the module-level trackInflight dedup registry + frozen fake-time mean the
// `button.press|<updateBtn>` key would leak across tests that share ids, so every test that presses
// (via reload or render-refresh) uses a UNIQUE tag. Account-tokened ids + integration-emitted
// `xplora_role` (ADR 0005): discovery is by (domain, role), never by parsing the id.
function idsFor(tag) {
  return {
    DEV: `dev-${tag}`,
    TRK: `device_tracker.xplora_${tag}_watch_tracker_natalie`,
    LU: `sensor.xplora_${tag}_watch_last_update_natalie`,
    UPD: `button.xplora_${tag}_watch_update_natalie`,
  };
}

// Default id set used by the read-only tests (which never press, so sharing a tag is safe).
const { DEV, TRK, LU, UPD } = idsFor("d");

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
  // stubMapHelpers() assigns window.loadCardHelpers (a plain property, not a spy), which
  // vi.restoreAllMocks() does not undo -- delete it so it can't leak into a later test.
  delete window.loadCardHelpers;
});

// Build a Guardian watch: a device_tracker (role `tracker`, with coords + a `last tracking` ISO fix),
// a `last_update` sensor (role `last_update`, the poll outcome) and an enabled Update button (role
// `update`). Knobs let a test drop coords, disable the button, or change the poll outcome/fix age.
function makeHass({
  tag = "d",
  fixIso = OLD_FIX,
  lastUpdate = "no_response",
  coords = true,
  updateButton = true,
  refreshOnRender = false,
  stamp = NOW, // resolved entities' `last_updated`; vary it to make the card's re-render signature change
  callService,
} = {}) {
  const { DEV, TRK, LU, UPD } = idsFor(tag);
  const states = {
    [TRK]: {
      entity_id: TRK,
      state: coords ? "not_home" : "unknown",
      last_changed: "2026-06-27T09:00:00Z",
      last_updated: stamp,
      attributes: {
        xplora_role: "tracker",
        friendly_name: "Dana",
        entity_picture: "/dana.png",
        address: "Somewhere",
        "Home Distance (m)": 1200,
        "last tracking": fixIso,
        refresh_on_card_render: refreshOnRender,
        ...(coords ? { latitude: 52.53, longitude: 13.41 } : {}),
      },
    },
    [LU]: {
      entity_id: LU,
      state: lastUpdate,
      last_changed: "2026-06-27T09:00:00Z",
      last_updated: stamp,
      attributes: { xplora_role: "last_update", refresh_on_card_render: refreshOnRender },
    },
  };
  const entities = {
    [TRK]: { entity_id: TRK, device_id: DEV },
    [LU]: { entity_id: LU, device_id: DEV },
  };
  // A disabled button entity is registered but has NO state (roleOf() -> undefined), so the card
  // discovers no Update button and offers no reload -- exactly the enabled/disabled distinction.
  entities[UPD] = { entity_id: UPD, device_id: DEV };
  if (updateButton) {
    states[UPD] = {
      entity_id: UPD,
      state: NOW,
      last_changed: NOW,
      last_updated: NOW,
      attributes: { xplora_role: "update" },
    };
  }
  return {
    states,
    entities,
    devices: { [DEV]: { name: "Dana Watch" } },
    locale: { language: "en" },
    callService: callService || vi.fn(async () => {}),
  };
}

// A Contact watch: no device_tracker exists at all (ref:XW-009 removes it), only the poll-outcome
// sensor. This is the permanent "location isn't available for this account" case.
function makeContactHass(tag = "d") {
  const { DEV, LU } = idsFor(tag);
  return {
    states: {
      [LU]: { entity_id: LU, state: "ok", last_updated: NOW, attributes: { xplora_role: "last_update" } },
    },
    entities: { [LU]: { entity_id: LU, device_id: DEV } },
    devices: { [DEV]: { name: "Timmy Watch" } },
    locale: { language: "en" },
    callService: vi.fn(async () => {}),
  };
}

function mount(config, hass) {
  const el = document.createElement("xplora-watch-map-card");
  el.setConfig(config);
  document.body.appendChild(el);
  if (hass) el.hass = hass;
  return el;
}

// Stub HA's `loadCardHelpers()` so the card can build its embedded `map` card in jsdom. Records
// every `createCardElement` config so tests can assert what reached HA's map card, and returns a
// dumb element that just tracks the `hass` pushed to it.
function stubMapHelpers() {
  const configs = [];
  window.loadCardHelpers = async () => ({
    createCardElement: (conf) => {
      configs.push(conf);
      const el = document.createElement("div");
      el.className = "stub-map";
      Object.defineProperty(el, "hass", { set(v) { this._hass = v; }, get() { return this._hass; }, configurable: true });
      return el;
    },
  });
  return configs;
}

// Let the card's async map build (await loadCardHelpers -> await createCardElement) settle.
const flush = () => vi.runAllTimersAsync();

describe("map card — registration", () => {
  it("registers as a custom element", () => {
    expect(customElements.get("xplora-watch-map-card")).toBeTypeOf("function");
  });

  it("appears in window.customCards with a preview", () => {
    const entry = (window.customCards || []).find((c) => c.type === "xplora-watch-map-card");
    expect(entry).toBeTruthy();
    expect(entry.preview).toBe(true);
    expect(entry.name).toBeTruthy();
  });
});

describe("map card — config & binding", () => {
  it("throws from setConfig when neither entity nor device is given", () => {
    const el = document.createElement("xplora-watch-map-card");
    expect(() => el.setConfig({})).toThrow(/entity.*device|device.*entity/i);
  });

  it("accepts an entity binding and resolves the watch via role discovery", () => {
    const el = mount({ entity: LU }, makeHass()); // bound by the last_update sensor, NOT the tracker
    // Discovery from the device (via the bound entity's device_id) plots the tracker regardless of
    // which watch entity the card was pointed at.
    const header = el.shadowRoot.querySelector(".map-header");
    expect(header).toBeTruthy();
    expect(header.textContent).toContain("Dana Watch");
  });

  it("accepts a device binding", () => {
    const el = mount({ device: DEV }, makeHass());
    expect(el.shadowRoot.querySelector(".map-header").textContent).toContain("Dana Watch");
  });
});

describe("map card — header", () => {
  it("shows the watch name from the device by default", () => {
    const el = mount({ device: DEV }, makeHass());
    expect(el.shadowRoot.querySelector(".map-header .map-name").textContent).toBe("Dana Watch");
  });

  it("uses the `title` override when set", () => {
    const el = mount({ device: DEV, title: "Where's Dana" }, makeHass());
    expect(el.shadowRoot.querySelector(".map-name").textContent).toBe("Where's Dana");
  });

  it("hides the header when show_header is false", () => {
    const el = mount({ device: DEV, show_header: false }, makeHass());
    expect(el.shadowRoot.querySelector(".map-header")).toBeFalsy();
  });
});

describe("map card — fix-age banner (ADR 0007)", () => {
  it("reports the poll outcome as colour/icon and the fix AGE as the text", async () => {
    const el = mount({ device: DEV }, makeHass({ lastUpdate: "no_response", fixIso: OLD_FIX }));
    await flush();
    const banner = el.shadowRoot.querySelector(".map-banner-text");
    expect(banner).toBeTruthy();
    // The time is the fix age (09:37 -> 10:00), NOT the just-now poll time; the label carries the outcome.
    expect(banner.textContent).toBe("Watch didn't respond · location from 23m ago");
    expect(el.shadowRoot.querySelector(".map-banner.warning")).toBeTruthy();
  });

  it("shows a neutral 'Location' banner (never a false 'Updated') when there is no poll outcome", async () => {
    const el = mount({ device: DEV }, makeHass({ lastUpdate: "unknown", fixIso: OLD_FIX }));
    await flush();
    const banner = el.shadowRoot.querySelector(".map-banner-text");
    expect(banner.textContent).toBe("Location · 23m ago");
    expect(el.shadowRoot.querySelector(".map-banner.unknown")).toBeTruthy();
    expect(banner.textContent).not.toContain("Updated");
  });

  it("re-derives the banner in place on a later hass push (never freezes at render time)", async () => {
    const el = mount({ device: DEV }, makeHass({ lastUpdate: "no_response", fixIso: OLD_FIX }));
    await flush();
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Watch didn't respond · location from 23m ago");
    // A background poll lands: the watch responded with a fresh fix.
    el.hass = makeHass({ lastUpdate: "ok", fixIso: FRESH_FIX });
    await flush();
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Updated · location just now");
    expect(el.shadowRoot.querySelector(".map-banner.success")).toBeTruthy();
  });

  it("banner text is allowed to wrap (not truncated) so a phone never hides it", async () => {
    const el = mount({ device: DEV }, makeHass());
    await flush();
    const span = el.shadowRoot.querySelector(".map-banner-text");
    // The style block sets normal white-space so long status text wraps instead of being clipped.
    expect(el.shadowRoot.querySelector("style").textContent).toMatch(/\.map-banner-text[^}]*white-space:\s*normal/);
    expect(span).toBeTruthy();
  });
});

describe("map card — the map surface", () => {
  it("builds HA's map card for the tracker and passes the default 16:9 aspect ratio", async () => {
    const configs = stubMapHelpers();
    const el = mount({ device: DEV }, makeHass());
    await flush();
    expect(el.shadowRoot.querySelector(".map-body .stub-map")).toBeTruthy();
    const conf = configs.find((c) => c.type === "map");
    expect(conf).toBeTruthy();
    expect(conf.entities).toEqual([TRK]);
    expect(conf.aspect_ratio).toBe("16:9");
  });

  it("passes a custom aspect_ratio through to the map card", async () => {
    const configs = stubMapHelpers();
    mount({ device: DEV, aspect_ratio: "4:3" }, makeHass());
    await flush();
    expect(configs.find((c) => c.type === "map").aspect_ratio).toBe("4:3");
  });

  it("aborts an in-flight map build when the card disconnects (no mount into a detached body)", async () => {
    // Park the build at `await createCardElement(...)`, then disconnect the card before it resolves.
    // The resolved map must NOT be appended -- disconnectedCallback bumps the generation token so the
    // superseded build aborts instead of mounting a Leaflet map into a detached shadow root.
    let resolveCreate;
    const pending = new Promise((r) => (resolveCreate = r));
    window.loadCardHelpers = async () => ({ createCardElement: () => pending });
    const el = mount({ device: DEV }, makeHass());
    await vi.advanceTimersByTimeAsync(0); // let loadCardHelpers() resolve -> build parks on createCardElement
    el.remove(); // disconnectedCallback -> _resetMap() bumps _mapGen
    const stub = document.createElement("div");
    stub.className = "stub-map";
    resolveCreate(stub);
    await flush();
    expect(el.shadowRoot.querySelector(".stub-map")).toBeFalsy(); // gen mismatch aborted the mount
  });

  it("re-attaches (never rebuilds) the embedded map across a re-rendering hass push", async () => {
    const configs = stubMapHelpers();
    const el = mount({ device: DEV }, makeHass({ fixIso: OLD_FIX }));
    await flush();
    const mapEl = el.shadowRoot.querySelector(".stub-map");
    const builtCount = configs.filter((c) => c.type === "map").length;
    // A background poll lands with a NEW timestamp -> the re-render signature changes, so _render()
    // actually runs and regenerates the card body. The persistent map element must be re-attached
    // (moved) into the fresh body, NOT rebuilt -- otherwise Leaflet's pan/zoom would reset.
    el.hass = makeHass({ fixIso: FRESH_FIX, stamp: "2026-06-27T10:05:00Z" });
    await flush();
    expect(el.shadowRoot.querySelector(".map-banner-text").textContent).toBe("Watch didn't respond · location just now"); // proves a re-render happened
    expect(configs.filter((c) => c.type === "map").length).toBe(builtCount); // NOT rebuilt
    expect(el.shadowRoot.querySelector(".stub-map")).toBe(mapEl); // ...same element instance, re-attached
    expect(el.shadowRoot.querySelector(".map-body .stub-map")).toBe(mapEl); // ...and it's back in the (new) body
  });
});

describe("map card — reload", () => {
  it("presses the watch's Update button to force a fresh fix", async () => {
    const callService = vi.fn(async () => {});
    const el = mount({ device: DEV }, makeHass({ callService }));
    await flush();
    const reload = el.shadowRoot.querySelector(".map-reload");
    expect(reload).toBeTruthy();
    reload.click();
    await flush();
    expect(callService).toHaveBeenCalledWith("button", "press", { entity_id: UPD });
  });

  it("announces the finished update so a hosting card can refresh its own status", async () => {
    const el = mount({ device: DEV }, makeHass());
    let fired = false;
    el.addEventListener("xplora-update-status", () => (fired = true));
    await flush();
    el.shadowRoot.querySelector(".map-reload").click();
    await flush();
    expect(fired).toBe(true);
  });

  it("offers NO reload button when the Update button is disabled (has no state)", async () => {
    const el = mount({ device: DEV }, makeHass({ updateButton: false }));
    await flush();
    expect(el.shadowRoot.querySelector(".map-reload")).toBeFalsy();
  });

  it("recovers the reload button after a failed press (never stuck disabled/spinning)", async () => {
    // An offline watch rejects the press. The button must NOT be left disabled + spinning forever --
    // nothing else would re-render it, since a failed press changes no state.
    const callService = vi.fn(async () => {
      throw new Error("watch offline");
    });
    const el = mount({ device: DEV }, makeHass({ callService }));
    await flush();
    el.shadowRoot.querySelector(".map-reload").click();
    await flush();
    expect(callService).toHaveBeenCalledWith("button", "press", { entity_id: UPD });
    const after = el.shadowRoot.querySelector(".map-reload");
    expect(after).toBeTruthy();
    expect(after.hasAttribute("disabled")).toBe(false); // live again
    expect(after.querySelector("ha-icon.spin")).toBeFalsy(); // not spinning
  });
});

describe("map card — empty states (ADR 0008)", () => {
  it("Contact watch (no tracker exists): permanent message, no reload, no map", async () => {
    const configs = stubMapHelpers();
    const el = mount({ device: DEV }, makeContactHass());
    await flush();
    const empty = el.shadowRoot.querySelector(".map-empty.contact");
    expect(empty).toBeTruthy();
    expect(empty.textContent).toMatch(/account type/i);
    expect(el.shadowRoot.querySelector(".map-reload")).toBeFalsy();
    expect(configs.some((c) => c.type === "map")).toBe(false); // map never created
  });

  it("Guardian watch with no fix yet: transient 'Location unavailable', reload kept live, no map", async () => {
    const configs = stubMapHelpers();
    const el = mount({ device: DEV }, makeHass({ coords: false }));
    await flush();
    const empty = el.shadowRoot.querySelector(".map-body .map-empty");
    expect(empty).toBeTruthy();
    expect(empty.textContent).toBe("Location unavailable");
    expect(el.shadowRoot.querySelector(".map-reload")).toBeTruthy(); // still live
    expect(configs.some((c) => c.type === "map")).toBe(false); // NEVER render the map without coords
  });

  it("Guardian tracker still warming up (registered, no state yet): transient 'Locating…', not Contact", async () => {
    // The tracker entity IS in the registry but has pushed no state, so role discovery finds no
    // tracker YET. This must read as transient ("Locating…"), NOT the permanent Contact message --
    // otherwise a cold-start Guardian would be mislabelled as a Contact.
    const hass = {
      states: {}, // no states at all yet
      entities: { [TRK]: { entity_id: TRK, device_id: DEV } }, // ...but the tracker is registered
      devices: { [DEV]: { name: "Dana Watch" } },
      locale: { language: "en" },
      callService: vi.fn(async () => {}),
    };
    const el = mount({ device: DEV }, hass);
    const empty = el.shadowRoot.querySelector(".map-empty");
    expect(empty).toBeTruthy();
    expect(empty.textContent).toBe("Locating…");
    expect(empty.classList.contains("contact")).toBe(false); // NOT the permanent Contact case
  });

  it("device binding on a cold start (registry not loaded): transient 'Locating…', NOT permanent Contact", () => {
    // Empty `hass.entities` (the first frames after connect) must not be mistaken for "the device
    // genuinely has no tracker" -- a real Guardian bound by `device:` would otherwise flash the
    // permanent Contact message. It stays transient until the registry populates.
    const hass = {
      states: {},
      entities: {}, // registry not loaded yet
      devices: { [DEV]: { name: "Dana Watch" } },
      locale: { language: "en" },
      callService: vi.fn(async () => {}),
    };
    const el = mount({ device: DEV }, hass);
    const empty = el.shadowRoot.querySelector(".map-empty");
    expect(empty).toBeTruthy();
    expect(empty.textContent).toBe("Locating…");
    expect(empty.classList.contains("contact")).toBe(false); // NOT the permanent Contact verdict
  });

  it("mis-configured entity (not in the registry): a clear configuration error, no reload", () => {
    // User story 24: bound to an id that resolves no watch -> a clear config error to fix the card.
    const BAD = "device_tracker.does_not_exist";
    const hass = { states: {}, entities: {}, devices: {}, locale: { language: "en" }, callService: vi.fn(async () => {}) };
    const el = mount({ entity: BAD }, hass);
    const empty = el.shadowRoot.querySelector(".map-empty.error");
    expect(empty).toBeTruthy();
    expect(empty.textContent).toContain("not found");
    expect(empty.textContent).toContain(BAD);
    expect(el.shadowRoot.querySelector(".map-reload")).toBeFalsy();
  });
});

describe("map card — getStubConfig", () => {
  it("defaults to the tracker-role entity for the card picker preview", () => {
    // A non-tracker watch entity is listed FIRST so the generic `xplora…_watch_` fallback would pick
    // the wrong one -- the stub must resolve the device_tracker by its role, not by id order.
    const hass = {
      states: {
        "sensor.xplora_dana_watch_battery_x": { entity_id: "sensor.xplora_dana_watch_battery_x", state: "80", attributes: { xplora_role: "battery" } },
        [TRK]: { entity_id: TRK, state: "not_home", attributes: { xplora_role: "tracker" } },
      },
    };
    const stub = customElements.get("xplora-watch-map-card").getStubConfig(hass);
    expect(stub.entity).toBe(TRK); // the device_tracker with xplora_role "tracker", not the battery sensor
  });
});

describe("map card — expand / fill mode", () => {
  it("expand opens the SAME card full-screen in fill mode", async () => {
    stubMapHelpers();
    const el = mount({ device: DEV }, makeHass());
    await flush();
    const expand = el.shadowRoot.querySelector(".map-expand");
    expect(expand).toBeTruthy();
    expand.click();
    await flush();
    const popupCard = el.shadowRoot.querySelector(".modal-host .card-popup.fill xplora-watch-map-card");
    expect(popupCard).toBeTruthy();
    expect(popupCard.fill).toBe(true);
  });

  it("a fill-mode card suppresses its own header AND expand button (no recursive re-open)", async () => {
    stubMapHelpers();
    const el = document.createElement("xplora-watch-map-card");
    el.fill = true;
    el.setConfig({ device: DEV });
    document.body.appendChild(el);
    el.hass = makeHass();
    await flush();
    expect(el.shadowRoot.querySelector(".map-header")).toBeFalsy();
    expect(el.shadowRoot.querySelector(".map-expand")).toBeFalsy();
    // ...but reload is still available full-screen.
    expect(el.shadowRoot.querySelector(".map-reload")).toBeTruthy();
  });
});

describe("map card — refresh-on-render dedup (ban hygiene, ADR 0008)", () => {
  it("fires ONE button.press across a co-rendered overview + map card when the opt-in is on", async () => {
    const ids = idsFor("dedup");
    const callService = vi.fn(async () => {});
    const hass = makeHass({ tag: "dedup", refreshOnRender: true, callService });

    // The overview card presses the same Update button on render; the map card must dedup against it
    // via the shared trackInflight key `button.press|<updateBtn>` so only ONE `see` is issued. The
    // overview only render-refreshes when bound by an entity, so bind it to a real watch entity.
    const overview = document.createElement("xplora-watch-overview-card");
    overview.setConfig({ entity: ids.TRK });
    document.body.appendChild(overview);
    overview.hass = hass;

    const map = document.createElement("xplora-watch-map-card");
    map.setConfig({ device: ids.DEV });
    document.body.appendChild(map);
    map.hass = hass;

    await flush();
    const presses = callService.mock.calls.filter((c) => c[0] === "button" && c[1] === "press" && c[2].entity_id === ids.UPD);
    expect(presses.length).toBe(1);
  });

  it("stays static (no press) when refresh_on_card_render is off", async () => {
    const callService = vi.fn(async () => {});
    const map = mount({ device: idsFor("static").DEV }, makeHass({ tag: "static", refreshOnRender: false, callService }));
    await flush();
    expect(callService.mock.calls.some((c) => c[0] === "button" && c[1] === "press")).toBe(false);
  });
});
