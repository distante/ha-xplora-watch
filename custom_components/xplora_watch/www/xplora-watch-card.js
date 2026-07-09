/**
 * Xplora® Watch alarm / silent-time card.
 *
 * A custom Lovelace card for the `xplora_watch` integration. It renders ONE watch's list of
 * either alarms OR silent-time windows (auto-detected from the bound sensor) and drives full
 * CRUD — list, add, edit, delete and per-entry enable/disable — through the integration's
 * services. The sensor exposes everything the card needs (`entry_id`, `wuid` and the per-entry
 * list), so no configuration beyond `entity` is required.
 *
 * Config:
 *   type: custom:xplora-watch-card
 *   entity: sensor.kid_one_watch_silents   # an *_alarms or *_silents list sensor (required)
 *   title: Silent times                    # optional override
 *
 * Element choices (deliberate, for longevity). The interactive controls use Home Assistant's own
 * elements so they stay themed (light/dark) and track HA's design system:
 *   - ha-card, ha-button, ha-icon-button, ha-switch, ha-icon  (always loaded; ha-button uses the
 *     short size tokens s/m/l — the long forms small/medium/large are deprecated)
 *   - ha-input        (text; replaced the removed `ha-textfield` in 2026.6)
 *   - ha-filter-chip / ha-chip-set  (selectable weekday chips)
 * Two things are intentionally NOT HA components, because driving them raw from a custom card is
 * fragile and they broke in practice on 2026.6:
 *   - The add/edit popup is a self-built modal (backdrop + panel) from plain divs + theme CSS
 *     variables. HA's `ha-dialog` is a heavyweight element meant to be opened via HA's internal
 *     dialog manager (its title/action slots are not a stable external API); a themed div overlay
 *     is static markup that can't be deprecated and renders deterministically.
 *   - Time entry uses a native `<input type="time">` — a web standard (never deprecated), the OS
 *     time picker, and a plain "HH:MM" value. HA's `ha-time-input` rendered blank when seeded
 *     externally (its internal value is an object and it pulls in lazy sub-elements).
 *
 * The read-only weekday badges in each list row are plain <span>s for the same reason: static
 * markup is not a deprecation risk (only interactive widgets are).
 */

const DOMAIN = "xplora_watch";

// Local mirror of the integration's `xplora_watch.*` service names (Python side:
// `const.ATTR_SERVICE_*`). JS can't import the Python constants, so this is the card's single
// source of truth for those strings -- change a name here in one place if the service is renamed,
// rather than hunting bare literals through the callService sites below.
const SERVICE = Object.freeze({
  CREATE_ALARM: "create_alarm",
  UPDATE_ALARM: "update_alarm",
  DELETE_ALARM: "delete_alarm",
  SET_ALARM_ENABLED: "set_alarm_enabled",
  CREATE_SILENT: "create_silent",
  UPDATE_SILENT: "update_silent",
  DELETE_SILENT: "delete_silent",
  SET_SILENT_ENABLED: "set_silent_enabled",
  TURN_ALL_ALARMS_ON: "turn_all_alarms_on",
  TURN_ALL_ALARMS_OFF: "turn_all_alarms_off",
  TURN_ALL_SILENTS_ON: "turn_all_silents_on",
  TURN_ALL_SILENTS_OFF: "turn_all_silents_off",
  REFRESH_FUNCTIONS: "refresh_functions",
});

// Attribute name the integration surfaces on EVERY watch entity (Python side:
// `const.CONF_REFRESH_ON_CARD_RENDER`, attached in `entity.XploraBaseEntity.extra_state_attributes`).
// The cards read it to decide whether to pull fresh data as soon as they are shown.
const ATTR_REFRESH_ON_CARD_RENDER = "refresh_on_card_render";

// Websocket command (Python side: `const.WS_TYPE_LOCATION_HISTORY`) the overview card calls to read
// location-history ranges longer than the bounded slice carried on the sensor's state attributes.
const WS_LOCATION_HISTORY = `${DOMAIN}/location_history`;

// Cap on the number of points plotted (a long track of thousands of points is both slow to draw and
// visually noisy). Overridable per card via `history_max_points`.
const HISTORY_MAX_POINTS_DEFAULT = 500;
// How long to wait for HA's lazy `ha-map` element to register before falling back to a list.
const HA_MAP_WAIT_MS = 2500;
// Recent days the day selector always offers (the watch's API only serves the last few days). Older
// days appear only if they were archived (cached) -- e.g. via the daily `fetch_history` service.
const HISTORY_RECENT_DAYS = 3;
// Auto-fit zoom cap for the history map: a day spent in one place has a tiny bounding box that would
// otherwise zoom to house level. Most HA versions use `ha-map.zoom` as the fitBounds maxZoom.
// Overridable per card via `history_zoom`.
const HISTORY_MAP_ZOOM_DEFAULT = 17;
// Initial zoom for the single-point position popup (HA `map` card `default_zoom`); higher = closer.
const POSITION_MAP_ZOOM = 18;
// Default aspect ratio for the standalone map card's inline map (passed to HA's `map` card). Makes
// the map responsive by construction -- a narrow phone column and a wide panel both look right.
const MAP_CARD_DEFAULT_ASPECT = "16:9";

// How long an optimistically-rendered outgoing message is kept before it's dropped if the server
// never echoes it back. The Xplora backend usually indexes a sent message within a second or two,
// but a lost/failed send must not leave a permanent "ghost" bubble -- so expire after this window.
const PENDING_SEND_TTL_MS = 120000;

// Auto-scroll the chat to the newest message only when the user is already within this many pixels
// of the bottom, so a background chat update doesn't yank them away while they read history.
const SCROLL_PIN_THRESHOLD_PX = 120;

// Matches one emoji "grapheme": an emoji-presentation char (or a text-default pictographic forced
// to emoji with VS16) plus an optional skin-tone modifier and any ZWJ-joined continuation; or a
// flag (two regional indicators). Built into a fresh RegExp per use to avoid shared lastIndex
// state. Deliberately excludes bare ™/©/® etc. (Extended_Pictographic but not emoji by default) so
// ordinary text symbols aren't enlarged.
const EMOJI_SEQ_SRC =
  "(?:(?:\\p{Emoji_Presentation}|\\p{Extended_Pictographic}\\uFE0F)\\p{Emoji_Modifier}?|\\p{Regional_Indicator}{2})" +
  "(?:\\u200D(?:\\p{Emoji_Presentation}|\\p{Extended_Pictographic}\\uFE0F)\\p{Emoji_Modifier}?)*";

// True when the bound entity carries the user's "refresh data when cards are shown" preference.
function refreshOnRenderEnabled(stateObj) {
  return !!(stateObj && stateObj.attributes && stateObj.attributes[ATTR_REFRESH_ON_CARD_RENDER]);
}

// True when the bound entity genuinely does not exist (as opposed to merely not having a state yet).
// `hass.states[id]` is empty for BOTH a still-coming-up entity and one that will never appear -- the
// classic case being a card left pointing at an old entity id after a slug rename. The entity
// registry (`hass.entities`) tells them apart: a live entity is listed there before it ever pushes a
// state, so "no state AND not in the registry" means gone/renamed. Returns false while the registry
// itself hasn't loaded (older HA, or the first frames after connect) -- we must not cry "missing"
// before we can actually tell, or a normal cold start would flash a false error.
function entityMissing(hass, entityId) {
  if (!hass || !hass.entities) return false;
  if (hass.states && hass.states[entityId]) return false;
  return !hass.entities[entityId];
}

// Inner HTML for a `.placeholder` shown when the bound entity has no state: name the entity and, when
// it is genuinely absent, say so (with a fix-the-card hint) instead of "Waiting for …" -- which would
// otherwise spin forever on an id that will never resolve.
function entityStatusHtml(hass, entityId, esc) {
  const code = `<code>${esc(entityId)}</code>`;
  if (entityMissing(hass, entityId)) {
    return `<div class="empty-title">Entity ${code} not found</div>
        <div class="empty-sub">It may have been renamed or removed — edit the card and pick the current entity.</div>`;
  }
  return `<div>Waiting for ${code}…</div>`;
}

// De-duplicate the on-demand refreshes triggered when cards render, and expose each run as a shared
// awaitable so a co-rendered card can drive a loading state off the *real* refresh lifecycle. A
// single dashboard view can hold several cards bound to the same watch (overview + alarm list +
// chat); without this each would fire its own service call on render. NOTE: the integration's
// coordinator is the authoritative dedup -- concurrent fetches with the same signature are coalesced
// server-side onto one network fan-out (see `_inflight_updates`), so this guard is a UX nicety that
// avoids even issuing the redundant websocket calls; correctness no longer depends on it.
//
// `_refreshDedup` records the last time each key fired (or was marked) for TTL suppression;
// `_inflightRuns` holds the live shared promise while a run is actually in flight.
const _refreshDedup = new Map();
const _inflightRuns = new Map();
export const REFRESH_DEDUP_TTL_MS = 15000;
// Safety net: if a refresh service call never settles (e.g. a dropped websocket reply), force the
// run's shared promise to resolve after this long so downstream loading state can't wedge forever.
export const REFRESH_WATCHDOG_MS = 30000;
// Keep a refresh spinner visible for at least this long. A cached/demo read can return in a few ms
// -- without a floor the spinner flashes too briefly to be seen, so the refresh gives no feedback.
export const MIN_REFRESH_SPIN_MS = 500;

// Start (or join) a deduped refresh for `key`, running `fn` at most once per TTL window. Returns a
// handle `{ promise, inflight }`:
//   - Concurrent callers for the same key share ONE run: the first fires `fn`, later callers get the
//     same live `promise` with `inflight: true` and do not re-fire.
//   - A caller arriving after the run settled but still within the TTL gets `inflight: false` (with
//     an already-resolved `promise`) -- nothing is live to await, so downstream shows no loading.
// The shared `promise` never rejects (a failed run is swallowed + logged, matching the old
// fire-and-forget behaviour) and settles no sooner than MIN_REFRESH_SPIN_MS and no later than
// REFRESH_WATCHDOG_MS after the run starts -- so an instant read still shows feedback and a
// never-settling one can't wedge a caller's loading state.
export function trackInflight(key, fn) {
  // A run is already live for this key: share it, don't re-fire.
  const active = _inflightRuns.get(key);
  if (active !== undefined) return { promise: active, inflight: true };

  // Fired (or explicitly marked) recently: suppress -- nothing live to await.
  const now = Date.now();
  const last = _refreshDedup.get(key);
  if (last !== undefined && now - last < REFRESH_DEDUP_TTL_MS) {
    return { promise: Promise.resolve(), inflight: false };
  }

  // Fresh run: record the window, fire `fn` (deferred so it can't throw into the caller), and wrap
  // it with the min-floor + watchdog into one shared, non-rejecting promise.
  _refreshDedup.set(key, now);
  const work = Promise.resolve()
    .then(fn)
    .catch((err) => console.error(`xplora-watch-card: tracked refresh "${key}" failed`, err));

  const promise = new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      _inflightRuns.delete(key);
      resolve();
    };
    const watchdog = setTimeout(finish, REFRESH_WATCHDOG_MS);
    work.then(() => {
      // Hold the shared promise for a perceptible minimum so an instant (cached) run still shows
      // feedback, then settle -- clearing the watchdog we no longer need.
      const remaining = MIN_REFRESH_SPIN_MS - (Date.now() - now);
      const settle = () => {
        clearTimeout(watchdog);
        finish();
      };
      if (remaining > 0) setTimeout(settle, remaining);
      else settle();
    });
  });
  _inflightRuns.set(key, promise);
  return { promise, inflight: true };
}

// Record that a refresh for `key` just happened (without firing), so a follow-up auto-refresh for
// the same watch/data set is suppressed by the dedup window. Used after an explicit user action.
export function markRefreshed(key) {
  _refreshDedup.set(key, Date.now());
}

// Every Xplora entity carries its role in the `xplora_role` state attribute (its entity-description
// key -- "battery", "state", "update", "tracker", …), emitted by the integration so the cards can
// discover a watch's entities by role WITHOUT parsing the account-tokened entity_id (ADR 0005).
// Returns undefined when the entity has no state yet (nothing to show), so callers skip it until it
// warms up. Role discovery keys on (entity domain, this value) -- never on the id string.
const XPLORA_ROLE_ATTR = "xplora_role";
export function roleOf(hass, entityId) {
  const s = hass && hass.states && hass.states[entityId];
  return s && s.attributes ? s.attributes[XPLORA_ROLE_ATTR] : undefined;
}

// Canonical weekday order. Index 0 = Sunday .. 6 = Saturday — matches the integration's
// `weekRepeat` string and the `weekdays` keys the sensor already exposes per entry.
const DAY_KEYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const DAY_SHORT_FALLBACK = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_FULL_FALLBACK = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
// 2021-08-01 was a Sunday — a reference week used to localize day names via Intl (index 0=Sun).
const REF_WEEK = [0, 1, 2, 3, 4, 5, 6].map((i) => new Date(2021, 7, 1 + i));

const PRESETS = {
  everyday: ["sun", "mon", "tue", "wed", "thu", "fri", "sat"],
  weekdays: ["mon", "tue", "wed", "thu", "fri"],
  weekend: ["sat", "sun"],
};

// Editor-only HA elements that the frontend lazy-loads. Only the alarm-name field (`ha-input`)
// is a lazy HA element now (the weekday selector uses plain <button> toggle chips), so we just
// warm that one before the popup opens.
const EDITOR_ELEMENTS = ["ha-input"];

class XploraWatchCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._mode = "list"; // "list" | "add" | "edit"
    this._editingId = null; // id of entry being edited
    this._confirmId = null; // id awaiting delete confirmation
    this._menu = null; // open overflow menu: null | {kind:"bulk"|"row", id, top, left}
    this._form = null; // { name, start, end, days:Set }
    this._error = "";
    this._busy = false;
    this._built = false;
    this._refreshing = false; // a header-triggered functions refresh is in flight
    this._autoRefreshDone = false; // guard so "refresh on render" fires once per card instance
    this._modal = null; // self-built modal-overlay host, created on first open and reused
    this._keyBound = false; // whether the Escape-to-close listener is attached
    this._dayCache = null; // { locale, short:[], long:[] } — localized weekday names, cached
    this._onKeyDown = (ev) => {
      if (ev.key === "Escape" && this._mode !== "list") this._cancel();
    };
  }

  /* ---------------------------------------------------------------- config */

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("xplora-watch-card: an `entity` (an *_alarms or *_silents sensor) is required.");
    }
    this._config = config;
    if (this._hass) this._render();
  }

  static getStubConfig(hass) {
    let entity = "sensor.watch_silents";
    if (hass && hass.states) {
      const match = Object.keys(hass.states).find((id) => {
        const a = hass.states[id].attributes || {};
        return a.silent !== undefined || a.alarm !== undefined;
      });
      if (match) entity = match;
    }
    return { entity };
  }

  getCardSize() {
    const entries = this._getEntries();
    return 2 + Math.max(entries.length, 1); // header + rows + add button
  }

  connectedCallback() {
    // Pre-warm the lazy editor elements so the add/edit popup opens instantly on first use.
    this._ensureEditorElements();
  }

  disconnectedCallback() {
    if (this._keyBound) {
      window.removeEventListener("keydown", this._onKeyDown);
      this._keyBound = false;
    }
  }

  /* ------------------------------------------------------------------ hass */

  set hass(hass) {
    const prev = this._hass;
    this._hass = hass;
    if (!this._config) return;
    // While the popup is open we must NOT rebuild (it would drop the modal and lose typed
    // values); the list re-renders when the popup closes.
    if (this._built && this._mode !== "list") return;
    // HA pushes a fresh `hass` on ANY entity change in the system. Only re-render when the bound
    // sensor's state object (HA reuses it while unchanged) or the locale actually changed, so a
    // busy dashboard doesn't rebuild + re-wire the whole list on every unrelated update.
    const entity = this._config.entity;
    const changed =
      !this._built ||
      !prev ||
      prev.states[entity] !== hass.states[entity] ||
      prev.locale !== hass.locale ||
      // The state object stays undefined whether the entity is coming up or missing, so also
      // re-render when the registry verdict flips (e.g. registry loads and confirms it's gone).
      entityMissing(prev, entity) !== entityMissing(hass, entity);
    if (changed) this._render();
    // Once the bound sensor exists, optionally pull fresh data on first show (deduped).
    this._maybeRefreshOnRender();
  }

  get hass() {
    return this._hass;
  }

  /* --------------------------------------------------------------- helpers */

  _stateObj() {
    if (!this._hass || !this._config) return undefined;
    return this._hass.states[this._config.entity];
  }

  // The bound entity is "ready" once it exists and reports a usable state (not still coming up or
  // dropped out). This is the modern stand-in for the old attribute-based "sensor not ready" gate:
  // services target the watch via this entity's id (resolved to its device server-side), so the
  // only thing an action needs is for the entity to actually be alive. Gating every fire on it keeps
  // an auto-refresh (or an early tap) from firing the instant a dashboard loads and toasting an error.
  _isReady() {
    const s = this._stateObj();
    return !!(s && s.state !== "unavailable" && s.state !== "unknown" && s.state !== "");
  }

  _kind() {
    const s = this._stateObj();
    if (!s) return "alarm";
    return s.attributes && s.attributes.silent !== undefined ? "silent" : "alarm";
  }

  _getEntries() {
    const s = this._stateObj();
    if (!s || !s.attributes) return [];
    const list = this._kind() === "silent" ? s.attributes.silent : s.attributes.alarm;
    return Array.isArray(list) ? list : [];
  }

  _base() {
    // Services target HA devices; the card targets the watch by the entity it is bound to (the CRUD
    // handlers resolve `entity_id` -> its device -> the (account, watch)). No extra config needed.
    return { entity_id: [this._config.entity] };
  }

  _locale() {
    return (this._hass && this._hass.locale && this._hass.locale.language) || navigator.language || "en";
  }

  _dayNames(style) {
    // style: "short" | "long". Returns 7 names ordered Sun..Sat in the user's locale. Cached per
    // locale: building an Intl.DateTimeFormat is relatively expensive and this is called twice per
    // row on every render.
    const locale = this._locale();
    if (!this._dayCache || this._dayCache.locale !== locale) {
      const build = (st) => {
        try {
          const fmt = new Intl.DateTimeFormat(locale, { weekday: st });
          return REF_WEEK.map((d) => fmt.format(d));
        } catch (e) {
          return st === "long" ? DAY_FULL_FALLBACK : DAY_SHORT_FALLBACK;
        }
      };
      this._dayCache = { locale, short: build("short"), long: build("long") };
    }
    return this._dayCache[style];
  }

  _title() {
    if (this._config.title) return this._config.title;
    const s = this._stateObj();
    if (s && s.attributes && s.attributes.friendly_name) return s.attributes.friendly_name;
    return this._kind() === "silent" ? "Silent times" : "Alarms";
  }

  _notify(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _hhmm(value) {
    return value ? String(value).slice(0, 5) : "";
  }

  /* ------------------------------------------------- lazy element warm-up */

  async _ensureEditorElements() {
    if (EDITOR_ELEMENTS.every((t) => customElements.get(t))) return;
    try {
      // Instantiating a built-in card's config editor pulls HA's form elements into the registry
      // without us importing private frontend chunks.
      const helpers = window.loadCardHelpers && (await window.loadCardHelpers());
      if (helpers) {
        const el = await helpers.createCardElement({ type: "entities", entities: [] });
        if (el && el.constructor && el.constructor.getConfigElement) {
          await el.constructor.getConfigElement();
        }
      }
    } catch (e) {
      /* best effort — fall through to whenDefined below */
    }
    // Resolve once each element is defined, but never hang if one never loads.
    await Promise.allSettled(
      EDITOR_ELEMENTS.map((t) => Promise.race([customElements.whenDefined(t), new Promise((r) => setTimeout(r, 2000))]))
    );
  }

  /* -------------------------------------------------------------- services */

  // Dedup key for this watch's functions refresh -- shared across every card targeting the same
  // watch so an overview + alarm + silent card in one view only refresh the data set once.
  _refreshKey() {
    const a = (this._stateObj() || {}).attributes || {};
    return `${SERVICE.REFRESH_FUNCTIONS}|${a.entry_id || ""}|${a.wuid || ""}`;
  }

  // Explicit header-button refresh: always fires (records the dedup window so a co-rendered card's
  // auto-refresh doesn't immediately re-fire), and spins the icon until the call resolves.
  async _refreshNow() {
    if (!this._hass || this._refreshing || !this._isReady()) return; // no bound entity alive to refresh
    const base = this._base();
    this._refreshing = true;
    markRefreshed(this._refreshKey());
    if (this._mode === "list") this._render();
    try {
      await this._hass.callService(DOMAIN, SERVICE.REFRESH_FUNCTIONS, base, undefined, false);
    } catch (err) {
      this._notify(`Xplora watch: ${err && err.message ? err.message : "could not refresh"}`);
    } finally {
      this._refreshing = false;
      if (this._mode === "list") this._render();
    }
  }

  // Fire a one-time refresh when the card is first shown, if the user enabled "refresh on render".
  // Deduped so several cards in one view don't each refresh the same watch; the shared handle drives
  // the header refresh spinner (same indicator as the explicit refresh button) while it's in flight.
  _maybeRefreshOnRender() {
    if (this._autoRefreshDone) return;
    if (!this._isReady()) return; // bound entity not alive yet -- try again on the next hass push
    this._autoRefreshDone = true;
    if (!refreshOnRenderEnabled(this._stateObj())) return;
    const { promise, inflight } = trackInflight(this._refreshKey(), () =>
      this._hass.callService(DOMAIN, SERVICE.REFRESH_FUNCTIONS, this._base(), undefined, false)
    );
    if (!inflight) return; // suppressed within the dedup window -- nothing live to spin for
    this._setRefreshing(true);
    // The shared promise never rejects, so a failed run clears the spinner silently (no toast) --
    // the coordinator's fail-loud outcome surfaces in the list on the next state push.
    promise.then(() => this._setRefreshing(false));
  }

  // Toggle the header refresh spinner and re-render. Only re-renders in list mode (mirroring
  // `_refreshNow`) so an open add/edit popup isn't torn down mid-typing.
  _setRefreshing(on) {
    this._refreshing = on;
    if (this._mode === "list") this._render();
  }

  async _callService(service, payload) {
    // No-op until the bound entity is alive: an early save/toggle/delete tap would otherwise fire a
    // doomed call and surface a spurious "no watch" error toast.
    if (!this._hass || !this._isReady()) return;
    this._busy = true;
    this._refreshSaveButton();
    try {
      // notifyOnError=false: failures are surfaced via `_notify` below, so suppress HA's built-in
      // duplicate "Failed to perform the action …" snackbar.
      await this._hass.callService(DOMAIN, service, { ...this._base(), ...payload }, undefined, false);
      // Success: the integration refreshes the sensor itself; close the popup and show the list.
      this._busy = false;
      this._mode = "list";
      this._editingId = null;
      this._confirmId = null;
      this._error = "";
      this._render();
    } catch (err) {
      this._busy = false;
      this._notify(`Xplora watch: ${err && err.message ? err.message : "action failed"}`);
      // Preserve a half-filled popup on failure; only resync the list for list-side actions.
      if (this._mode === "list") this._render();
      else this._refreshSaveButton();
    }
  }

  _toggleEnabled(entry, enabled) {
    if (this._kind() === "silent") this._callService(SERVICE.SET_SILENT_ENABLED, { silent_id: entry.id, enabled });
    else this._callService(SERVICE.SET_ALARM_ENABLED, { alarm_id: entry.id, enabled });
  }

  _delete(entry) {
    if (this._kind() === "silent") this._callService(SERVICE.DELETE_SILENT, { silent_id: entry.id });
    else this._callService(SERVICE.DELETE_ALARM, { alarm_id: entry.id });
  }

  // Enable/disable every alarm or silent on this watch in one call (header bulk buttons). Reuses
  // `_callService`, which merges `_base()` (the device target) and re-renders on success/failure.
  _callBulk(enabled) {
    const silent = this._kind() === "silent";
    const svc = enabled
      ? silent
        ? SERVICE.TURN_ALL_SILENTS_ON
        : SERVICE.TURN_ALL_ALARMS_ON
      : silent
        ? SERVICE.TURN_ALL_SILENTS_OFF
        : SERVICE.TURN_ALL_ALARMS_OFF;
    this._callService(svc, {});
  }

  // The JSON service-data block for `create_alarm` / `create_silent` -- pasteable to reproduce this
  // entry. Mirrors what `_save()` sends (user/target come from `_base()`); alarms carry an optional
  // `name`, silents carry an `end`.
  _buildPayloadJson(entry) {
    const base = this._base();
    const data = { entity_id: base.entity_id, start: entry.start, weekdays: entry.weekdays || [] };
    if (this._kind() === "silent") data.end = entry.end;
    else if (entry.name) data.name = entry.name;
    return JSON.stringify(data, null, 2);
  }

  // A complete, paste-ready `set_alarm_enabled` / `set_silent_enabled` automation `action:` block
  // with this entry's id and current state pre-filled. Services target HA devices, so the call
  // targets the watch by the card's bound entity (the handler resolves it to the device). Hand-rolled
  // YAML (no YAML lib in the card); `q()` double-quotes scalars safely via JSON string encoding.
  _buildServiceCallYaml(entry) {
    const base = this._base();
    const silent = this._kind() === "silent";
    const svc = silent ? SERVICE.SET_SILENT_ENABLED : SERVICE.SET_ALARM_ENABLED;
    const idKey = silent ? "silent_id" : "alarm_id";
    const enabled = entry.status === "ENABLE";
    const q = (v) => JSON.stringify(v == null ? "" : String(v));
    return [
      `action: ${DOMAIN}.${svc}`,
      `target:`,
      `  entity_id:`,
      `    - ${q(base.entity_id[0])}`,
      `data:`,
      `  ${idKey}: ${q(entry.id)}`,
      `  enabled: ${enabled}`,
    ].join("\n");
  }

  // Open/close an overflow menu anchored at the clicked kebab `btn`. `kind` is "bulk" (header) or
  // "row" (per entry); clicking the same kebab again toggles it shut. The anchor rect is captured
  // now (jsdom returns zeros -- harmless, the menu still renders at 0,0 for tests) and the menu is
  // flipped above the button when it would overflow the viewport bottom.
  _toggleMenu(kind, id, btn) {
    const same = this._menu && this._menu.kind === kind && this._menu.id === (id || null);
    if (same) {
      this._menu = null;
      this._render();
      return;
    }
    const r = btn.getBoundingClientRect();
    const itemCount = kind === "bulk" ? 2 : 3;
    const estHeight = itemCount * 44 + 12;
    let top = r.bottom + 4;
    if (top + estHeight > window.innerHeight) top = Math.max(8, r.top - estHeight - 4);
    // `left` is the kebab's right edge; the menu grows leftward from it (CSS translateX(-100%)).
    this._menu = { kind, id: id || null, top, left: r.right };
    this._render();
  }

  // Write `text` to the clipboard and confirm via a toast (the originating menu closes on copy, so an
  // in-place icon flash would vanish). Falls back to a hidden <textarea> + execCommand for non-secure
  // contexts where `navigator.clipboard` is unavailable (e.g. plain-HTTP HA dashboards).
  _copyToClipboard(text, label) {
    const write = (t) => {
      if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t);
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      try {
        document.execCommand("copy");
      } catch (e) {
        /* ignore -- handled by the rejected/empty promise path below */
      }
      document.body.removeChild(ta);
      return Promise.resolve();
    };
    Promise.resolve(write(text))
      .then(() => this._notify(label ? `Copied ${label}` : "Copied to clipboard"))
      .catch(() => this._notify("Xplora watch: could not copy to clipboard"));
  }

  _save() {
    const kind = this._kind();
    const f = this._form;
    const days = DAY_KEYS.filter((k) => f.days.has(k)); // canonical order

    // Validation -----------------------------------------------------------
    if (days.length === 0) return this._setError("Pick at least one day.");
    if (!f.start) return this._setError("Set a time.");
    if (kind === "silent") {
      if (!f.end) return this._setError("Set an end time.");
      if (f.end === f.start) return this._setError("Start and end can't be the same.");
    }

    if (kind === "silent") {
      if (this._mode === "add") this._callService(SERVICE.CREATE_SILENT, { start: f.start, end: f.end, weekdays: days });
      else this._callService(SERVICE.UPDATE_SILENT, { silent_id: this._editingId, start: f.start, end: f.end, weekdays: days });
    } else {
      const name = (f.name || "").trim();
      if (this._mode === "add") {
        const p = { start: f.start, weekdays: days };
        if (name) p.name = name;
        this._callService(SERVICE.CREATE_ALARM, p);
      } else {
        this._callService(SERVICE.UPDATE_ALARM, { alarm_id: this._editingId, start: f.start, weekdays: days, name });
      }
    }
  }

  _setError(msg) {
    this._error = msg;
    const errEl = this._modal && this._modal.querySelector("#f-err");
    if (errEl) {
      errEl.textContent = msg;
      errEl.classList.toggle("show", !!msg);
    } else {
      this._render();
    }
  }

  _clearError() {
    if (!this._error) return;
    this._error = "";
    const errEl = this._modal && this._modal.querySelector("#f-err");
    if (errEl) {
      errEl.textContent = "";
      errEl.classList.remove("show");
    }
  }

  _refreshSaveButton() {
    const btn = this._modal && this._modal.querySelector('[data-act="save"]');
    if (!btn) return;
    btn.disabled = this._busy;
    btn.textContent = this._busy ? "Saving…" : "Save";
  }

  /* ------------------------------------------------------------ mode switch */

  async _openAdd() {
    this._mode = "add";
    this._editingId = null;
    this._error = "";
    this._form = {
      name: "",
      start: this._kind() === "silent" ? "08:00" : "07:00",
      end: "12:00",
      days: new Set(PRESETS.weekdays),
    };
    const warm = EDITOR_ELEMENTS.every((t) => customElements.get(t));
    this._render(); // opens the popup instantly
    await this._ensureEditorElements();
    // Only rebuild the popup if elements weren't ready at first paint; once warm the first build
    // is already complete, so re-syncing would be wasted work on every open.
    if (!warm && this._mode !== "list") this._syncModal();
  }

  async _openEdit(entry) {
    this._mode = "edit";
    this._editingId = entry.id;
    this._error = "";
    this._form = {
      name: entry.name || "",
      start: this._hhmm(entry.start),
      end: this._hhmm(entry.end),
      days: new Set(Array.isArray(entry.weekdays) ? entry.weekdays : []),
    };
    const warm = EDITOR_ELEMENTS.every((t) => customElements.get(t));
    this._render();
    await this._ensureEditorElements();
    if (!warm && this._mode !== "list") this._syncModal();
  }

  _cancel() {
    this._mode = "list";
    this._editingId = null;
    this._error = "";
    this._form = null;
    this._render();
  }

  /* ---------------------------------------------------------------- render */

  _render() {
    if (!this._built) this._buildShell();
    // Match native controls (e.g. <input type="time">) to HA's actual theme brightness.
    const dark = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    this.style.setProperty("--xplora-color-scheme", dark ? "dark" : "light");
    const s = this._stateObj();
    this._root.innerHTML = s ? this._listTemplate() : this._loadingTemplate();
    if (s) this._wireList();
    this._syncModal();
  }

  _buildShell() {
    const style = document.createElement("style");
    style.textContent = this._css();
    this.shadowRoot.appendChild(style);

    this._card = document.createElement("ha-card");
    this.shadowRoot.appendChild(this._card);

    this._root = document.createElement("div");
    this._root.className = "wrap";
    this._card.appendChild(this._root);

    this._built = true;
  }

  /* ------------------------------------------------------- view templates */

  _header() {
    const icon = this._kind() === "silent" ? "mdi:volume-off" : "mdi:alarm";
    // Bulk enable/disable lives behind a single overflow (3-dot) menu so the header stays uncluttered.
    // Hidden when the list is empty -- there is nothing to toggle, and the empty-state CTA owns that view.
    const menu =
      this._getEntries().length > 0
        ? `
          <ha-icon-button class="menu-btn" data-act="menu-bulk" label="More actions" title="More actions">
            <ha-icon icon="mdi:dots-vertical"></ha-icon>
          </ha-icon-button>`
        : "";
    // Refresh re-fetches this watch's alarms/silent times on demand (the data is OFF by default),
    // mirroring the chat card's header refresh. The icon spins while a refresh is in flight.
    return `
      <div class="header">
        <div class="title">
          <ha-icon class="title-icon" icon="${icon}"></ha-icon>
          <span>${this._esc(this._title())}</span>
        </div>
        <div class="header-actions">
          ${menu}
          <ha-icon-button class="refresh-btn" data-act="refresh" label="Refresh">
            <ha-icon class="${this._refreshing ? "spin" : ""}" icon="mdi:refresh"></ha-icon>
          </ha-icon-button>
        </div>
      </div>`;
  }

  _loadingTemplate() {
    return `
      ${this._header()}
      <div class="placeholder">
        <ha-icon icon="mdi:watch"></ha-icon>
        ${entityStatusHtml(this._hass, this._config.entity, (v) => this._esc(v))}
      </div>`;
  }

  _pills(weekdays) {
    const set = new Set(Array.isArray(weekdays) ? weekdays : []);
    const all = DAY_KEYS.every((k) => set.has(k));
    const short = this._dayNames("short");
    const full = this._dayNames("long");
    const inner = DAY_KEYS.map((k, i) => {
      const on = set.has(k) ? " on" : "";
      return `<span class="pill${on}" title="${this._esc(full[i])}">${this._esc(short[i])}</span>`;
    }).join("");
    return `<div class="pills" aria-label="${all ? "Every day" : ""}">${inner}</div>`;
  }

  _row(entry) {
    const kind = this._kind();
    const disabled = entry.status !== "ENABLE";
    const checked = disabled ? "" : " checked";

    const timeHtml =
      kind === "silent"
        ? `<span class="time">${this._esc(this._hhmm(entry.start))}</span><span class="dash">–</span><span class="time">${this._esc(this._hhmm(entry.end))}</span>`
        : `<span class="time big">${this._esc(this._hhmm(entry.start))}</span>`;

    const nameHtml = kind === "alarm" && entry.name ? `<div class="entry-name">${this._esc(entry.name)}</div>` : "";

    const actions =
      this._confirmId === entry.id
        ? `<div class="confirm">
             <span class="confirm-q">Delete?</span>
             <ha-button appearance="plain" variant="neutral" size="s" data-act="cancel-delete">Cancel</ha-button>
             <ha-button appearance="filled" variant="danger" size="s" data-act="do-delete" data-id="${entry.id}">Delete</ha-button>
           </div>`
        : `<div class="row-actions">
             <ha-icon-button class="menu-btn" data-act="menu-row" data-id="${entry.id}" label="Copy options" title="Copy options">
               <ha-icon icon="mdi:dots-vertical"></ha-icon>
             </ha-icon-button>
             <ha-icon-button data-act="edit" data-id="${entry.id}" label="Edit">
               <ha-icon icon="mdi:pencil"></ha-icon>
             </ha-icon-button>
             <ha-icon-button data-act="ask-delete" data-id="${entry.id}" label="Delete">
               <ha-icon icon="mdi:trash-can-outline"></ha-icon>
             </ha-icon-button>
           </div>`;

    return `
      <li class="row${disabled ? " dim" : ""}">
        <div class="row-top">
          <div class="row-info">
            <div class="time-line">${timeHtml}</div>
            ${nameHtml}
          </div>
          <ha-switch class="sw" data-act="toggle" data-id="${entry.id}"${checked}></ha-switch>
        </div>
        <div class="row-bottom">
          ${this._pills(entry.weekdays)}
          ${actions}
        </div>
      </li>`;
  }

  _listTemplate() {
    const entries = this._getEntries();
    const kind = this._kind();
    const addLabel = kind === "silent" ? "Add silent time" : "Add alarm";

    if (entries.length === 0) {
      return `
        ${this._header()}
        <div class="empty">
          <ha-icon icon="${kind === "silent" ? "mdi:volume-off" : "mdi:alarm-off"}"></ha-icon>
          <div class="empty-title">No ${kind === "silent" ? "silent times" : "alarms"} yet</div>
          <div class="empty-sub">Create one to get started.</div>
          <ha-button class="cta" appearance="filled" variant="brand" data-act="add">
            <ha-icon slot="start" icon="mdi:plus"></ha-icon>${addLabel}
          </ha-button>
        </div>
        ${this._menuTemplate()}`;
    }

    return `
      ${this._header()}
      <ul class="list">
        ${entries.map((e) => this._row(e)).join("")}
      </ul>
      <div class="footer">
        <ha-button class="cta full" appearance="filled" variant="brand" data-act="add">
          <ha-icon slot="start" icon="mdi:plus"></ha-icon>${addLabel}
        </ha-button>
      </div>
      ${this._menuTemplate()}`;
  }

  // The overflow (3-dot) popover. `this._menu` (set by `_toggleMenu`) is either null, the header
  // bulk menu ({kind:"bulk"}), or a per-row copy menu ({kind:"row", id}). Rendered as a fixed-position
  // panel anchored at the kebab so the card's `overflow:hidden` can't clip it; a transparent backdrop
  // behind it closes the menu on an outside click (same technique as the edit modal).
  _menuTemplate() {
    const m = this._menu;
    if (!m) return "";
    const silent = this._kind() === "silent";
    const items =
      m.kind === "bulk"
        ? [
            { act: "bulk-on", icon: "mdi:checkbox-multiple-marked-outline", label: silent ? "Enable all silent times" : "Enable all alarms" },
            { act: "bulk-off", icon: "mdi:checkbox-multiple-blank-outline", label: silent ? "Disable all silent times" : "Disable all alarms" },
          ]
        : [
            { act: "copy-id", icon: "mdi:identifier", label: "Copy ID" },
            { act: "copy-call", icon: "mdi:script-text-outline", label: "Copy service call" },
            { act: "copy-payload", icon: "mdi:code-json", label: "Copy payload" },
          ];
    const dataId = m.kind === "row" ? ` data-id="${m.id}"` : "";
    const rows = items
      .map(
        (it) => `
        <button class="menu-item" type="button" role="menuitem" data-act="${it.act}"${dataId}>
          <ha-icon icon="${it.icon}"></ha-icon><span>${this._esc(it.label)}</span>
        </button>`,
      )
      .join("");
    return `
      <div class="menu-backdrop" data-act="menu-close"></div>
      <div class="menu" role="menu" style="top:${m.top}px; left:${m.left}px;">${rows}</div>`;
  }

  /* ---------------------------------------------------------- editor modal */

  _syncModal() {
    const open = this._mode === "add" || this._mode === "edit";

    if (!open) {
      if (this._keyBound) {
        window.removeEventListener("keydown", this._onKeyDown);
        this._keyBound = false;
      }
      if (this._modal) {
        this._modal.hidden = true;
        this._modal.innerHTML = "";
      }
      return;
    }

    if (!this._modal) {
      this._modal = document.createElement("div");
      this._modal.className = "modal-host";
      this.shadowRoot.appendChild(this._modal);
    }
    this._modal.hidden = false;
    this._modal.innerHTML = this._modalHtml();
    this._wireEditor(this._modal);

    if (!this._keyBound) {
      window.addEventListener("keydown", this._onKeyDown);
      this._keyBound = true;
    }
  }

  _modalHtml() {
    const kind = this._kind();
    const f = this._form;
    const isAdd = this._mode === "add";
    const titleTxt = (isAdd ? "Add " : "Edit ") + (kind === "silent" ? "silent time" : "alarm");
    const startLabel = kind === "silent" ? "From" : "Time";

    const endField =
      kind === "silent"
        ? `<div class="field grow">
             <span class="field-label">To</span>
             <input type="time" id="f-end" class="time-input" value="${this._esc(f.end || "")}" />
           </div>`
        : "";

    const nameField =
      kind === "alarm"
        ? `<div class="field">
             <span class="field-label">Name <span class="opt">(optional)</span></span>
             <ha-input id="f-name" placeholder="e.g. Wake up"></ha-input>
           </div>`
        : "";

    const cFull = this._dayNames("long");
    // Custom toggle chips (plain buttons): the checkmark always reserves its box, so selecting a
    // day never changes a chip's width and the row never reflows/jumps.
    const chips = DAY_KEYS.map((k, i) => {
      const on = f.days.has(k);
      return `<button type="button" class="day-chip${on ? " on" : ""}" data-day="${k}" aria-pressed="${on}">
                <ha-icon class="day-check" icon="mdi:check"></ha-icon>
                <span class="day-name">${this._esc(cFull[i])}</span>
              </button>`;
    }).join("");

    return `
      <div class="backdrop" data-act="cancel"></div>
      <div class="panel" role="dialog" aria-modal="true" aria-label="${this._esc(titleTxt)}">
        <div class="panel-header">
          <span class="panel-title">${this._esc(titleTxt)}</span>
          <ha-icon-button class="panel-close" data-act="cancel" label="Close">
            <ha-icon icon="mdi:close"></ha-icon>
          </ha-icon-button>
        </div>

        <div class="panel-body">
          <div class="time-row">
            <div class="field grow">
              <span class="field-label">${startLabel}</span>
              <input type="time" id="f-start" class="time-input" value="${this._esc(f.start || "")}" />
            </div>
            ${endField}
          </div>

          ${nameField}

          <div class="field">
            <div class="field-label-row">
              <span class="field-label">Repeat</span>
              <div class="presets">
                <ha-button appearance="plain" variant="brand" size="s" data-preset="everyday">Every day</ha-button>
                <ha-button appearance="plain" variant="brand" size="s" data-preset="weekdays">Weekdays</ha-button>
                <ha-button appearance="plain" variant="brand" size="s" data-preset="weekend">Weekend</ha-button>
              </div>
            </div>
            <div class="chips" id="f-chips">${chips}</div>
          </div>

          <div class="err${this._error ? " show" : ""}" id="f-err">${this._esc(this._error)}</div>
        </div>

        <div class="panel-footer">
          <ha-button appearance="plain" variant="neutral" data-act="cancel">Cancel</ha-button>
          <ha-button appearance="filled" variant="brand" data-act="save"${this._busy ? " disabled" : ""}>
            ${this._busy ? "Saving…" : "Save"}
          </ha-button>
        </div>
      </div>`;
  }

  /* ------------------------------------------------------------- wiring */

  _wireList() {
    this._root.querySelectorAll("[data-act]").forEach((el) => {
      const act = el.getAttribute("data-act");
      if (act === "toggle") return; // handled below
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const id = el.getAttribute("data-id");
        const entry = this._getEntries().find((e) => e.id === id);
        switch (act) {
          case "add":
            this._openAdd();
            break;
          case "edit":
            if (entry) this._openEdit(entry);
            break;
          case "ask-delete":
            this._confirmId = id;
            this._render();
            break;
          case "cancel-delete":
            this._confirmId = null;
            this._render();
            break;
          case "do-delete":
            if (entry) this._delete(entry);
            break;
          case "menu-bulk":
            this._toggleMenu("bulk", null, el);
            break;
          case "menu-row":
            this._toggleMenu("row", id, el);
            break;
          case "menu-close":
            this._menu = null;
            this._render();
            break;
          case "copy-id":
            this._menu = null;
            if (entry) this._copyToClipboard(entry.id, "ID");
            this._render();
            break;
          case "copy-call":
            this._menu = null;
            if (entry) this._copyToClipboard(this._buildServiceCallYaml(entry), "service call");
            this._render();
            break;
          case "copy-payload":
            this._menu = null;
            if (entry) this._copyToClipboard(this._buildPayloadJson(entry), "payload");
            this._render();
            break;
          case "bulk-on":
            this._menu = null;
            this._callBulk(true);
            break;
          case "bulk-off":
            this._menu = null;
            this._callBulk(false);
            break;
          case "refresh":
            this._refreshNow();
            break;
        }
      });
    });

    this._root.querySelectorAll('ha-switch[data-act="toggle"]').forEach((sw) => {
      sw.addEventListener("change", (ev) => {
        const id = sw.getAttribute("data-id");
        const entry = this._getEntries().find((e) => e.id === id);
        if (entry) this._toggleEnabled(entry, !!ev.target.checked);
      });
    });
  }

  _wireEditor(scope) {
    // Time inputs: native <input type="time"> — plain "HH:MM" string value, `input` event.
    const wireTime = (id, key) => {
      const el = scope.querySelector("#" + id);
      if (!el) return;
      el.addEventListener("input", () => {
        this._form[key] = el.value;
        this._clearError();
      });
    };
    wireTime("f-start", "start");
    wireTime("f-end", "end");

    // Name: <ha-input> (HA's text field; `value-changed` event).
    const nameEl = scope.querySelector("#f-name");
    if (nameEl) {
      nameEl.value = this._form.name || "";
      nameEl.addEventListener("value-changed", (ev) => {
        this._form.name = (ev.detail && ev.detail.value) || "";
      });
    }

    // Weekday chips: the form model is the source of truth; repaint each chip from it.
    const paintChip = (chip) => {
      const on = this._form.days.has(chip.getAttribute("data-day"));
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", String(on));
    };
    scope.querySelectorAll(".day-chip[data-day]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const day = chip.getAttribute("data-day");
        if (this._form.days.has(day)) this._form.days.delete(day);
        else this._form.days.add(day);
        paintChip(chip);
        this._clearError();
      });
    });

    // Presets.
    scope.querySelectorAll("[data-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._form.days = new Set(PRESETS[btn.getAttribute("data-preset")]);
        scope.querySelectorAll(".day-chip[data-day]").forEach(paintChip);
        this._clearError();
      });
    });

    // Close (backdrop, X and Cancel all carry data-act="cancel") and Save.
    scope.querySelectorAll('[data-act="cancel"]').forEach((el) => el.addEventListener("click", () => this._cancel()));
    const saveBtn = scope.querySelector('[data-act="save"]');
    if (saveBtn) saveBtn.addEventListener("click", () => this._save());
  }

  /* ----------------------------------------------------------------- util */

  _esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ------------------------------------------------------------------ css */

  _css() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .wrap { color: var(--primary-text-color); }

      .header { display: flex; align-items: center; padding: 16px 16px 8px 16px; }
      .title { display: flex; align-items: center; gap: 10px; font-size: 1.25rem; font-weight: 500; line-height: 1.2; min-width: 0; }
      .title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .title-icon { color: var(--primary-color); --mdc-icon-size: 22px; flex: 0 0 auto; }
      .header-actions { margin-left: auto; display: flex; align-items: center; flex: 0 0 auto; }
      .menu-btn, .refresh-btn { flex: 0 0 auto; color: var(--secondary-text-color); }
      @keyframes xplora-spin { to { transform: rotate(360deg); } }
      .refresh-btn ha-icon.spin { animation: xplora-spin 0.9s linear infinite; }

      /* ---- list ---- */
      .list { list-style: none; margin: 0; padding: 4px 8px; }
      .row { padding: 12px 8px; border-radius: 12px; transition: opacity 0.15s ease; }
      .row + .row { border-top: 1px solid var(--divider-color); }
      .row.dim { opacity: 0.55; }

      .row-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
      .row-info { min-width: 0; }
      .time-line { display: flex; align-items: baseline; gap: 6px; white-space: nowrap; }
      .time { font-variant-numeric: tabular-nums; font-size: 1.5rem; font-weight: 500; letter-spacing: 0.5px; }
      .time.big { font-size: 1.9rem; }
      .dash { color: var(--secondary-text-color); font-size: 1.2rem; }
      .entry-name { margin-top: 2px; color: var(--secondary-text-color); font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60vw; }

      .row-bottom { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }

      .pills { display: flex; gap: 4px; flex-wrap: wrap; }
      .pill { min-width: 20px; height: 22px; padding: 0 7px; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; border-radius: 11px; font-size: 0.72rem; font-weight: 600; color: var(--secondary-text-color); background: var(--secondary-background-color); border: 1px solid var(--divider-color); }
      .pill.on { color: var(--text-primary-color, #fff); background: var(--primary-color); border-color: var(--primary-color); }

      .row-actions { display: flex; gap: 4px; justify-content: flex-end; }
      ha-icon-button { --mdc-icon-button-size: 44px; color: var(--secondary-text-color); }
      ha-switch { --mdc-theme-secondary: var(--primary-color); }

      /* ---- overflow (3-dot) menu ---- */
      .menu-backdrop { position: fixed; inset: 0; z-index: 8; }
      .menu {
        position: fixed; z-index: 9; transform: translateX(-100%);
        min-width: 200px; padding: 6px; box-sizing: border-box;
        background: var(--card-background-color, var(--ha-card-background, #fff));
        border: 1px solid var(--divider-color); border-radius: 12px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.24);
      }
      .menu-item {
        display: flex; align-items: center; gap: 12px; width: 100%;
        padding: 10px 12px; border: 0; border-radius: 8px; background: none; cursor: pointer;
        font: inherit; font-size: 0.95rem; text-align: left; color: var(--primary-text-color);
      }
      .menu-item:hover, .menu-item:focus-visible { background: var(--secondary-background-color); outline: none; }
      .menu-item ha-icon { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: 0 0 auto; }

      .confirm { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
      .confirm-q { color: var(--error-color); font-weight: 500; font-size: 0.92rem; }

      /* ---- footer / CTA ---- */
      .footer { padding: 8px 16px 16px 16px; }
      .cta.full { width: 100%; }

      /* ---- empty ---- */
      .empty { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 28px 20px 32px 20px; }
      .empty ha-icon { --mdc-icon-size: 48px; color: var(--disabled-text-color); margin-bottom: 4px; }
      .empty-title { font-size: 1.1rem; font-weight: 500; }
      .empty-sub { color: var(--secondary-text-color); margin-bottom: 14px; }

      /* ---- loading placeholder ---- */
      .placeholder { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 10px; padding: 26px 20px 30px 20px; color: var(--secondary-text-color); }
      .placeholder ha-icon { --mdc-icon-size: 40px; color: var(--disabled-text-color); }
      .placeholder code { background: var(--secondary-background-color); padding: 2px 6px; border-radius: 6px; font-size: 0.85rem; }

      /* ---- editor modal ---- */
      .modal-host { position: fixed; inset: 0; z-index: 9; display: flex; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; }
      .modal-host[hidden] { display: none; }
      .backdrop { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.46); }
      .panel {
        position: relative; z-index: 1;
        width: 100%; max-width: 460px;
        max-height: calc(100% - 16px);
        display: flex; flex-direction: column;
        background: var(--card-background-color, var(--ha-card-background, #fff));
        color: var(--primary-text-color);
        border-radius: var(--ha-card-border-radius, 16px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
        /* Hint native controls (e.g. <input type=time>) to match the HA theme brightness. */
        color-scheme: var(--xplora-color-scheme, light dark);
      }
      .panel-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 8px 8px 20px; }
      .panel-title { font-size: 1.2rem; font-weight: 500; }
      .panel-close { color: var(--secondary-text-color); }
      .panel-body { padding: 4px 20px 8px; overflow: auto; }
      .panel-footer { display: flex; justify-content: flex-end; gap: 10px; padding: 8px 16px 16px; }

      .time-row { display: flex; gap: 12px; flex-wrap: wrap; }
      .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
      .field.grow { flex: 1; min-width: 140px; }
      .field-label { font-size: 0.85rem; color: var(--secondary-text-color); font-weight: 500; }
      .opt { font-weight: 400; opacity: 0.8; }
      .field-label-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
      ha-input { width: 100%; }

      .time-input {
        font: inherit; font-size: 1.05rem; font-variant-numeric: tabular-nums;
        color: var(--primary-text-color);
        background: var(--secondary-background-color);
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        padding: 0 12px; min-height: 46px; width: 100%; box-sizing: border-box;
      }
      .time-input:focus { outline: none; border-color: var(--primary-color); }

      .presets { display: flex; gap: 4px; flex-wrap: wrap; }
      .chips { display: flex; flex-wrap: wrap; gap: 8px; }
      .day-chip {
        -webkit-appearance: none; appearance: none; cursor: pointer;
        display: inline-flex; align-items: center; gap: 6px;
        min-height: 44px; padding: 0 14px; box-sizing: border-box;
        border-radius: 22px;
        font: inherit; font-weight: 600; font-size: 0.9rem;
        color: var(--primary-text-color);
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color);
        transition: background-color 0.12s ease, border-color 0.12s ease;
      }
      .day-chip.on { background: var(--secondary-background-color); border-color: var(--secondary-background-color); }
      /* The check always occupies its box (visibility, not display) so toggling a day never
         changes the chip's width — the chips don't reflow/jump. */
      .day-check { --mdc-icon-size: 18px; width: 18px; height: 18px; flex: 0 0 auto; visibility: hidden; }
      .day-chip.on .day-check { visibility: visible; }

      .err { color: var(--error-color); font-size: 0.88rem; max-height: 0; overflow: hidden; opacity: 0; transition: opacity 0.15s ease; }
      .err.show { max-height: 60px; opacity: 1; margin-bottom: 6px; }
    `;
  }
}

if (!customElements.get("xplora-watch-card")) {
  customElements.define("xplora-watch-card", XploraWatchCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "xplora-watch-card",
  name: "Xplora Watch Alarms / Silent Times",
  description: "View and manage a watch's alarms or silent-time windows (list, add, edit, delete, enable/disable).",
  preview: true,
});

/* ---------------------------------------------------------------------------------------------
 * Shared "last update" status. The outcome is computed server-side and surfaced by the integration's
 * `last_update` sensor (state = ok | no_response | error), so it is authoritative, shared across
 * users/devices and also reflects background polls. Cards just map that sensor's state to a colour:
 * success = the watch reported new data, warning = the watch did not respond (off / out of reach),
 * error = the request failed.
 * ------------------------------------------------------------------------------------------- */
const STATUS_META = {
  success: { color: "var(--success-color, #43a047)", icon: "mdi:check-circle", label: "Updated" },
  warning: { color: "var(--warning-color, #ffa600)", icon: "mdi:alert", label: "Watch didn't respond" },
  error: { color: "var(--error-color, #db4437)", icon: "mdi:close-circle", label: "Update failed" },
  // Neutral map-banner state when there is no poll outcome to report (last_update unknown/disabled)
  // -- carries the fix age without falsely claiming "Updated". Never produced by statusFromSensor.
  unknown: { color: "var(--secondary-text-color, #888)", icon: "mdi:map-marker", label: "Location" },
};

// Map a `last_update` sensor state object to a status key, or null if unknown/not yet reported.
function statusFromSensor(s) {
  if (!s) return null;
  if (s.state === "ok") return "success";
  if (s.state === "no_response") return "warning";
  if (s.state === "error" || s.state === "unavailable") return "error";
  return null;
}

function relTime(ts) {
  if (!ts) return "";
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

// Relative time from an ISO timestamp (e.g. a state object's last_updated).
function relTimeIso(iso) {
  const t = iso ? Date.parse(iso) : NaN;
  return isNaN(t) ? "" : relTime(t);
}

// The watch's fix time (ISO `last tracking`) from a device_tracker state object, or null. One
// definition so every surface -- overview chip/banner AND the controls status line -- reads the
// attribute the same way and can't drift (ADR 0007).
function trackerFixIso(st) {
  return (st && st.attributes && st.attributes["last tracking"]) || null;
}

// Shared basis for the header chip AND the map popup banner, so the two can never drift (ADR 0007).
// STATUS is the poll outcome (the backend `last_update` sensor); the fix time is the WATCH's own
// capture time (the tracker's ISO `last tracking` attribute) -- i.e. how old the shown position is,
// NOT when we last polled. Each surface reads this one place and formats it its own way.
export function fixAgeStatus(hass, entities) {
  const lu = entities && entities.lastupdate ? hass.states[entities.lastupdate] : undefined;
  const trk = entities && entities.tracker ? hass.states[entities.tracker] : undefined;
  return { status: statusFromSensor(lu), fixIso: trackerFixIso(trk) };
}

// Banner phrasing for the fix age (ADR 0007 wording (i)): anchored to "location" so a relative age
// can't be misread as "when the poll happened". Empty when the fix time is unknown -> caller omits.
export function locationAgePhrase(fixIso) {
  const rel = relTimeIso(fixIso);
  if (!rel) return "";
  return rel === "just now" ? "location just now" : `location from ${rel}`;
}

// Module-level HTML escaper, shared by the generic popup host and the map card. Each card also has
// its own `_esc` method (kept as-is); this exists so module-level helpers don't reach into an
// instance.
function escapeHtml(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    // Also escape the single quote: the map card interpolates values into a single-quoted CSS
    // `url('…')`, so an apostrophe would otherwise break out of the string (defense in depth).
    .replace(/'/g, "&#39;");
}

/* ---------------------------------------------------------------------------------------------
 * Generic full-screen (or compact) popup host. Extracted from the overview card so BOTH it and the
 * map card can reuse it (ADR 0008): it mounts ANY card element in a modal overlay and keeps it
 * hass-live. It is deliberately dumb about what it mounts -- the map's banner / reload / fix-age
 * logic lives in the map card now, NOT here. `fill` makes the popup take most of the screen and
 * stretch its content to full height (the map); compact popups (alarm/silent, controls, chat) size
 * to their content.
 * ------------------------------------------------------------------------------------------- */
const POPUP_HOST_CSS = `
  .modal-host { position: fixed; inset: 0; z-index: 9; display: flex; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; }
  .modal-host[hidden] { display: none; }
  .backdrop { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.46); }
  .card-popup { position: relative; z-index: 1; width: 100%; max-width: 480px; max-height: 100%; display: flex; flex-direction: column; }
  .card-popup.fill { max-width: 1000px; height: 100%; }
  .popup-bar { display: flex; justify-content: flex-end; margin-bottom: 4px; flex: 0 0 auto; }
  .popup-close { color: #fff; }
  .popup-slot { overflow: auto; }
  .card-popup.fill .popup-slot { flex: 1; min-height: 0; display: flex; }
  .card-popup.fill .popup-slot > * { flex: 1; min-height: 0; }
  .popup-error { color: var(--error-color); background: var(--card-background-color); padding: 16px; border-radius: 12px; }
`;

class CardPopupHost {
  // `root` is the shadow root to mount into; `getHass` returns the owner's current hass so the
  // embedded card can be built and kept live without the host storing a stale copy.
  constructor(root, getHass) {
    this._root = root;
    this._getHass = getHass;
    this._modal = null;
    this._embedded = null; // the card element currently shown (kept hass-live), or null
    this._styleInjected = false;
    this._openGen = 0; // bumped on every open()/close() so a stale async builder can't clobber a newer popup
    this._onKeyDown = (ev) => {
      if (ev.key === "Escape" && this.isOpen()) this.close();
    };
  }

  isOpen() {
    return !!(this._modal && !this._modal.hidden);
  }

  // Push a fresh hass to the embedded card while the popup is open (the owner calls this from its
  // own `set hass`). The embedded card recomputes itself -- e.g. a map card re-derives its banner.
  setHass(hass) {
    if (this._embedded) this._embedded.hass = hass;
  }

  // Open a popup hosting the card returned by `builder` (sync or async). Close wiring is set up
  // before the (possibly async) build so the popup can be dismissed while it loads.
  async open(builder, opts = {}) {
    const hass = this._getHass();
    if (!hass) return;
    if (!this._styleInjected) {
      const style = document.createElement("style");
      style.textContent = POPUP_HOST_CSS;
      this._root.appendChild(style);
      this._styleInjected = true;
    }
    if (!this._modal) {
      this._modal = document.createElement("div");
      this._modal.className = "modal-host";
      this._root.appendChild(this._modal);
    }
    // A fresh open supersedes any still-pending build from an earlier open()/close() (the async
    // builder below re-checks this token, so it can't append into a slot that has been replaced).
    const gen = ++this._openGen;
    this._modal.hidden = false;
    this._modal.innerHTML = `
      <div class="backdrop" data-close></div>
      <div class="card-popup${opts.fill ? " fill" : ""}">
        <div class="popup-bar">
          <ha-icon-button class="popup-close" data-close label="Close"><ha-icon icon="mdi:close"></ha-icon></ha-icon-button>
        </div>
        <div class="popup-slot"></div>
      </div>`;
    const slot = this._modal.querySelector(".popup-slot");
    this._modal.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", () => this.close()));
    window.addEventListener("keydown", this._onKeyDown);

    try {
      const card = await builder();
      if (gen !== this._openGen || this._modal.hidden) return; // superseded by a newer open/close, or dismissed
      card.hass = hass;
      if (opts.fill) card.style.height = "100%";
      this._embedded = card;
      slot.appendChild(card);
    } catch (e) {
      if (gen === this._openGen && !this._modal.hidden) {
        slot.innerHTML = `<div class="popup-error">Could not open: ${escapeHtml(e && e.message)}</div>`;
      }
    }
  }

  close() {
    this._openGen++; // abort any in-flight build so it can't mount after we've closed
    window.removeEventListener("keydown", this._onKeyDown);
    this._embedded = null;
    if (this._modal) {
      this._modal.hidden = true;
      this._modal.innerHTML = "";
    }
  }
}

/**
 * Xplora® Watch controls card.
 *
 * Renders a watch's `button.*` action entities (update / restart / shutdown) and wraps the
 * destructive ones in an "Are you sure?" confirmation BY DEFAULT, so users get a guard without
 * configuring per-card `tap_action.confirmation` themselves. `update` (a harmless refresh) fires
 * immediately; everything else (restart/shutdown) confirms first. Built on HA's own ha-button /
 * ha-icon and a self-built confirm overlay (themed div), like the alarm/silent card.
 *
 * Config:
 *   type: custom:xplora-watch-actions-card
 *   title: Kid One — Controls                    # optional
 *   entities:                                     # the watch's button entities (any subset)
 *     - button.xplora_kid_one_watch_update
 *     - button.xplora_kid_one_watch_reboot
 *     - button.xplora_kid_one_watch_shutdown
 *   # `entity:` (single) is also accepted as a shorthand for a one-button card.
 */
class XploraWatchActionsCard extends HTMLElement {
  // Per-action presentation, keyed by the button entity_id suffix. `confirm:false` means the
  // press fires immediately; everything not listed defaults to confirm-required (safe default).
  // Keyed by the entity's `xplora_role` (its integration-emitted role, ADR 0005), not an id suffix.
  static ACTIONS = {
    update: { label: "Update", icon: "mdi:refresh", appearance: "filled", variant: "brand", confirm: false },
    // `refresh_functions` re-fetches alarms/silent times; harmless, so it fires without a confirm.
    refresh_functions: {
      label: "Refresh Alarms & Silent Times",
      icon: "mdi:calendar-refresh",
      appearance: "outlined",
      variant: "neutral",
      confirm: false,
    },
    reboot: { label: "Restart", icon: "mdi:restart", appearance: "outlined", variant: "neutral", confirm: true },
    shutdown: { label: "Turn off", icon: "mdi:power", appearance: "outlined", variant: "danger", confirm: true },
  };

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._sig = ""; // signature of rendered entities, to skip needless re-renders
    this._modal = null; // confirm overlay host
    this._pending = null; // entity_id awaiting confirmation
    this._busy = false;
    this._onKeyDown = (ev) => {
      if (ev.key === "Escape" && this._pending) this._closeConfirm();
    };
  }

  setConfig(config) {
    const entities = config && (config.entities || (config.entity ? [config.entity] : null));
    if (!Array.isArray(entities) || entities.length === 0) {
      throw new Error("xplora-watch-actions-card: define `entities` (a list of button.* entity ids) or `entity`.");
    }
    this._config = { ...config, entities };
    if (this._hass) this._render();
  }

  static getStubConfig(hass) {
    const ids = hass && hass.states ? Object.keys(hass.states) : [];
    const found = ids.filter((id) => id.startsWith("button.") && XploraWatchActionsCard.ACTIONS[roleOf(hass, id)]);
    return { entities: found.length ? found : ["button.xplora_watch_update"] };
  }

  getCardSize() {
    return 2;
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._onKeyDown);
    if (this._toastTimer) clearTimeout(this._toastTimer);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    // Cheap re-render guard: only rebuild when the set of present entities or their labels change,
    // and never while the confirm dialog is open or an action is running (a rebuild would drop the
    // button the spinner is attached to).
    if (this._pending || this._busy) return;
    const lu = this._lastUpdateState();
    const sig =
      this._config.entities
        .map((id) => {
          const s = hass.states[id];
          return `${id}:${s ? s.attributes.friendly_name || "" : "∅"}`;
        })
        .join("|") + `|lu:${lu ? `${lu.state}@${lu.last_updated}` : ""}`;
    if (!this._built || sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  _action(entityId) {
    // Unknown/unrecognised roles default to confirm-required so a future destructive button is guarded too.
    return (
      XploraWatchActionsCard.ACTIONS[roleOf(this._hass, entityId)] || {
        label: null,
        icon: "mdi:gesture-tap-button",
        appearance: "outlined",
        variant: "neutral",
        confirm: true,
      }
    );
  }

  _label(entityId, action) {
    if (action.label) return action.label;
    const s = this._hass.states[entityId];
    return (s && s.attributes.friendly_name) || entityId;
  }

  _press(entityId, btn) {
    return this._runAction(entityId, btn);
  }

  // Run a watch action (update / reboot / shutdown) with app-like feedback: a spinner on the
  // pressed button while the call is in flight, then a result popup (HA toast) -- for EVERY action,
  // success and failure. `update` additionally waits for the backend `last_update` sensor (ok /
  // no_response / error) so its toast + the coloured status line + the overview chip reflect this
  // run; reboot/shutdown are fire-and-forget (the server returns only an accept/reject Boolean), so
  // their toast just confirms the command was sent.
  async _runAction(entityId, btn) {
    if (!this._hass) return;
    const action = this._action(entityId);
    const label = this._label(entityId, action);
    const isUpdate = roleOf(this._hass, entityId) === "update";
    const before = this._lastUpdateState();
    const beforeStamp = before ? before.last_updated : null;

    this._busy = true;
    this._setUpdating(btn, true);
    let failed = false;
    let errMsg = "";
    try {
      // notifyOnError=false: we render our own coloured result toast below, so suppress HA's
      // built-in neutral "Failed to perform the action …" snackbar (otherwise both show at once).
      await this._hass.callService("button", "press", { entity_id: entityId }, undefined, false);
    } catch (err) {
      failed = true;
      errMsg = this._errMsg(err);
    }
    // Wait for the backend's last_update sensor to reflect this run (it arrives via websocket).
    let outcome = null;
    if (isUpdate && !failed) {
      await this._awaitSensorChange(beforeStamp, 5000);
      outcome = statusFromSensor(this._lastUpdateState());
    }
    this._setUpdating(btn, false);
    this._busy = false;

    // Result popup for every action, coloured + icon'd by outcome.
    if (failed) {
      this._showToast(`${label} failed${errMsg ? `: ${errMsg}` : ""}`, "error");
    } else if (isUpdate) {
      // `outcome` is a STATUS_META key (success / warning / error) straight from the backend
      // last_update sensor, so the toast colour matches the real result.
      this._showToast(outcome ? STATUS_META[outcome].label : "Update requested", outcome || "success");
    } else {
      // reboot/shutdown resolved without error -> the backend accepted the command (a rejected
      // command, e.g. the watch is off, makes button.press raise and lands in the `failed` branch).
      this._showToast(`${label} accepted by the watch`, "success");
    }
    // Let an embedding card (e.g. the overview) refresh its own last-update indicator at once.
    this.dispatchEvent(new CustomEvent("xplora-update-status", { bubbles: true, composed: true }));
    this._render();
  }

  // The watch's device id, derived from the configured button entities.
  _deviceId() {
    const ents = (this._hass && this._hass.entities) || {};
    for (const id of this._config.entities) {
      if (ents[id] && ents[id].device_id) return ents[id].device_id;
    }
    return null;
  }

  // The watch's `last_update` sensor state object (found via the device), or null.
  _lastUpdateState() {
    const hass = this._hass;
    const ents = hass.entities || {};
    const devId = this._deviceId();
    if (!devId) return null;
    const found = Object.values(ents).find(
      (e) => e.device_id === devId && roleOf(hass, e.entity_id) === "last_update"
    );
    return found ? hass.states[found.entity_id] : null;
  }

  // The watch's fix time (the device_tracker's ISO `last tracking` attribute), or null. Shared rule
  // with the overview (ADR 0007): the status line's time is the fix age, not the poll time.
  _trackerFixIso() {
    const hass = this._hass;
    const ents = hass.entities || {};
    const devId = this._deviceId();
    if (!devId) return null;
    const found = Object.values(ents).find((e) => e.device_id === devId && roleOf(hass, e.entity_id) === "tracker");
    return trackerFixIso(found && hass.states[found.entity_id]);
  }

  async _awaitSensorChange(beforeStamp, timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 250));
      const lu = this._lastUpdateState();
      if (lu && lu.last_updated !== beforeStamp) return true;
    }
    return false;
  }

  _setUpdating(btn, on) {
    if (!btn || !btn.isConnected) return;
    btn.disabled = on;
    const icon = btn.querySelector("ha-icon");
    if (icon) icon.classList.toggle("spin", on);
  }

  // Colored, icon'd result toast. HA's built-in `hass-notification` snackbar is a single neutral
  // style, so success/warning/error would all look identical -- this renders a self-styled toast
  // inside the card's shadow DOM instead, reusing STATUS_META's per-state colour + icon.
  // `kind` ∈ success (green ✓) | warning (amber !) | error (red ✕).
  _showToast(message, kind = "success") {
    const meta = STATUS_META[kind] || STATUS_META.success;
    if (!this._toastEl) {
      this._toastEl = document.createElement("div");
      this.shadowRoot.appendChild(this._toastEl);
    }
    const el = this._toastEl;
    el.className = `xtoast ${kind}`;
    el.innerHTML = `<ha-icon icon="${meta.icon}"></ha-icon><span>${this._esc(message)}</span>`;
    void el.offsetWidth; // reflow so re-showing the same element re-triggers the transition
    el.classList.add("show");
    if (this._toastTimer) clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => el.classList.remove("show"), 4500);
  }

  _errMsg(err) {
    return (err && (err.message || err.error)) || "";
  }

  _render() {
    if (!this._built) {
      const style = document.createElement("style");
      style.textContent = this._css();
      this.shadowRoot.appendChild(style);
      this._card = document.createElement("ha-card");
      this.shadowRoot.appendChild(this._card);
      this._built = true;
    }
    // Theme hint for any native controls (and consistency with the alarm/silent card).
    const dark = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    this.style.setProperty("--xplora-color-scheme", dark ? "dark" : "light");

    const present = this._config.entities.filter((id) => this._hass.states[id]);
    const title = this._config.title || "Watch controls";

    const buttons = present
      .map((id) => {
        const a = this._action(id);
        return `<ha-button class="action" appearance="${a.appearance}" variant="${a.variant}" data-entity="${id}">
                  <ha-icon slot="start" icon="${a.icon}"></ha-icon>${this._esc(this._label(id, a))}
                </ha-button>`;
      })
      .join("");

    const lu = this._lastUpdateState();
    const status = statusFromSensor(lu);
    // Label/colour = poll outcome; time = fix age (ADR 0007), same as the overview chip/banner.
    const statusLine = status
      ? `<div class="last-status ${status}">
           <ha-icon icon="${STATUS_META[status].icon}"></ha-icon>
           <span class="ls-label">${STATUS_META[status].label}</span>
           <span class="ls-when">${this._esc(relTimeIso(this._trackerFixIso()))}</span>
         </div>`
      : "";

    this._card.innerHTML = `
      <div class="header"><span class="title">${this._esc(title)}</span></div>
      <div class="actions">
        ${buttons || `<div class="empty">No watch action buttons found. Enable them on the watch's device page.</div>`}
      </div>
      ${statusLine}`;

    this._card.querySelectorAll("ha-button.action").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.getAttribute("data-entity");
        const a = this._action(id);
        if (a.confirm) this._openConfirm(id, a, btn);
        else this._press(id, btn);
      });
    });

    this._syncConfirm();
  }

  /* ---- confirmation overlay ---- */

  _openConfirm(entityId, action, btn) {
    this._pending = { entityId, action, btn };
    this._syncConfirm();
  }

  _closeConfirm() {
    this._pending = null;
    this._busy = false;
    this._syncConfirm();
  }

  _syncConfirm() {
    if (!this._pending) {
      window.removeEventListener("keydown", this._onKeyDown);
      if (this._modal) {
        this._modal.hidden = true;
        this._modal.innerHTML = "";
      }
      return;
    }
    if (!this._modal) {
      this._modal = document.createElement("div");
      this._modal.className = "modal-host";
      this.shadowRoot.appendChild(this._modal);
    }
    const { entityId, action } = this._pending;
    const label = this._label(entityId, action);
    const danger = action.variant === "danger";

    this._modal.hidden = false;
    this._modal.innerHTML = `
      <div class="backdrop" data-act="cancel"></div>
      <div class="panel" role="alertdialog" aria-modal="true">
        <div class="panel-header"><span class="panel-title">${this._esc(label)}?</span></div>
        <div class="panel-body">This will run <b>${this._esc(label)}</b> on the watch.</div>
        <div class="panel-footer">
          <ha-button appearance="plain" variant="neutral" data-act="cancel">Cancel</ha-button>
          <ha-button appearance="filled" variant="${danger ? "danger" : "brand"}" data-act="confirm">Confirm</ha-button>
        </div>
      </div>`;

    this._modal.querySelectorAll('[data-act="cancel"]').forEach((el) => el.addEventListener("click", () => this._closeConfirm()));
    const confirmBtn = this._modal.querySelector('[data-act="confirm"]');
    if (confirmBtn) {
      confirmBtn.addEventListener("click", () => {
        // Capture before closing (which clears `_pending`), then run with the original button so
        // the spinner attaches to it and a result toast fires -- same feedback as Update.
        const { entityId: id, btn } = this._pending;
        this._closeConfirm();
        this._runAction(id, btn);
      });
    }
    window.addEventListener("keydown", this._onKeyDown);
  }

  _esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _css() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; }
      .header { padding: 16px 16px 4px; }
      .title { font-size: 1.25rem; font-weight: 500; }
      .actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px 16px; }
      .empty { color: var(--secondary-text-color); font-size: 0.95rem; }
      /* Spinner shown on a button's icon while its action (update / restart / shutdown) is in flight. */
      @keyframes xplora-spin { to { transform: rotate(360deg); } }
      .action ha-icon.spin { animation: xplora-spin 0.9s linear infinite; }
      /* Persisted last-update outcome (green = new data, amber = watch unreachable, red = failed). */
      .last-status { display: flex; align-items: center; gap: 6px; padding: 0 16px 14px; font-size: 0.92rem; }
      .last-status ha-icon { --mdc-icon-size: 18px; }
      .last-status .ls-when { margin-left: auto; color: var(--secondary-text-color); font-size: 0.85rem; }
      .last-status.success { color: var(--success-color, #43a047); }
      .last-status.warning { color: var(--warning-color, #ffa600); }
      .last-status.error { color: var(--error-color, #db4437); }

      .modal-host { position: fixed; inset: 0; z-index: 9; display: flex; align-items: center; justify-content: center; padding: 16px; box-sizing: border-box; }
      .modal-host[hidden] { display: none; }
      .backdrop { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.46); }
      .panel {
        position: relative; z-index: 1; width: 100%; max-width: 400px;
        display: flex; flex-direction: column;
        background: var(--card-background-color, var(--ha-card-background, #fff));
        color: var(--primary-text-color);
        border-radius: var(--ha-card-border-radius, 16px);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
        color-scheme: var(--xplora-color-scheme, light dark);
      }
      .panel-header { padding: 16px 20px 4px; }
      .panel-title { font-size: 1.2rem; font-weight: 500; }
      .panel-body { padding: 4px 20px 8px; color: var(--secondary-text-color); }
      .panel-footer { display: flex; justify-content: flex-end; gap: 10px; padding: 8px 16px 16px; }

      /* Result toast: coloured accent + icon per outcome (success / warning / error). */
      .xtoast {
        position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 16px); z-index: 10;
        display: flex; align-items: center; gap: 10px;
        max-width: min(92vw, 420px); padding: 12px 16px; box-sizing: border-box;
        background: var(--card-background-color, var(--ha-card-background, #fff));
        color: var(--primary-text-color);
        border-radius: 10px; border-left: 4px solid var(--xtoast-accent, var(--primary-color));
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.3);
        opacity: 0; pointer-events: none; transition: opacity 0.2s ease, transform 0.2s ease;
        color-scheme: var(--xplora-color-scheme, light dark);
        font-size: 0.95rem;
      }
      .xtoast.show { opacity: 1; transform: translate(-50%, 0); }
      .xtoast ha-icon { --mdc-icon-size: 22px; flex: 0 0 auto; }
      .xtoast.success { --xtoast-accent: var(--success-color, #43a047); }
      .xtoast.success ha-icon { color: var(--success-color, #43a047); }
      .xtoast.warning { --xtoast-accent: var(--warning-color, #ffa600); }
      .xtoast.warning ha-icon { color: var(--warning-color, #ffa600); }
      .xtoast.error { --xtoast-accent: var(--error-color, #db4437); }
      .xtoast.error ha-icon { color: var(--error-color, #db4437); }
    `;
  }
}

if (!customElements.get("xplora-watch-actions-card")) {
  customElements.define("xplora-watch-actions-card", XploraWatchActionsCard);
}

window.customCards.push({
  type: "xplora-watch-actions-card",
  name: "Xplora Watch Controls",
  description: "Action buttons for a watch (update / restart / shutdown) with confirmation by default.",
  preview: true,
});

/**
 * Xplora® Watch overview card.
 *
 * A single, app-like summary of one watch: avatar, online + battery, last location (address,
 * distance from home, last fix time) and a grid of extras (steps, coins, unread messages, alarm
 * and silent-time counts, safe-zone status). Each value is tappable to open its entity's more-info.
 *
 * Point it at ANY one of the watch's entities (or its device); it discovers the rest of that
 * watch's entities from the registry — no need to list them. Values come straight from the
 * integration's existing entities, so a tile only appears when its entity is enabled and has data
 * (battery / charging / online / location are enabled by default; enable the others on the device
 * page to light up more tiles).
 *
 * Config:
 *   type: custom:xplora-watch-overview-card
 *   entity: device_tracker.xplora_kid_one_watch_tracker   # any watch entity (or use `device:`)
 *   title: Kid One                                         # optional name override
 */
class XploraWatchOverviewCard extends HTMLElement {
  // "<domain>.<xplora_role>" -> this card's logical role. Keyed on the integration-emitted role
  // attribute (ADR 0005), not the entity_id, so account-tokened / renamed ids still resolve. Scoped
  // by domain because `safezone` is emitted by BOTH a binary_sensor (the in/out tile) and
  // device_tracker per-zone entities -- the domain tells them apart.
  static ROLE_BY_DOMAIN_ROLE = {
    "sensor.battery": "battery",
    "sensor.step_day": "steps",
    "sensor.xcoin": "xcoin",
    "sensor.message": "messages",
    "sensor.alarms": "alarms",
    "sensor.silents": "silents",
    "sensor.last_update": "lastupdate",
    "sensor.location_history": "history",
    "binary_sensor.charging": "charging",
    "binary_sensor.state": "online",
    "binary_sensor.safezone": "safezone",
    // Watch-reported name of the safezone the watch is inside (unknown while outside every
    // safezone). The safe-zone tile merges it with the in/out binary sensor above.
    "sensor.current_safezone": "safezone_label",
    "device_tracker.tracker": "tracker",
  };

  static TILES = [
    { role: "steps", icon: "mdi:run", label: "Steps" },
    { role: "xcoin", icon: "mdi:hand-coin", label: "XCoins" },
    { role: "messages", icon: "mdi:message-text", label: "Unread" },
    { role: "alarms", icon: "mdi:alarm", label: "Alarms" },
    { role: "silents", icon: "mdi:school", label: "Silent times" },
    { role: "safezone", icon: "mdi:shield-home", label: "Safe zone" },
  ];

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._sig = "";
    this._deviceId = null;
    this._autoRefreshDone = false; // guard so "refresh on render" fires once per card instance
    this._refreshing = false; // an on-render auto-refresh is in flight (drives the status-row spinner)
    // Generic popup host (ADR 0008): mounts the alarm/silent, chat, controls, history and map cards
    // full-screen or compact. The map's banner / reload / fix-age logic lives in the map card, so
    // this host is deliberately generic -- it just mounts and keeps the embedded card hass-live.
    this._popup = new CardPopupHost(this.shadowRoot, () => this._hass);
    // An embedded card in the popup (controls, or the map card on reload) fires this when an update
    // finishes -- refresh the header's last-update indicator even if the watch data didn't change.
    this._onUpdateStatus = () => this._render();
  }

  connectedCallback() {
    this.addEventListener("xplora-update-status", this._onUpdateStatus);
  }

  disconnectedCallback() {
    this._popup.close();
    this.removeEventListener("xplora-update-status", this._onUpdateStatus);
  }

  // Config (all optional unless noted):
  //   entity / device          - any watch entity, or the device id (one is required)
  //   title                    - overrides the displayed watch name
  //   show_history             - show the "Location history" row (default true; needs the opt-in
  //                              location-history sensor enabled)
  //   history_max_points       - cap on plotted points per fetch (default 500)
  //   history_zoom             - max auto-fit zoom for the day's map track (default 15)
  setConfig(config) {
    if (!config || (!config.entity && !config.device)) {
      throw new Error("xplora-watch-overview-card: define `entity` (any watch entity) or `device`.");
    }
    this._config = config;
    if (this._hass) this._render();
  }

  static getStubConfig(hass) {
    const ids = hass && hass.states ? Object.keys(hass.states) : [];
    const tracker = ids.find((id) => id.startsWith("device_tracker.") && roleOf(hass, id) === "tracker");
    const any = ids.find((id) => id.includes("xplora") && id.includes("_watch_"));
    return { entity: tracker || any || "device_tracker.xplora_watch_tracker" };
  }

  getCardSize() {
    return 4;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    // Keep the embedded popup card live while its popup is open (regardless of the overview's own
    // re-render guard below). A map card in the popup re-derives its own fix-age banner from this.
    this._popup.setHass(hass);
    const map = this._watchEntities();
    const sig =
      Object.values(map)
        .map((id) => {
          const s = hass.states[id];
          // `last_updated` (not `last_changed`): battery/location/status etc. update as ATTRIBUTES
          // without the state value changing, and `last_changed` would miss those.
          return s ? `${id}=${s.state}@${s.last_updated}` : `${id}=∅`;
        })
        .join("|") +
      "|" +
      this._deviceName();
    if (!this._built || sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
    // Once the watch's entities are resolved, optionally pull fresh data on first show (deduped).
    this._maybeRefreshOnRender(map);
  }

  _locale() {
    return (this._hass && this._hass.locale && this._hass.locale.language) || navigator.language || "en";
  }

  // Discover the watch's entities (keyed by this card's logical role) from the registry, given any
  // one of them. Each entity is placed by its (domain, integration-emitted `xplora_role`) rather
  // than by parsing the entity_id, so account-tokened / user-renamed ids still resolve (ADR 0005).
  _watchEntities() {
    const hass = this._hass;
    const conf = this._config;
    const ents = hass.entities || {};
    let deviceId = conf.device || null;
    if (!deviceId && conf.entity && ents[conf.entity]) deviceId = ents[conf.entity].device_id;
    this._deviceId = deviceId;

    const map = {};
    const place = (id) => {
      const role = XploraWatchOverviewCard.ROLE_BY_DOMAIN_ROLE[`${id.split(".")[0]}.${roleOf(hass, id)}`];
      if (role) map[role] = id;
    };
    if (deviceId) {
      Object.values(ents).forEach((e) => {
        if (e.device_id === deviceId) place(e.entity_id);
      });
    }
    // Fallback when the entity registry isn't exposed: at least map the configured entity itself.
    if (Object.keys(map).length === 0 && conf.entity) place(conf.entity);
    return map;
  }

  // The watch's button.* action entities (update / restart / shutdown) for the controls popup.
  _buttonEntities() {
    const ents = this._hass.entities || {};
    if (!this._deviceId) return [];
    return Object.values(ents)
      .filter((e) => e.device_id === this._deviceId && e.entity_id.startsWith("button."))
      .map((e) => e.entity_id);
  }

  _deviceName() {
    if (this._config && this._config.title) return this._config.title;
    const dev = this._deviceId && this._hass.devices && this._hass.devices[this._deviceId];
    if (dev) return dev.name_by_user || dev.name || "Watch";
    return "Watch overview";
  }

  _usable(stateObj) {
    return stateObj && stateObj.state !== "unavailable" && stateObj.state !== "unknown" && stateObj.state !== "";
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  // Fire-and-forget on-demand refresh of the alarm/silent/safezone data for the watch behind
  // `entityId`. The list sensors surface `wuid` + `entry_id` (the same attributes the management
  // card targets the CRUD services with), which is exactly what `xplora_watch.refresh_functions`
  // needs. No-op if those attributes are missing (e.g. the entity isn't a list sensor).
  _refreshFunctions(entityId) {
    const a = ((this._hass && this._hass.states[entityId]) || {}).attributes || {};
    if (!a.wuid || !a.entry_id) return;
    // Record the dedup window so a co-rendered card's "refresh on render" doesn't immediately
    // re-fire the same call (this explicit tap always fires). The service targets the watch by the
    // list entity (resolved to its device server-side).
    markRefreshed(`${SERVICE.REFRESH_FUNCTIONS}|${a.entry_id}|${a.wuid}`);
    Promise.resolve(
      this._hass.callService(DOMAIN, SERVICE.REFRESH_FUNCTIONS, { entity_id: [entityId] })
    ).catch((err) => console.error("xplora-watch-overview-card: refresh_functions failed", err));
  }

  // When the user enabled "refresh on render", refresh this watch's data once on first show:
  // functions (alarms/silent times) via a list sensor, and location via the watch's `update`
  // button. Both are deduped so several cards in one view only refresh each data set once, and the
  // shared `trackInflight` handles drive the status-row loading indicator until both settle.
  _maybeRefreshOnRender(map) {
    if (this._autoRefreshDone) return;
    const primary = this._config.entity && this._hass.states[this._config.entity];
    if (!this._usable(primary)) return; // bound entity not alive yet -- retry on the next hass push
    this._autoRefreshDone = true;
    if (!refreshOnRenderEnabled(primary)) return;
    const handles = [];
    const listEntity = map.alarms || map.silents;
    if (listEntity) {
      const a = (this._hass.states[listEntity] || {}).attributes || {};
      if (a.wuid && a.entry_id) {
        handles.push(
          trackInflight(`${SERVICE.REFRESH_FUNCTIONS}|${a.entry_id}|${a.wuid}`, () =>
            this._hass.callService(DOMAIN, SERVICE.REFRESH_FUNCTIONS, { entity_id: [listEntity] }, undefined, false)
          )
        );
      }
    }
    const updateBtn = this._buttonEntities().find((id) => roleOf(this._hass, id) === "update");
    if (updateBtn) {
      handles.push(
        trackInflight(`button.press|${updateBtn}`, () => this._hass.callService("button", "press", { entity_id: updateBtn }))
      );
    }
    this._trackRefreshLoading(handles);
  }

  // Show the "Updating…" indicator in the last-update status row while any on-render refresh is
  // actually live, and clear it once ALL of them settle. A caller inside the dedup window gets
  // `inflight: false` (nothing live to await) so it shows no spinner. The shared promises never
  // reject, so a failed run clears the spinner silently -- the coordinator's fail-loud outcome
  // surfaces in the status row on the next state push, not as a toast.
  _trackRefreshLoading(handles) {
    const live = handles.filter((h) => h && h.inflight);
    if (!live.length) return;
    this._refreshing = true;
    this._render();
    Promise.all(live.map((h) => h.promise)).then(() => {
      this._refreshing = false;
      this._render();
    });
  }

  _dist(m) {
    const n = Number(m);
    if (m == null || isNaN(n)) return "";
    return n >= 1000 ? `${(n / 1000).toFixed(1)} km` : `${Math.round(n)} m`;
  }

  _fmtTime(v) {
    if (v == null || v === "") return "";
    let d;
    if (typeof v === "number" || /^\d+$/.test(String(v))) {
      let n = Number(v);
      if (n < 1e12) n *= 1000; // seconds -> ms
      d = new Date(n);
    } else {
      d = new Date(v);
    }
    return isNaN(d.getTime()) ? String(v) : d.toLocaleString(this._locale());
  }

  _render() {
    if (!this._built) {
      const style = document.createElement("style");
      style.textContent = this._css();
      this.shadowRoot.appendChild(style);
      this._card = document.createElement("ha-card");
      this.shadowRoot.appendChild(this._card);
      this._built = true;
    }

    const hass = this._hass;
    const map = this._watchEntities();
    const st = (role) => (map[role] ? hass.states[map[role]] : undefined);

    const tracker = st("tracker");
    const battEnt = st("battery");
    const onlineEnt = st("online");
    const chargeEnt = st("charging");

    const battery = this._usable(battEnt)
      ? Number(battEnt.state)
      : tracker && tracker.attributes.battery_level != null
        ? Number(tracker.attributes.battery_level)
        : null;
    const online = this._usable(onlineEnt) ? onlineEnt.state === "on" : null;
    const charging = this._usable(chargeEnt) && chargeEnt.state === "on";
    const picture = tracker && tracker.attributes.entity_picture;

    const addr = tracker && tracker.attributes["address"];
    const dist = tracker && this._dist(tracker.attributes["Home Distance (m)"]);
    // Fix time only (ADR 0007): no `|| tracker.last_changed` fallback -- last_changed is the
    // entity's zone-transition time, neither the fix nor the poll time, so showing it here would
    // reintroduce the "which time is this?" ambiguity. Unknown fix -> no timestamp on the row.
    const lastTrack = tracker && this._fmtTime(tracker.attributes["last tracking"]);

    // Status line: online dot + battery (+ charging bolt).
    const statusBits = [];
    if (online !== null) statusBits.push(`<span class="dot ${online ? "on" : "off"}"></span>${online ? "Online" : "Offline"}`);
    if (battery !== null) {
      const lvl = battery <= 15 ? "low" : battery <= 35 ? "mid" : "ok";
      statusBits.push(`<span class="batt ${lvl}"><ha-icon icon="mdi:battery"></ha-icon>${Math.round(battery)}%</span>`);
    }
    if (charging) statusBits.push(`<span class="charging"><ha-icon icon="mdi:flash"></ha-icon>Charging</span>`);

    // Last-update outcome from the backend `last_update` sensor (covers manual + background polls).
    // While an on-render auto-refresh is in flight, show an "Updating…" spinner in its place; the
    // resolved chip returns once the refresh settles (the data beneath stays visible throughout).
    const { status: luStatus, fixIso } = fixAgeStatus(hass, map);
    if (this._refreshing) {
      statusBits.push(
        `<span class="upd refreshing" title="Updating…"><ha-icon class="spin" icon="mdi:refresh"></ha-icon>Updating…</span>`
      );
    } else if (luStatus) {
      const m = STATUS_META[luStatus];
      // The icon/colour carry the poll outcome; the time is how old the shown FIX is (ADR 0007),
      // NOT when we last polled. Empty (icon only) when the fix time is unknown.
      statusBits.push(
        `<span class="upd ${luStatus}" title="${this._esc(m.label)}"><ha-icon icon="${m.icon}"></ha-icon>${this._esc(relTimeIso(fixIso))}</span>`
      );
    }

    const avatar = picture
      ? `<div class="avatar" style="background-image:url('${this._esc(picture)}')"></div>`
      : `<div class="avatar fallback"><ha-icon icon="mdi:watch"></ha-icon></div>`;

    const location = tracker
      ? `<button class="row location" data-map="${this._esc(map.tracker)}">
           <ha-icon class="row-icon" icon="mdi:map-marker"></ha-icon>
           <div class="row-text">
             <div class="row-main">${this._esc(addr || "Location unavailable")}</div>
             <div class="row-sub">${[lastTrack, dist ? `${dist} from home` : ""].filter(Boolean).map((s) => this._esc(s)).join(" · ")}</div>
           </div>
           <ha-icon class="row-chevron" icon="mdi:chevron-right"></ha-icon>
         </button>`
      : "";

    // Location-history row: shown when the (opt-in) history sensor is enabled and `show_history`
    // isn't disabled. Tapping it opens a map-track popup of the watch's recent path.
    const historyEnt = st("history");
    const showHistory = !this._config || this._config.show_history !== false;
    const historyRow =
      historyEnt && showHistory
        ? (() => {
            const total = Number((historyEnt.attributes && historyEnt.attributes.history_total_points) || 0);
            const sub = total > 0 ? `${total} point${total === 1 ? "" : "s"} kept` : "No history yet";
            return `<button class="row" data-history="${this._esc(map.history)}">
                 <ha-icon class="row-icon" icon="mdi:map-marker-path"></ha-icon>
                 <div class="row-text">
                   <div class="row-main">Location history</div>
                   <div class="row-sub">${this._esc(sub)}</div>
                 </div>
                 <ha-icon class="row-chevron" icon="mdi:chevron-right"></ha-icon>
               </button>`;
          })()
        : "";

    const tiles = XploraWatchOverviewCard.TILES.map((t) => {
      const s = st(t.role);
      if (!this._usable(s)) return "";
      let value = s.state;
      let unit = s.attributes.unit_of_measurement || "";
      if (t.role === "safezone") {
        // The binary sensor is a SAFETY alert: "on" means the watch is OUTSIDE every safezone.
        // While inside, the current-safezone sensor (when enabled and reporting) names the
        // watch-reported zone; unknown just means "inside, but no zone name known".
        const label = st("safezone_label");
        value = s.state === "on" ? "Outside" : this._usable(label) ? label.state : "Inside";
        unit = "";
      }
      // Alarms/silents open the full management card in a popup; messages open the chat card;
      // everything else opens more-info.
      const isChat = t.role === "messages";
      const opensCard = t.role === "alarms" || t.role === "silents";
      const attr = isChat ? "data-chat" : opensCard ? "data-card" : "data-more";
      return `<button class="tile" ${attr}="${this._esc(map[t.role])}">
                <ha-icon icon="${t.icon}"></ha-icon>
                <div class="tile-value">${this._esc(value)}${unit ? `<span class="unit"> ${this._esc(unit)}</span>` : ""}</div>
                <div class="tile-label">${t.label}</div>
              </button>`;
    }).join("");

    const controls = this._buttonEntities();
    const controlsBtn = controls.length
      ? `<ha-icon-button class="controls-btn" data-controls label="Controls"><ha-icon icon="mdi:cog"></ha-icon></ha-icon-button>`
      : "";

    this._card.innerHTML = `
      <div class="ov-header">
        ${avatar}
        <div class="head-text">
          <div class="name">${this._esc(this._deviceName())}</div>
          <div class="status">${statusBits.join('<span class="sep">·</span>') || "No status entities enabled"}</div>
        </div>
        ${controlsBtn}
      </div>
      ${location}
      ${historyRow}
      ${tiles ? `<div class="grid">${tiles}</div>` : ""}`;

    this._card.querySelectorAll("[data-more]").forEach((el) => {
      el.addEventListener("click", () => this._moreInfo(el.getAttribute("data-more")));
    });
    this._card.querySelectorAll("[data-card]").forEach((el) => {
      const entity = el.getAttribute("data-card");
      el.addEventListener("click", () => {
        // Tapping an alarms/silents count opens the management list. Those lists come from the
        // "functions" fetch, which defaults to OFF (refreshed on demand) -- so kick off a refresh
        // here so the popup reflects the watch's current state instead of the last cached values.
        this._refreshFunctions(entity);
        this._popup.open(() => {
          const c = document.createElement("xplora-watch-card");
          c.setConfig({ entity });
          return c;
        });
      });
    });
    this._card.querySelectorAll("[data-chat]").forEach((el) => {
      const entity = el.getAttribute("data-chat");
      el.addEventListener("click", () => {
        // The chat card reads `entry_id`/`wuid` from the message sensor and auto-fetches the thread
        // on first open if nothing is cached, so no pre-refresh is needed here.
        this._popup.open(() => {
          const c = document.createElement("xplora-watch-chat-card");
          c.setConfig({ entity });
          return c;
        });
      });
    });
    this._card.querySelectorAll("[data-history]").forEach((el) => {
      const entity = el.getAttribute("data-history");
      el.addEventListener("click", () => {
        // History is fetched on-demand only -- it is kept off the regular see/poll cycle. Force a
        // refresh so the sensor's "points kept" count reflects today's track, then open the map-track
        // popup (fill mode); the popup itself pulls each day fresh over the websocket regardless.
        this._refreshFunctions(entity);
        this._popup.open(() => this._buildHistoryView(entity), { fill: true });
      });
    });
    this._card.querySelectorAll("[data-map]").forEach((el) => {
      const entity = el.getAttribute("data-map");
      el.addEventListener("click", () => {
        // The location row opens the SAME standalone map card full-screen (ADR 0008): one component
        // renders the inline card and the popup, so the fix-age banner (ADR 0007) + reload can't
        // drift between them. The map card resolves its own tracker / last_update / Update button by
        // role, so bind it by device when known (else by the tapped tracker id). Fill mode stretches
        // the map and suppresses the card's own header + expand button (no recursive re-open).
        this._popup.open(
          () => {
            const c = document.createElement("xplora-watch-map-card");
            c.fill = true;
            c.setConfig(this._deviceId ? { device: this._deviceId } : { entity });
            return c;
          },
          { fill: true },
        );
      });
    });
    const ctrlBtn = this._card.querySelector("[data-controls]");
    if (ctrlBtn) {
      ctrlBtn.addEventListener("click", () =>
        this._popup.open(() => {
          const c = document.createElement("xplora-watch-actions-card");
          c.setConfig({ entities: controls });
          return c;
        }),
      );
    }
  }

  // ---- Location history (map track) ----------------------------------------------------------

  _historyMaxPoints() {
    const n = Number(this._config && this._config.history_max_points);
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : HISTORY_MAX_POINTS_DEFAULT;
  }

  _historyZoom() {
    const n = Number(this._config && this._config.history_zoom);
    return Number.isFinite(n) && n > 0 ? n : HISTORY_MAP_ZOOM_DEFAULT;
  }

  // Normalize a point timestamp to epoch ms (the backend already does this, but be defensive so a
  // raw seconds value still maps correctly when fed to `new Date()`).
  _toMs(v) {
    let n = Number(v);
    if (!isFinite(n)) return Date.now();
    if (n < 1e12) n *= 1000;
    return n;
  }

  // Fetch ONE day's points via the websocket command. The backend serves today fresh and a past
  // day from cache (network only on first view). Falls back to the bounded sensor attribute
  // (~today) when the websocket is unavailable (old HA / jsdom) or fails.
  async _fetchDay(historyEntity, dayKey) {
    const a = ((this._hass && this._hass.states[historyEntity]) || {}).attributes || {};
    const attrPoints = Array.isArray(a.history_points) ? a.history_points : [];
    if (!a.entry_id || !a.wuid || typeof this._hass.callWS !== "function") return attrPoints;
    try {
      const res = await this._hass.callWS({ type: WS_LOCATION_HISTORY, entry_id: a.entry_id, wuid: a.wuid, day: dayKey });
      return res && Array.isArray(res.points) ? res.points : [];
    } catch (e) {
      console.error("xplora-watch-overview-card: location_history WS failed", e);
      return attrPoints;
    }
  }

  // Calendar-day key (YYYY-MM-DD) for an epoch-ms timestamp, computed in the WATCH timezone
  // (`this._historyTz`, set when the popup opens) so the date picker's today/min bounds match what
  // the app shows regardless of the browser's timezone. Falls back to the browser tz if unset.
  _dayKey(ms) {
    try {
      // en-CA yields ISO-like YYYY-MM-DD.
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: this._historyTz || undefined,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date(ms));
    } catch (e) {
      return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
    }
  }

  // Friendly label for a "YYYY-MM-DD" day key. Formatted from the bare date (noon UTC, displayed in
  // UTC) so the printed calendar date always equals the key -- no timezone shift in the label.
  _dayLabel(dayKey) {
    const [y, m, d] = String(dayKey).split("-").map(Number);
    if (!y || !m || !d) return dayKey;
    const dt = new Date(Date.UTC(y, m - 1, d, 12));
    try {
      return new Intl.DateTimeFormat(this._locale(), {
        timeZone: "UTC",
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(dt);
    } catch (e) {
      return dayKey;
    }
  }

  // Compact numeric label for a "YYYY-MM-DD" day key, shown on the date bar (e.g. de: "28.06.2026",
  // en: "06/28/2026"). Locale-aware via Intl; formatted from noon UTC so the printed date equals the
  // key with no timezone shift.
  _dayLabelShort(dayKey) {
    const [y, m, d] = String(dayKey).split("-").map(Number);
    if (!y || !m || !d) return dayKey;
    const dt = new Date(Date.UTC(y, m - 1, d, 12));
    try {
      return new Intl.DateTimeFormat(this._locale(), { timeZone: "UTC", day: "2-digit", month: "2-digit", year: "numeric" }).format(dt);
    } catch (e) {
      return dayKey;
    }
  }

  // Build a single `ha-map` path (polyline) from the points.
  _historyPath(points) {
    return {
      name: this._deviceName(),
      color: "var(--primary-color, #2196f3)",
      gradualOpacity: 0.8,
      fullDatetime: true,
      points: points.map((p) => ({ point: [Number(p.lat), Number(p.lng)], timestamp: new Date(this._toMs(p.tm)) })),
    };
  }

  _historyListItem(p) {
    const ms = this._toMs(p.tm);
    const opts = { hour: "2-digit", minute: "2-digit" };
    if (this._historyTz) opts.timeZone = this._historyTz;
    let when;
    try {
      when = new Date(ms).toLocaleTimeString(this._locale(), opts);
    } catch (e) {
      when = new Date(ms).toLocaleTimeString(this._locale(), { hour: "2-digit", minute: "2-digit" });
    }
    const label = p.addr || p.poi || `${Number(p.lat).toFixed(5)}, ${Number(p.lng).toFixed(5)}`;
    return `<li class="hist-item"><span class="hist-time">${this._esc(when)}</span><span class="hist-addr">${this._esc(label)}</span></li>`;
  }

  // Ensure HA's lazy `ha-map` element is registered. Returns false (-> list fallback) when it can't
  // be loaded -- old HA, a chunk failure, or a non-HA host like jsdom (no `loadCardHelpers`).
  async _ensureHaMap() {
    if (customElements.get("ha-map")) return true;
    if (typeof window.loadCardHelpers !== "function") return false;
    try {
      const helpers = await window.loadCardHelpers();
      // Creating a `map` card pulls in the map JS chunk, which registers `ha-map` as a side effect.
      try {
        helpers.createCardElement({ type: "map", entities: [] });
      } catch (e) {
        /* only the chunk load matters here */
      }
      await Promise.race([
        customElements.whenDefined("ha-map"),
        new Promise((_, reject) => setTimeout(() => reject(new Error("ha-map timeout")), HA_MAP_WAIT_MS)),
      ]);
    } catch (e) {
      /* fall through to the list fallback */
    }
    return !!customElements.get("ha-map");
  }

  // Fetch and render ONE day into the popup body: a map track, or a chronological list when the map
  // element is unavailable. The container is tagged `data-history-mode` so tests (and styling) can
  // tell which branch ran.
  async _showDay(historyEntity, body, dayKey) {
    // Generation token: a newer day selection (or a closed popup) makes an in-flight render stale
    // so it doesn't overwrite the latest result when its awaits resolve out of order.
    const gen = (body.__histGen = (body.__histGen || 0) + 1);
    const stale = () => body.__histGen !== gen || !this._popup.isOpen();
    body.innerHTML = `<div class="hist-msg">Loading…</div>`;
    const points = await this._fetchDay(historyEntity, dayKey);
    if (stale()) return;
    if (!points.length) {
      body.setAttribute("data-history-mode", "empty");
      body.innerHTML = `<div class="hist-msg">No location history for this day.</div>`;
      return;
    }
    const capped = points.slice(-this._historyMaxPoints());
    const haveMap = await this._ensureHaMap();
    if (stale()) return;
    if (haveMap) {
      body.setAttribute("data-history-mode", "map");
      body.innerHTML = "";
      const el = document.createElement("ha-map");
      el.hass = this._hass;
      el.autoFit = true;
      // Cap the auto-fit zoom so a day spent in one place doesn't zoom too far.
      el.zoom = this._historyZoom();
      // `themeMode` is the current property name; `darkMode` is the older one. Set both so the map
      // themes correctly across HA versions (an unknown property is harmless on a Lit element).
      el.themeMode = "auto";
      el.darkMode = false;
      el.paths = [this._historyPath(capped)];
      body.appendChild(el);
    } else {
      body.setAttribute("data-history-mode", "list");
      const rows = capped
        .slice()
        .reverse()
        .map((p) => this._historyListItem(p))
        .join("");
      body.innerHTML = `<ul class="hist-list">${rows}</ul>`;
    }
  }

  // Render a month grid into `calEl` (pure markup; the caller wires the listeners after each render).
  // Days that have data -- cached days UNION the always-available recent days -- get the `has-data`
  // class and are clickable; every other day is disabled and dimmed. This scales to months of
  // archived data far better than a flat dropdown. Month nav is bounded to [minKey month, today].
  _renderCalendarInto(calEl, { year, month, selectable, selected, today, minKey }) {
    const pad = (n) => String(n).padStart(2, "0");
    const monthStart = new Date(Date.UTC(year, month, 1, 12));
    const startDow = monthStart.getUTCDay();
    const daysInMonth = new Date(Date.UTC(year, month + 1, 0, 12)).getUTCDate();
    const title = new Intl.DateTimeFormat(this._locale(), { timeZone: "UTC", month: "long", year: "numeric" }).format(monthStart);
    // Localized one-letter weekday headers (2021-08-01 was a Sunday -> index 0 = Sun).
    const weekdays = Array.from({ length: 7 }, (_, i) =>
      new Intl.DateTimeFormat(this._locale(), { timeZone: "UTC", weekday: "narrow" }).format(new Date(Date.UTC(2021, 7, 1 + i, 12))),
    );
    const [minY, minM] = String(minKey).split("-").map(Number);
    const [maxY, maxM] = String(today).split("-").map(Number);
    const atMin = year < minY || (year === minY && month <= minM - 1);
    const atMax = year > maxY || (year === maxY && month >= maxM - 1);
    let cells = "";
    for (let i = 0; i < startDow; i++) cells += `<span class="hist-cell empty"></span>`;
    for (let d = 1; d <= daysInMonth; d++) {
      const key = `${year}-${pad(month + 1)}-${pad(d)}`;
      const has = selectable.has(key);
      const cls = ["hist-cell"];
      if (has) cls.push("has-data");
      if (key === selected) cls.push("sel");
      if (key === today) cls.push("today");
      cells += `<button class="${cls.join(" ")}" data-day="${key}" title="${this._esc(this._dayLabel(key))}"${has ? "" : " disabled"}>${d}</button>`;
    }
    calEl.innerHTML =
      `<div class="hist-cal-head">` +
      `<button class="hist-cal-nav" data-nav="-1"${atMin ? " disabled" : ""} aria-label="Previous month"><ha-icon icon="mdi:chevron-left"></ha-icon></button>` +
      `<span class="hist-cal-title">${this._esc(title)}</span>` +
      `<button class="hist-cal-nav" data-nav="1"${atMax ? " disabled" : ""} aria-label="Next month"><ha-icon icon="mdi:chevron-right"></ha-icon></button>` +
      `</div>` +
      `<div class="hist-cal-week">${weekdays.map((w) => `<span>${this._esc(w)}</span>`).join("")}</div>` +
      `<div class="hist-cal-grid">${cells}</div>`;
  }

  // Build the location-history popup: a compact date bar ("< DD.MM.YYYY >" with prev/next/today
  // buttons) above the day's map track. Tapping the date opens a month-calendar popover that
  // highlights the days with data. The watch's API only serves the last few days, so the selectable
  // set is the cached days (archived via the daily `fetch_history` service) UNION the always-available
  // recent ones; the arrows step through that set and the calendar disables every other day. Defaults
  // to today (always fetched fresh). Returned to the popup host (fill mode).
  async _buildHistoryView(historyEntity) {
    const wrap = document.createElement("div");
    wrap.className = "hist-wrap";
    // Day keys/labels are computed in the watch timezone (exposed on the sensor), not the browser's.
    const attrs = ((this._hass && this._hass.states[historyEntity]) || {}).attributes || {};
    this._historyTz = attrs.timezone || null;
    const today = this._dayKey(Date.now());
    const recent = Array.from({ length: HISTORY_RECENT_DAYS }, (_, i) => this._dayKey(Date.now() - i * 86400000));
    const cached = Array.isArray(attrs.history_days) ? attrs.history_days : [];
    const selectable = new Set([...recent, ...cached]);
    const days = [...selectable].sort(); // ascending list of selectable day keys (arrows step through it)
    const minKey = days[0] || today;
    // Localized "Today" label via HA's own frontend translations (covers every HA language, not just
    // this integration's), falling back to English when the key/localize is unavailable.
    const todayLabel = (this._hass && this._hass.localize && this._hass.localize("ui.components.calendar.today")) || "Today";
    wrap.innerHTML =
      `<div class="hist-bar">` +
      `<button class="hist-nav" data-step="-1" aria-label="Previous day"><ha-icon icon="mdi:chevron-left"></ha-icon></button>` +
      `<button class="hist-date" aria-haspopup="true"><span class="hist-date-label"></span></button>` +
      `<button class="hist-nav" data-step="1" aria-label="Next day"><ha-icon icon="mdi:chevron-right"></ha-icon></button>` +
      `<button class="hist-today">${this._esc(todayLabel)}</button>` +
      `<div class="hist-pop" hidden><div class="hist-cal"></div></div>` +
      `</div>` +
      `<div class="hist-body"><div class="hist-msg">Loading…</div></div>`;
    const dateBtn = wrap.querySelector(".hist-date");
    const dateLabel = wrap.querySelector(".hist-date-label");
    const pop = wrap.querySelector(".hist-pop");
    const calEl = wrap.querySelector(".hist-cal");
    const body = wrap.querySelector(".hist-body");
    let selected = today;
    let [viewYear, viewMonth] = [Number(today.split("-")[0]), Number(today.split("-")[1]) - 1];

    const closePop = () => {
      pop.hidden = true;
    };
    // Reflect the current selection in the bar: update the label and disable arrows/today at bounds.
    const syncBar = () => {
      dateLabel.textContent = this._dayLabelShort(selected);
      const i = days.indexOf(selected);
      wrap.querySelector('[data-step="-1"]').disabled = i <= 0;
      wrap.querySelector('[data-step="1"]').disabled = i < 0 || i >= days.length - 1;
      wrap.querySelector(".hist-today").disabled = selected === today;
    };
    // Select a day: sync the bar, keep the calendar's month view in step, and (re)load the map track.
    const select = (day) => {
      selected = day;
      viewYear = Number(day.split("-")[0]);
      viewMonth = Number(day.split("-")[1]) - 1;
      syncBar();
      if (!pop.hidden) renderCal();
      this._showDay(historyEntity, body, selected);
    };

    const renderCal = () => {
      this._renderCalendarInto(calEl, { year: viewYear, month: viewMonth, selectable, selected, today, minKey });
      calEl.querySelectorAll("[data-nav]").forEach((b) =>
        b.addEventListener("click", () => {
          viewMonth += Number(b.getAttribute("data-nav"));
          if (viewMonth < 0) {
            viewMonth = 11;
            viewYear--;
          } else if (viewMonth > 11) {
            viewMonth = 0;
            viewYear++;
          }
          renderCal();
        }),
      );
      calEl.querySelectorAll(".hist-cell.has-data").forEach((c) =>
        c.addEventListener("click", () => {
          const day = c.getAttribute("data-day");
          closePop();
          select(day);
        }),
      );
    };

    // Date label toggles the calendar popover; the arrows/today button step through `days`.
    dateBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (pop.hidden) {
        renderCal();
        pop.hidden = false;
      } else {
        closePop();
      }
    });
    wrap.querySelectorAll("[data-step]").forEach((b) =>
      b.addEventListener("click", () => {
        const ni = days.indexOf(selected) + Number(b.getAttribute("data-step"));
        if (ni >= 0 && ni < days.length) select(days[ni]);
      }),
    );
    wrap.querySelector(".hist-today").addEventListener("click", () => {
      closePop();
      if (selected !== today) select(today);
    });
    // Click anywhere outside the popover (but inside the view) dismisses it.
    wrap.addEventListener("click", (e) => {
      if (!pop.hidden && !pop.contains(e.target) && !dateBtn.contains(e.target)) closePop();
    });

    syncBar();
    // Fire-and-forget so the body renders AFTER the wrap is in the DOM (Leaflet needs a laid-out box).
    this._showDay(historyEntity, body, today);
    return wrap;
  }

  _esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _css() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; color: var(--primary-text-color); }

      .ov-header { display: flex; align-items: center; gap: 14px; padding: 16px; }
      .avatar { width: 56px; height: 56px; border-radius: 50%; background-size: cover; background-position: center; flex: 0 0 auto; box-shadow: 0 0 0 2px var(--divider-color); }
      .avatar.fallback { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .avatar.fallback ha-icon { --mdc-icon-size: 30px; }
      .head-text { min-width: 0; flex: 1; }
      .controls-btn { color: var(--secondary-text-color); flex: 0 0 auto; }
      .name { font-size: 1.3rem; font-weight: 500; line-height: 1.2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .status { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 4px; color: var(--secondary-text-color); font-size: 0.95rem; }
      .status .sep { opacity: 0.5; }
      .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 4px; vertical-align: middle; }
      .dot.on { background: var(--success-color, #43a047); }
      .dot.off { background: var(--disabled-text-color, #9e9e9e); }
      .batt { display: inline-flex; align-items: center; gap: 2px; }
      .batt ha-icon { --mdc-icon-size: 18px; }
      .batt.ok { color: var(--success-color, #43a047); }
      .batt.mid { color: var(--warning-color, #ffa600); }
      .batt.low { color: var(--error-color, #db4437); }
      .charging { display: inline-flex; align-items: center; gap: 2px; color: var(--warning-color, #ffa600); }
      .charging ha-icon { --mdc-icon-size: 18px; }
      .upd { display: inline-flex; align-items: center; gap: 2px; }
      .upd ha-icon { --mdc-icon-size: 16px; }
      .upd.success { color: var(--success-color, #43a047); }
      .upd.warning { color: var(--warning-color, #ffa600); }
      .upd.error { color: var(--error-color, #db4437); }
      /* On-render refresh in flight: neutral text + a spinning refresh icon (keyframes below). */
      .upd.refreshing { color: var(--secondary-text-color); }
      .upd ha-icon.spin { animation: xplora-spin 0.9s linear infinite; }

      .row { display: flex; align-items: center; gap: 12px; width: 100%; box-sizing: border-box;
        padding: 12px 16px; border-top: 1px solid var(--divider-color);
        background: none; border-left: none; border-right: none; border-bottom: none;
        font: inherit; color: inherit; text-align: left; cursor: pointer; }
      .row:hover { background: var(--secondary-background-color); }
      .row-icon { color: var(--primary-color); --mdc-icon-size: 24px; flex: 0 0 auto; }
      .row-text { flex: 1; min-width: 0; }
      .row-main { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .row-sub { color: var(--secondary-text-color); font-size: 0.85rem; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .row-chevron { color: var(--secondary-text-color); --mdc-icon-size: 22px; flex: 0 0 auto; }

      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; padding: 12px 16px 16px; }
      .tile { display: flex; flex-direction: column; align-items: center; gap: 2px;
        padding: 12px 8px; border-radius: 12px;
        background: var(--secondary-background-color); border: none;
        font: inherit; color: inherit; cursor: pointer; }
      .tile:hover { filter: brightness(0.97); }
      .tile ha-icon { --mdc-icon-size: 24px; color: var(--primary-color); margin-bottom: 2px; }
      .tile-value { font-size: 1.15rem; font-weight: 600; }
      .tile-value .unit { font-size: 0.8rem; font-weight: 400; color: var(--secondary-text-color); }
      .tile-label { font-size: 0.78rem; color: var(--secondary-text-color); }

      /* The popup host injects its own generic overlay CSS (.modal-host / .card-popup / .popup-*)
         into this shadow root (ADR 0008); only the keyframes the header spinner shares live here. */
      @keyframes xplora-spin { to { transform: rotate(360deg); } }

      /* ---- location history (map track) popup ---- */
      .hist-wrap { display: flex; flex-direction: column; min-height: 0; height: 100%;
        background: var(--card-background-color, var(--ha-card-background, #fff)); border-radius: 12px; overflow: hidden; }
      /* ---- date bar: "< DD.MM.YYYY >" + today button, with a calendar popover ---- */
      /* z-index:1 (vs the map body's z-index:0) keeps the popover painting above Leaflet's panes. */
      .hist-bar { position: relative; z-index: 1; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
        gap: 4px; padding: 8px; border-bottom: 1px solid var(--divider-color); }
      .hist-nav { background: none; border: none; color: var(--primary-text-color); cursor: pointer; padding: 4px;
        display: inline-flex; border-radius: 50%; }
      .hist-nav:hover { background: var(--secondary-background-color); }
      .hist-nav[disabled] { opacity: 0.3; cursor: default; background: none; }
      .hist-date { background: none; border: none; color: var(--primary-text-color); cursor: pointer; font: inherit; font-weight: 600;
        font-variant-numeric: tabular-nums; padding: 4px 12px; border-radius: 8px; min-width: 120px; text-align: center; }
      .hist-date:hover { background: var(--secondary-background-color); }
      .hist-today { background: none; border: none; color: var(--primary-color); cursor: pointer; font: inherit; font-weight: 500;
        padding: 4px 10px; border-radius: 8px; margin-left: 4px; }
      .hist-today:hover { background: var(--secondary-background-color); }
      .hist-today[disabled] { color: var(--disabled-text-color, #9e9e9e); cursor: default; background: none; }
      .hist-pop { position: absolute; top: 100%; left: 50%; transform: translateX(-50%); margin-top: 4px;
        background: var(--card-background-color, var(--ha-card-background, #fff)); border-radius: 12px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3); padding: 8px; }
      .hist-pop[hidden] { display: none; }
      /* ---- month calendar inside the popover (data days highlighted) ---- */
      .hist-cal { width: 280px; max-width: 80vw; }
      .hist-cal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
      .hist-cal-title { font-weight: 500; font-size: 0.95rem; }
      .hist-cal-nav { background: none; border: none; color: var(--primary-text-color); cursor: pointer; padding: 2px; display: inline-flex; }
      .hist-cal-nav[disabled] { opacity: 0.3; cursor: default; }
      .hist-cal-week, .hist-cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
      .hist-cal-week span { text-align: center; font-size: 0.7rem; color: var(--secondary-text-color); }
      .hist-cell { aspect-ratio: 1; max-height: 38px; display: flex; align-items: center; justify-content: center; border: none;
        background: none; border-radius: 50%; color: var(--primary-text-color); font: inherit; font-size: 0.8rem; cursor: default; padding: 0; }
      .hist-cell.empty { visibility: hidden; }
      .hist-cell[disabled] { color: var(--disabled-text-color, #9e9e9e); opacity: 0.4; }
      .hist-cell.has-data { cursor: pointer; background: var(--secondary-background-color); font-weight: 500; }
      .hist-cell.has-data:hover { filter: brightness(0.95); }
      .hist-cell.today { outline: 2px solid var(--primary-color); }
      .hist-cell.sel { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      /* position:relative + an absolutely-filled ha-map gives Leaflet a definite box to size to;
         a flex-only chain leaves the map short (it can't resolve its own height). z-index:0 puts the
         whole map (and Leaflet's internal panes) into a stacking context below the date bar's popover. */
      .hist-body { flex: 1; min-height: 0; position: relative; z-index: 0; display: flex; }
      .hist-body > ha-map { position: absolute; inset: 0; height: 100%; width: 100%; }
      .hist-msg { flex: 1; display: flex; align-items: center; justify-content: center; padding: 24px; color: var(--secondary-text-color); }
      .hist-list { flex: 1; list-style: none; margin: 0; padding: 0; overflow: auto; }
      .hist-item { display: flex; gap: 12px; padding: 10px 14px; border-top: 1px solid var(--divider-color); }
      .hist-item:first-child { border-top: none; }
      .hist-time { flex: 0 0 auto; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; white-space: nowrap; }
      .hist-addr { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    `;
  }
}

if (!customElements.get("xplora-watch-overview-card")) {
  customElements.define("xplora-watch-overview-card", XploraWatchOverviewCard);
}

window.customCards.push({
  type: "xplora-watch-overview-card",
  name: "Xplora Watch Overview",
  description: "App-like summary of a watch: online, battery, last location, steps, coins, messages and more.",
  preview: true,
});

// Fix-age banner for the map card (ADR 0007): the STATUS (colour/icon) is the poll outcome from the
// `last_update` sensor; the age in the TEXT is the WATCH's own fix time, never our poll time. When
// there is no poll outcome to report we show a neutral "Location" state carrying just the age, so the
// banner never falsely claims "Updated". One place so the inline card and the full-screen popup (the
// same component) can't drift.
function mapBanner(hass, entities) {
  const { status: rawStatus, fixIso } = fixAgeStatus(hass, entities);
  if (!rawStatus) {
    const age = relTimeIso(fixIso);
    return { status: "unknown", text: `Location${age ? ` · ${age}` : ""}` };
  }
  const phrase = locationAgePhrase(fixIso);
  return { status: rawStatus, text: `${STATUS_META[rawStatus].label}${phrase ? ` · ${phrase}` : ""}` };
}

/**
 * Xplora® Watch location map card.
 *
 * Renders ONE watch's current position inline on a dashboard (ADR 0008): HA's built-in `map` card,
 * a fix-age banner (ADR 0007), a reload button that forces a fresh fix by pressing the watch's
 * Update button, and an expand button that opens the SAME card full-screen. It is Guardian-only --
 * a Contact account has no location data (ref:XW-009) -- and says so honestly. The overview card's
 * location-row popup mounts this very component in fill mode, so the inline and full-screen views
 * can never drift.
 *
 * Config:
 *   type: custom:xplora-watch-map-card
 *   entity / device   - any watch entity, or the device id (one is required)
 *   title             - overrides the displayed watch name (default: the watch's device name)
 *   aspect_ratio      - map aspect ratio passed to HA's map card (default "16:9")
 *   show_header       - show the avatar + name header (default true)
 */
class XploraWatchMapCard extends HTMLElement {
  // "<domain>.<xplora_role>" -> this card's logical role (ADR 0005): keyed on the integration-emitted
  // role attribute, not the entity_id, so account-tokened / renamed ids still resolve.
  static ROLE_BY_DOMAIN_ROLE = {
    "device_tracker.tracker": "tracker",
    "sensor.last_update": "lastupdate",
    "button.update": "updateBtn",
  };

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._fill = false; // internal: set true by the popup host before mounting (full-screen mode)
    this._ent = {}; // resolved entities keyed by logical role
    this._deviceId = null;
    this._sig = "";
    this._mapEl = null; // the embedded HA `map` card element (built once, kept hass-live)
    this._mapKey = null; // rebuild key for the embedded map (tracker id + aspect/fill)
    this._mapGen = 0; // generation token so a superseded async map build can't overwrite a newer one
    this._autoRefreshDone = false; // guard so "refresh on render" fires once per card instance
    this._reloading = false; // a reload is in flight (drives the spinner + guards a double press)
    this._popup = new CardPopupHost(this.shadowRoot, () => this._hass);
  }

  // Internal fill-mode flag, set by the popup host before mounting (never a config key). In fill
  // mode the card stretches the map to the popup height and suppresses its own header + expand
  // button, so the full-screen popup can't recursively re-open itself (ADR 0008).
  set fill(v) {
    this._fill = !!v;
    if (this._built && this._hass) this._render();
  }

  get fill() {
    return this._fill;
  }

  setConfig(config) {
    if (!config || (!config.entity && !config.device)) {
      throw new Error("xplora-watch-map-card: define `entity` (any watch entity) or `device`.");
    }
    this._config = config;
    this._sig = ""; // force a rebuild on the next hass push
    if (this._hass) this._render();
  }

  static getStubConfig(hass) {
    const ids = hass && hass.states ? Object.keys(hass.states) : [];
    const tracker = ids.find((id) => id.startsWith("device_tracker.") && roleOf(hass, id) === "tracker");
    const any = ids.find((id) => id.includes("xplora") && id.includes("_watch_"));
    return { entity: tracker || any || "device_tracker.xplora_watch_tracker" };
  }

  getCardSize() {
    return 6; // header + banner + a map roughly the height of the overview card
  }

  disconnectedCallback() {
    this._resetMap(); // bump _mapGen so an in-flight map build can't mount into a detached shadow root
    this._popup.close();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    this._popup.setHass(hass); // keep an expanded (fill-mode) popup live
    if (this._mapEl) this._mapEl.hass = hass; // keep the embedded map's marker current without a rebuild
    const ent = this._resolveEntities();
    const sig = this._signature(ent);
    if (!this._built || sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
    this._maybeRefreshOnRender();
  }

  get hass() {
    return this._hass;
  }

  // Discover the watch's tracker / last-update / update-button entities from the registry, given any
  // one watch entity (or the device id). Placed by (domain, integration-emitted `xplora_role`), never
  // by parsing the entity_id (ADR 0005). A disabled Update button has no state -> roleOf() is
  // undefined -> it is not discovered, which is exactly the "reload only when enabled" rule.
  _resolveEntities() {
    const hass = this._hass;
    const conf = this._config;
    const ents = (hass && hass.entities) || {};
    let deviceId = conf.device || null;
    if (!deviceId && conf.entity && ents[conf.entity]) deviceId = ents[conf.entity].device_id;
    this._deviceId = deviceId;

    const map = {};
    const place = (id) => {
      const role = XploraWatchMapCard.ROLE_BY_DOMAIN_ROLE[`${id.split(".")[0]}.${roleOf(hass, id)}`];
      if (role) map[role] = id;
    };
    if (deviceId) {
      Object.values(ents).forEach((e) => {
        if (e.device_id === deviceId) place(e.entity_id);
      });
    }
    // Fallback when the registry isn't exposed: at least map the configured entity itself.
    if (Object.keys(map).length === 0 && conf.entity) place(conf.entity);
    return map;
  }

  // A re-render is warranted when any resolved entity's state/last_updated changed, the device name
  // changed, or fill mode flipped -- HA pushes a fresh `hass` on ANY system change, so we filter.
  _signature(ent) {
    const hass = this._hass;
    return (
      Object.values(ent)
        .map((id) => {
          const s = hass.states[id];
          return s ? `${id}=${s.state}@${s.last_updated}` : `${id}=∅`;
        })
        .join("|") +
      `|${this._deviceName()}|${this._fill ? "fill" : "inline"}`
    );
  }

  _deviceName() {
    if (this._config && this._config.title) return this._config.title;
    const dev = this._deviceId && this._hass.devices && this._hass.devices[this._deviceId];
    if (dev) return dev.name_by_user || dev.name || "Watch";
    return "Watch";
  }

  _usable(stateObj) {
    return stateObj && stateObj.state !== "unavailable" && stateObj.state !== "unknown" && stateObj.state !== "";
  }

  // The tracker's current coordinates, or null when there is no usable fix. A device_tracker with no
  // fix drops its latitude/longitude attributes, so absence means "no position" -- we must never
  // render the `map` card without coordinates (it would silently centre on Home, ADR 0008).
  _coords(trk) {
    const lat = trk && trk.attributes ? trk.attributes.latitude : undefined;
    const lng = trk && trk.attributes ? trk.attributes.longitude : undefined;
    if (lat == null || lng == null || !isFinite(Number(lat)) || !isFinite(Number(lng))) return null;
    return { lat: Number(lat), lng: Number(lng) };
  }

  _showHeader() {
    // Fill mode is the full-screen popup -- the watch context is already obvious there, so the header
    // (like the old popup) is suppressed; inline it is shown unless the user opts out.
    return !this._fill && !(this._config && this._config.show_header === false);
  }

  _headerHtml(trk) {
    if (!this._showHeader()) return "";
    const picture = trk && trk.attributes && trk.attributes.entity_picture;
    const avatar = picture
      ? `<div class="map-avatar" style="background-image:url('${this._esc(picture)}')"></div>`
      : `<div class="map-avatar fallback"><ha-icon icon="mdi:watch"></ha-icon></div>`;
    return `<div class="map-header">
        ${avatar}
        <div class="map-name">${this._esc(this._deviceName())}</div>
      </div>`;
  }

  // True when a device_tracker entity EXISTS in the registry for this watch's device -- regardless of
  // whether it has pushed state yet. Distinguishes a Guardian whose tracker is merely warming up (it
  // is in the registry) from a Contact watch, for which the integration creates NO tracker at all
  // (ref:XW-009). Used to pick the permanent Contact message vs the transient "waiting" state.
  _hasTrackerEntity() {
    const ents = (this._hass && this._hass.entities) || {};
    if (!this._deviceId) return false;
    return Object.values(ents).some((e) => e.device_id === this._deviceId && e.entity_id.startsWith("device_tracker."));
  }

  // True when the registry has loaded AND lists at least one entity for this watch's device. This is
  // the positive evidence the Contact verdict needs: an empty/not-yet-loaded `hass.entities` (common
  // in the first frames after connect) is otherwise indistinguishable from "device genuinely has no
  // tracker", and a `device:`-bound Guardian would flash the permanent Contact message on cold start.
  _deviceHasEntities() {
    const ents = (this._hass && this._hass.entities) || {};
    if (!this._deviceId) return false;
    return Object.values(ents).some((e) => e.device_id === this._deviceId);
  }

  _render() {
    if (!this._built) {
      const style = document.createElement("style");
      style.textContent = this._css();
      this.shadowRoot.appendChild(style);
      this._card = document.createElement("ha-card");
      this.shadowRoot.appendChild(this._card);
      this._built = true;
    }

    const hass = this._hass;
    const ent = (this._ent = this._resolveEntities());
    const trk = ent.tracker ? hass.states[ent.tracker] : undefined;

    // No usable tracker discovered: figure out WHY so we show the right message (ADR 0008).
    if (!ent.tracker) {
      this._resetMap();
      let msg;
      let cls = "map-empty";
      if (this._hasTrackerEntity()) {
        // Guardian tracker exists but hasn't pushed state yet -- transient, retry on the next push.
        msg = "Locating…";
      } else if (this._deviceHasEntities()) {
        // Registry loaded and this device has entities but NO tracker: a Contact account has no
        // location data at all (ref:XW-009). Permanent -- no reload can conjure a tracker, so none
        // is offered (ADR 0008). Gated on `_deviceHasEntities` (not just `_deviceId`) so an empty /
        // not-yet-loaded registry doesn't misread a real Guardian as a Contact on cold start.
        msg = "Location isn't available for this account type.";
        cls = "map-empty contact";
      } else if (this._config.entity && entityMissing(hass, this._config.entity)) {
        // Bound to an entity that isn't in the registry -- a mis-configured card (ADR 0008).
        msg = `Entity ${this._config.entity} not found — edit the card and pick a watch entity.`;
        cls = "map-empty error";
      } else {
        // Device known but its entities (or the registry) haven't loaded yet -- transient.
        msg = "Locating…";
      }
      this._card.innerHTML = `${this._headerHtml(undefined)}<div class="map-body"><div class="${cls}">${this._esc(msg)}</div></div>`;
      return;
    }

    const coords = this._coords(trk);
    const banner = mapBanner(hass, ent);
    const meta = STATUS_META[banner.status] || STATUS_META.unknown;
    // Reload is offered ONLY when the Update button is enabled (a disabled button has no state, so
    // it is never discovered) -- there is no honest reload without it (ADR 0008). Expand opens the
    // same card full-screen; it is suppressed in fill mode (no recursive re-open) and when there is
    // no map worth enlarging (no coordinates).
    // The spinner/disabled state is derived from `this._reloading` (an instance flag), NOT set
    // imperatively on the DOM node, so it survives a re-render triggered by an unrelated background
    // `hass` push mid-reload -- which would otherwise swap in a fresh, enabled button and let the
    // user fire a second overlapping press.
    const reloadBtn = ent.updateBtn
      ? `<ha-icon-button class="map-reload" data-reload label="Refresh location"${this._reloading ? " disabled" : ""}><ha-icon icon="mdi:refresh"${this._reloading ? ' class="spin"' : ""}></ha-icon></ha-icon-button>`
      : "";
    const expandBtn =
      coords && !this._fill
        ? `<ha-icon-button class="map-expand" data-expand label="Expand"><ha-icon icon="mdi:arrow-expand"></ha-icon></ha-icon-button>`
        : "";
    const bannerHtml = `
      <div class="map-banner ${banner.status}">
        <ha-icon icon="${meta.icon}"></ha-icon>
        <span class="map-banner-text">${this._esc(banner.text)}</span>
        ${reloadBtn}${expandBtn}
      </div>`;

    this._card.innerHTML = `${this._headerHtml(trk)}${bannerHtml}<div class="map-body"></div>`;

    const reloadEl = this._card.querySelector("[data-reload]");
    if (reloadEl) reloadEl.addEventListener("click", () => this._reload());
    const expandEl = this._card.querySelector("[data-expand]");
    if (expandEl) expandEl.addEventListener("click", () => this._expand());

    const body = this._card.querySelector(".map-body");
    if (coords) {
      this._ensureMapEl(body);
    } else {
      // Guardian with a tracker but no fix yet: transient, keep the reload button live so the first
      // fix can be requested. NEVER render the `map` card without coordinates (ADR 0008).
      this._resetMap();
      body.innerHTML = `<div class="map-empty">Location unavailable</div>`;
    }
  }

  // Drop any embedded map so a later coords-less render can't leave a stale marker around.
  _resetMap() {
    this._mapEl = null;
    this._mapKey = null;
    this._mapGen++;
  }

  _aspectRatio() {
    return (this._config && this._config.aspect_ratio) || MAP_CARD_DEFAULT_ASPECT;
  }

  // Build (once) and mount HA's built-in `map` card for the tracker, swapping it in over a
  // placeholder. Rebuilt only when the tracker id or the aspect/fill mode changes -- a plain hass
  // push updates the existing map's marker (via `set hass`) without resetting the pan/zoom.
  _ensureMapEl(body) {
    const key = `${this._ent.tracker}|${this._fill ? "fill" : this._aspectRatio()}`;
    if (this._mapEl && this._mapKey === key) {
      body.appendChild(this._mapEl); // re-attach the persistent map into the freshly-rendered body
      return;
    }
    this._mapEl = null;
    this._mapKey = key;
    const gen = ++this._mapGen;
    body.innerHTML = `<div class="map-empty">Loading map…</div>`;
    (async () => {
      try {
        const helpers = window.loadCardHelpers && (await window.loadCardHelpers());
        if (!helpers) return; // non-HA host (jsdom without a stub): leave the placeholder
        const conf = { type: "map", entities: [this._ent.tracker], default_zoom: POSITION_MAP_ZOOM };
        // Inline passes an aspect ratio (responsive by construction); fill mode stretches instead.
        if (!this._fill) conf.aspect_ratio = this._aspectRatio();
        const el = await helpers.createCardElement(conf);
        if (gen !== this._mapGen) return; // superseded by a newer build (key changed / unmounted)
        el.hass = this._hass;
        if (this._fill) el.style.height = "100%";
        this._mapEl = el;
        const b = this._card.querySelector(".map-body");
        if (b) {
          b.innerHTML = "";
          b.appendChild(el);
        }
      } catch (e) {
        if (gen !== this._mapGen) return;
        const b = this._card.querySelector(".map-body");
        if (b) b.innerHTML = `<div class="map-empty">Map unavailable</div>`;
      }
    })();
  }

  // Poll for the `last_update` sensor to reflect a just-requested refresh (it arrives via websocket
  // asynchronously after the button press resolves). Reads `this._hass` fresh each tick so it sees
  // background pushes.
  async _awaitLastUpdateChange(entityId, beforeStamp, timeoutMs) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 250));
      const lu = entityId ? this._hass.states[entityId] : null;
      if (lu && lu.last_updated !== beforeStamp) return true;
    }
    return false;
  }

  // Reload = force a fresh fix by pressing the watch's Update button (the integration's `see`), then
  // await the poll outcome and re-derive the banner in place. The banner also re-derives on the next
  // hass push, but recompute now so the spinner clears against fresh data (ADR 0008).
  async _reload() {
    const ent = this._ent;
    // `_reloading` guards re-entrancy (a second tap, or a click on a button a mid-flight re-render
    // rebuilt) and drives the spinner/disabled state through `_render`, so it survives re-renders.
    if (!ent.updateBtn || this._reloading) return;
    this._reloading = true;
    this._render(); // reflect the spinner + disabled state
    try {
      // Mark the dedup window BEFORE pressing (like the other refresh sites) so a co-rendered card's
      // render-refresh can't slip a second `see` through the window while this press is in flight.
      markRefreshed(`button.press|${ent.updateBtn}`);
      const before = ent.lastupdate ? this._hass.states[ent.lastupdate] : null;
      await this._hass.callService("button", "press", { entity_id: ent.updateBtn });
      await this._awaitLastUpdateChange(ent.lastupdate, before ? before.last_updated : null, 5000);
      this.dispatchEvent(new CustomEvent("xplora-update-status", { bubbles: true, composed: true }));
    } catch (e) {
      // A rejected press (e.g. the watch is offline) must NOT leave the button stuck disabled and
      // spinning -- the `finally` always clears `_reloading` and re-renders a live button.
      console.error("xplora-watch-map-card: reload failed", e);
    } finally {
      this._reloading = false;
      this._sig = ""; // force the next render to rebuild the banner from fresh state
      if (this._hass) this._render();
    }
  }

  // Open the SAME card full-screen (fill mode) via the shared popup host. Fill mode suppresses the
  // header + expand button, so the popup can't recursively re-open itself (ADR 0008).
  _expand() {
    this._popup.open(
      () => {
        const c = document.createElement("xplora-watch-map-card");
        c.fill = true;
        c.setConfig(this._config);
        return c;
      },
      { fill: true },
    );
  }

  _esc(v) {
    return escapeHtml(v);
  }

  _css() {
    return `
      :host { display: block; height: 100%; }
      ha-card { overflow: hidden; color: var(--primary-text-color); height: 100%; display: flex; flex-direction: column; }

      .map-header { display: flex; align-items: center; gap: 12px; padding: 12px 16px; flex: 0 0 auto; }
      .map-avatar { width: 40px; height: 40px; border-radius: 50%; background-size: cover; background-position: center;
        flex: 0 0 auto; box-shadow: 0 0 0 2px var(--divider-color); }
      .map-avatar.fallback { display: flex; align-items: center; justify-content: center; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .map-avatar.fallback ha-icon { --mdc-icon-size: 24px; }
      .map-name { font-size: 1.1rem; font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

      /* Fix-age banner (ADR 0007): status icon + age text + reload/expand buttons. The text WRAPS
         (never truncates) so a long status can't hide it or the buttons on a phone; the buttons are
         fixed-size 48px tap targets that never shrink regardless of the text length. */
      .map-banner { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 8px 8px 8px 14px;
        font-size: 0.92rem; font-weight: 500; border-bottom: 1px solid var(--divider-color); }
      .map-banner > ha-icon { --mdc-icon-size: 18px; flex: 0 0 auto; }
      .map-banner.success { color: var(--success-color, #43a047); }
      .map-banner.warning { color: var(--warning-color, #ffa600); }
      .map-banner.error { color: var(--error-color, #db4437); }
      .map-banner.unknown { color: var(--secondary-text-color, #888); }
      .map-banner-text { flex: 1 1 auto; min-width: 0; white-space: normal; overflow-wrap: anywhere; color: var(--primary-text-color); }
      /* Fixed 48px tap targets that never shrink, so a long banner can't clip or hide them. */
      .map-reload, .map-expand { flex: 0 0 auto; min-width: 48px; min-height: 48px; color: var(--secondary-text-color); }
      @keyframes xplora-spin { to { transform: rotate(360deg); } }
      .map-reload ha-icon.spin { animation: xplora-spin 0.9s linear infinite; }

      /* Map body: the HA map card fills the remaining height. */
      .map-body { flex: 1 1 auto; min-height: 0; position: relative; display: flex; }
      .map-body > * { flex: 1 1 auto; min-height: 0; width: 100%; }
      .map-empty { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center;
        padding: 24px 16px; color: var(--secondary-text-color); min-height: 120px; }
      .map-empty.error { color: var(--error-color, #db4437); }
    `;
  }

  // When the user enabled "refresh on render", press the watch's Update button once on first show to
  // pull a fresh fix -- deduplicated via the shared trackInflight key `button.press|<updateBtn>` so a
  // dashboard showing both the overview and the map card fires only ONE `see`, not two (ban hygiene,
  // ADR 0008). Off by default. Never fires without an enabled Update button (nothing to press).
  _maybeRefreshOnRender() {
    if (this._autoRefreshDone) return;
    const ent = this._ent;
    // Read the opt-in from any resolved watch entity (every one carries the attribute). Wait until at
    // least one is alive, so a cold start doesn't decide "off" before the entity has pushed state.
    const anyId = ent.tracker || ent.lastupdate || ent.updateBtn;
    const primary = anyId && this._hass.states[anyId];
    if (!this._usable(primary)) return; // not alive yet -- retry on the next hass push
    this._autoRefreshDone = true;
    if (!refreshOnRenderEnabled(primary)) return;
    if (!ent.updateBtn) return;
    trackInflight(`button.press|${ent.updateBtn}`, () => this._hass.callService("button", "press", { entity_id: ent.updateBtn }));
  }
}

if (!customElements.get("xplora-watch-map-card")) {
  customElements.define("xplora-watch-map-card", XploraWatchMapCard);
}

window.customCards.push({
  type: "xplora-watch-map-card",
  name: "Xplora Watch Map",
  description: "A watch's current location on a map, with its fix age and a button to pull a fresh position.",
  preview: true,
});

/**
 * Xplora® Watch chat card.
 *
 * A messenger-style view of ONE watch's chat history with a composer to send a new text message.
 * Everything is driven by the integration's existing `sensor.*_message` entity, which exposes the
 * full chat list plus the `entry_id`/`wuid` the message services need — so the only required config
 * is `entity`.
 *
 * Messages are rendered as left/right bubbles (left = from the watch/kid, right = sent from the
 * app/parent), oldest at the top, newest at the bottom (auto-scrolled into view). TEXT shows the
 * text; VOICE / IMAGE / SHORT_VIDEO render the media cached by the integration under
 * `config/www/{voice,image,video}/<msgId>.<ext>` (served at `/local/...`) — these only appear
 * after a read/refresh has downloaded them, so the card triggers one automatically when it first
 * opens with no cached messages.
 *
 * Driven services (Python side `const.ATTR_SERVICE_*`); the watch is targeted by the card's bound
 * entity (resolved to its device server-side):
 *   - `send_message` (compose box) — { message, entity_id:[<bound entity>] }
 *   - `read_message` (refresh)     — { entity_id:[<bound entity>] }
 *
 * Config:
 *   type: custom:xplora-watch-chat-card
 *   entity: sensor.kid_one_watch_message   # the watch's *_message sensor (required; enable it on
 *                                          # the device page — it's off by default)
 *   title: Messages                        # optional override
 *
 * The composer is built once and kept across state updates so typing is never interrupted when a
 * background poll pushes a fresh `hass`; only the message list re-renders.
 */
const CHAT_SERVICE = Object.freeze({
  SEND: "send_message",
  READ: "read_message",
});

// Media base paths (the integration writes downloaded attachments here, keyed by msgId).
const MEDIA = Object.freeze({
  voice: "/local/voice", // <msgId>.mp3
  image: "/local/image", // <msgId>.jpeg
  video: "/local/video", // <msgId>.mp4  (+ thumb at /local/video/thumb/<msgId>.jpeg)
});

// Xplora emoticon code -> Unicode glyph. EMOTICON messages usually carry the glyph directly in
// `data.emoticon_id`; this map is the fallback for payloads that only provide the numeric code.
// Mirrors the `Emoji` enum in pyxplora_api/status.py; codes with no real glyph upstream are omitted.
const EMOJI_MAP = Object.freeze({
  M1001: "😄",
  M1002: "😏",
  M1003: "😘",
  M1004: "😅",
  M1005: "😂",
  M1006: "😭",
  M1007: "😍",
  M1008: "😎",
  M1009: "😜",
  M1010: "😳",
  M1011: "🥱",
  M1012: "👏",
  M1013: "😡",
  M1014: "👍",
  M1015: "😏",
  M1016: "😓",
  M1017: "🍧",
  M1018: "😮",
  M1020: "🎁",
  M1022: "☺️",
  M1024: "🌹",
});

class XploraWatchChatCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._sig = ""; // signature of the rendered message list, to skip needless re-renders
    this._busy = false; // a send is in flight
    this._refreshing = false; // a read/refresh is in flight
    this._autoFetched = false; // whether the one-shot "fetch on first open" already ran
    this._pending = []; // optimistically-rendered sent messages awaiting the server echo
    this._localSeq = 0; // monotonic id source for optimistic messages
    this._forceScroll = false; // force a scroll-to-bottom on the next render (set right after a send)
    this._native = false; // native (Fullscreen API) fullscreen is active for this card
    this._cssExpanded = false; // CSS-maximize fallback active (when the Fullscreen API is unavailable)
    // The browser can leave native fullscreen without going through our button (Esc key, browser UI).
    // When nothing is fullscreen anymore, clear our flag and reconcile. (We can't test "is it US?"
    // via `document.fullscreenElement` -- in a shadow tree it reports the shadow host, not us.)
    this._onFsChange = () => {
      if (!document.fullscreenElement) {
        this._native = false;
        this._syncFullscreen();
      }
    };
    this._lightbox = null; // lazily-built image lightbox overlay
    this._onLightboxKey = (ev) => {
      if (ev.key === "Escape") this._closeLightbox();
    };
  }

  connectedCallback() {
    document.addEventListener("fullscreenchange", this._onFsChange);
  }

  disconnectedCallback() {
    document.removeEventListener("fullscreenchange", this._onFsChange);
    window.removeEventListener("keydown", this._onLightboxKey);
    // Best-effort: don't leave the page stuck in fullscreen if the card is removed while expanded.
    if (this._native && document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("xplora-watch-chat-card: an `entity` (a *_message sensor) is required.");
    }
    this._config = config;
    if (this._hass) this._render();
  }

  // Opt-in console logging (set `debug: true` in the card config). Prefixed so it's easy to filter
  // in the browser console.
  _debug(...args) {
    if (this._config && this._config.debug) console.info("[xplora-watch-chat]", ...args);
  }

  static getStubConfig(hass) {
    let entity = "sensor.watch_message";
    if (hass && hass.states) {
      const match = Object.keys(hass.states).find((id) => roleOf(hass, id) === "message");
      if (match) entity = match;
    }
    return { entity };
  }

  getCardSize() {
    return 8; // a chat is tall; reserve a generous slot
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    // Only re-render when the bound sensor changed or the locale changed, so an unrelated entity
    // update on a busy dashboard doesn't rebuild the list. NOTE: use `last_updated`, NOT
    // `last_changed` -- new chat messages arrive as ATTRIBUTE changes (the message list), and
    // `last_changed` only moves when the state *value* changes, so a refresh would otherwise be
    // skipped and new messages wouldn't appear until a full page reload.
    const s = hass.states[this._config.entity];
    // The "no state" case splits into coming-up vs missing (see entityMissing); fold that into the
    // signature so the placeholder re-renders when the registry verdict flips, not only on state.
    const empty = entityMissing(hass, this._config.entity) ? "missing" : "∅";
    const sig = (s ? `${s.state}@${s.last_updated}` : empty) + "|" + this._locale();
    if (!this._built || sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
    this._maybeAutoFetch();
  }

  get hass() {
    return this._hass;
  }

  /* --------------------------------------------------------------- helpers */

  _stateObj() {
    if (!this._hass || !this._config) return undefined;
    return this._hass.states[this._config.entity];
  }

  _attrs() {
    return (this._stateObj() || {}).attributes || {};
  }

  _locale() {
    return (this._hass && this._hass.locale && this._hass.locale.language) || navigator.language || "en";
  }

  _base() {
    // Message services target HA devices; the card targets the watch by its bound message entity
    // (the handler resolves `entity_id` -> its device -> the (account, watch)).
    return { entity_id: [this._config.entity] };
  }

  // Chat entries sorted oldest -> newest. Each is a `SimpleChat` dict (see pyxplora_api/model.py).
  _messages() {
    const list = Array.isArray(this._attrs().list) ? this._attrs().list.slice() : [];
    this._reconcilePending(list);
    return list.concat(this._pending).sort((a, b) => this._stamp(a) - this._stamp(b));
  }

  // Drop optimistic sends once the server echoes them back (matched by an outgoing message with the
  // same text), and expire any that were never confirmed within PENDING_SEND_TTL_MS so a lost send
  // can't leave a permanent ghost bubble. Mutates `this._pending` in place.
  _reconcilePending(list) {
    if (!this._pending.length) return;
    const now = Date.now();
    const realOutgoing = list.filter((m) => !this._incoming(m)).map((m) => this._text(m).trim());
    this._pending = this._pending.filter((p) => {
      if (now - p.create > PENDING_SEND_TTL_MS) return false;
      const i = realOutgoing.indexOf(((p.data && p.data.text) || "").trim());
      if (i >= 0) {
        realOutgoing.splice(i, 1); // consume one match so identical re-sends aren't both dropped
        return false;
      }
      return true;
    });
  }

  _stamp(msg) {
    // `create` (ms or s) is the message time; fall back to the inner data `tm`.
    const raw = msg && (msg.create != null ? msg.create : msg.data && msg.data.tm);
    let n = Number(raw);
    if (!isFinite(n)) return 0;
    if (n > 0 && n < 1e12) n *= 1000; // seconds -> ms
    return n;
  }

  // True when the message came FROM the watch (kid) -> shown on the LEFT. Anything we sent from
  // Home Assistant / the app is shown on the RIGHT. Direction is decided from the sender id:
  //   - definitively OUTGOING when `sender.id` is our own account id (`account_user_id`) -- when we
  //     send, "sender is login User" (see pyxplora_api `sendText`);
  //   - otherwise INCOMING (it's the watch, whose `sender.id` is the `wuid`).
  // The account-id check is the reliable signal; the wuid check is a fallback. Unknown senders
  // default to incoming (left).
  _incoming(msg) {
    const a = this._attrs();
    const sender = (msg && msg.sender) || {};
    const sid = sender.id != null ? sender.id : sender.userId;
    if (a.account_user_id != null && sid === a.account_user_id) return false; // we sent it
    if (a.wuid != null && sid === a.wuid) return true; // from the watch
    return true; // unknown -> treat as incoming
  }

  _text(msg) {
    const d = (msg && msg.data) || {};
    return d.text || d.Text || "";
  }

  // True when a text string is nothing but emoji (+ whitespace). Messages we send carry their emoji
  // inside `data.text` (a TEXT message, not an EMOTICON), so without this they'd render at normal
  // text size -- much smaller than the watch's EMOTICON glyphs. An emoji-only message is enlarged
  // wholesale (the "jumbomoji" behavior of common chat apps); mixed text enlarges emoji inline.
  _isEmojiOnly(text) {
    const t = (text || "").trim();
    if (!t) return false;
    return t.replace(new RegExp(EMOJI_SEQ_SRC, "gu"), "").trim() === "";
  }

  // Render message text with each emoji wrapped in a <span class="emoji"> so it can be enlarged
  // inline, while the surrounding words stay at the normal size. Every run is HTML-escaped, so the
  // result is safe to inject as innerHTML.
  _richText(text) {
    const t = text || "";
    const re = new RegExp(EMOJI_SEQ_SRC, "gu");
    let out = "";
    let last = 0;
    let m;
    while ((m = re.exec(t)) !== null) {
      if (m.index > last) out += this._esc(t.slice(last, m.index));
      out += `<span class="emoji">${this._esc(m[0])}</span>`;
      last = re.lastIndex;
      if (re.lastIndex === m.index) re.lastIndex++; // belt-and-suspenders against a zero-length match
    }
    if (last < t.length) out += this._esc(t.slice(last));
    return out;
  }

  _senderName(msg) {
    const d = (msg && msg.data) || {};
    const s = (msg && msg.sender) || {};
    return d.sender_name || s.name || "";
  }

  _fmtTime(ms) {
    if (!ms) return "";
    const d = new Date(ms);
    return isNaN(d.getTime()) ? "" : d.toLocaleString(this._locale());
  }

  _title() {
    if (this._config.title) return this._config.title;
    const s = this._stateObj();
    if (s && s.attributes && s.attributes.friendly_name) return s.attributes.friendly_name;
    return "Messages";
  }

  _notify(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  /* -------------------------------------------------------------- services */

  // Fetch messages once when the card first opens with nothing cached, so an empty card fills in
  // without the user having to hit refresh. `read_message` also downloads any media attachments.
  _maybeAutoFetch() {
    if (this._autoFetched) return;
    const a = this._attrs();
    if (!a.wuid || !a.entry_id) return; // sensor not ready yet
    this._autoFetched = true;
    // Always fill an empty card on first open. Additionally, when the user enabled "refresh on
    // render", re-pull the thread on show even if something is cached -- deduped so a duplicate
    // chat card (or another card hitting read_message) in the same view doesn't repeat the call.
    if (this._messages().length === 0) {
      this._refresh();
    } else if (refreshOnRenderEnabled(this._stateObj())) {
      trackInflight(`${CHAT_SERVICE.READ}|${a.entry_id}|${a.wuid}`, () => this._refresh());
    }
  }

  async _refresh() {
    if (!this._hass || this._refreshing) return;
    const a = this._attrs();
    if (!a.wuid || !a.entry_id) return;
    this._refreshing = true;
    this._syncControls();
    // Watchdog: a `callService` promise that never settles would otherwise leave `_refreshing`
    // stuck `true` -- the button disabled and every later click dropped by the guard above. Clear
    // the flag after a timeout so the user can retry even if the call is wedged.
    let settled = false;
    const watchdog = setTimeout(() => {
      if (settled) return;
      this._refreshing = false;
      this._syncControls();
    }, REFRESH_WATCHDOG_MS);
    const startedAt = Date.now();
    try {
      await this._hass.callService(DOMAIN, CHAT_SERVICE.READ, this._base(), undefined, false);
    } catch (err) {
      this._notify(`Xplora watch: ${err && err.message ? err.message : "could not read messages"}`);
    } finally {
      settled = true;
      clearTimeout(watchdog);
      // Hold the spinner for a perceptible minimum so an instant (cached) read still shows feedback.
      const remaining = MIN_REFRESH_SPIN_MS - (Date.now() - startedAt);
      if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining));
      this._refreshing = false;
      this._syncControls();
    }
  }

  async _send() {
    if (!this._hass || this._busy) return;
    const input = this._composer && this._composer.querySelector(".msg-input");
    const text = input ? input.value.trim() : "";
    if (!text) return;
    const a = this._attrs();
    if (!a.wuid || !a.entry_id) {
      this._notify("Xplora watch: message sensor not ready yet.");
      return;
    }
    this._busy = true;
    this._syncControls();
    try {
      await this._hass.callService(DOMAIN, CHAT_SERVICE.SEND, { ...this._base(), message: text }, undefined, false);
      if (input) input.value = "";
      // Optimistically show the sent message immediately. The follow-up refresh below usually runs
      // before the Xplora backend has indexed the message, so a fetched list wouldn't contain it
      // yet -- without this the message would vanish until a much later poll. The reconcile in
      // `_messages()` drops this placeholder once the real one comes back.
      this._pending.push({
        msgId: `local-${++this._localSeq}`,
        type: "TEXT",
        sender: { id: a.account_user_id },
        data: { text },
        create: Date.now(),
      });
      this._busy = false;
      this._syncControls();
      // `_sig` is derived from the sensor state, so a local-only change needs an explicit render.
      // Force the scroll-to-bottom so the user always sees their just-sent message.
      this._forceScroll = true;
      this._render();
      // `send_message` doesn't itself refresh the sensor; pull the thread so the sent message shows.
      this._refresh();
    } catch (err) {
      this._busy = false;
      this._notify(`Xplora watch: ${err && err.message ? err.message : "could not send message"}`);
      this._syncControls();
    }
  }

  /* ---------------------------------------------------------------- render */

  _render() {
    if (!this._built) this._buildShell();
    const dark = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    this.style.setProperty("--xplora-color-scheme", dark ? "dark" : "light");

    this._titleEl.textContent = this._title();

    const s = this._stateObj();
    if (!s) {
      this._listEl.innerHTML = `<div class="placeholder">
        <ha-icon icon="mdi:message-text-outline"></ha-icon>
        ${entityStatusHtml(this._hass, this._config.entity, (v) => this._esc(v))}
      </div>`;
    } else {
      const msgs = this._messages();
      // Debug: dump what the sensor actually exposes and how each message is classified, so a
      // not-rendering emoji/attachment can be diagnosed from the browser console.
      if (this._config.debug) {
        const a = this._attrs();
        this._debug("attributes:", { wuid: a.wuid, entry_id: a.entry_id, account_user_id: a.account_user_id, count: msgs.length });
        msgs.forEach((m, i) => {
          this._debug(`msg[${i}]`, {
            type: m.type,
            msgId: m.msgId,
            sender: m.sender,
            data: m.data,
            emojiCode: this._emojiCode(m),
            emojiGlyph: this._emojiGlyph(m),
            incoming: this._incoming(m),
          });
        });
      }
      this._renderMessages(msgs);
    }
    this._syncControls();
  }

  // Incrementally reconcile the rendered bubbles against `msgs` (keyed by msgId / a stable
  // fallback) instead of rebuilding innerHTML. Existing nodes -- and their loaded media, in-flight
  // video playback, and the user's scroll position -- survive a chat update; only genuinely new
  // messages get new DOM, and removed ones (reconciled optimistic sends, deletions) are pruned.
  _renderMessages(msgs) {
    const el = this._listEl;
    if (!msgs.length) {
      el.innerHTML = `<div class="placeholder">
           <ha-icon icon="mdi:message-text-outline"></ha-icon>
           <div class="empty-title">No messages yet</div>
           <div class="empty-sub">Say hello below.</div>
         </div>`;
      return;
    }
    // Was the user pinned to the bottom before we touched the DOM? (Decides auto-scroll below.)
    const pinned = this._forceScroll || el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_PIN_THRESHOLD_PX;
    this._forceScroll = false;

    // Leaving the empty-state: drop the placeholder before inserting bubbles.
    const ph = el.querySelector(":scope > .placeholder");
    if (ph) ph.remove();

    // Index the bubbles already in the DOM by key, then walk the desired order: reuse a matching
    // node (moving it into place if needed) or create a new one. Leftovers are removed at the end.
    // Bubbles whose media failed to load are skipped (left out of the reuse map) so they're rebuilt
    // fresh -- this retries the media, recovering once a previously-missing file gets downloaded.
    const existing = new Map();
    el.querySelectorAll(":scope > [data-key]").forEach((n) => {
      if (n.querySelector(".media-wrap.broken")) return;
      existing.set(n.getAttribute("data-key"), n);
    });

    let appended = false;
    let cursor = null; // the last node placed; the next desired node belongs right after it
    for (const msg of msgs) {
      const key = this._msgKey(msg);
      let node = existing.get(key);
      if (node) {
        existing.delete(key);
      } else {
        node = this._bubbleNode(msg);
        appended = true;
      }
      const want = cursor ? cursor.nextSibling : el.firstChild;
      if (node !== want) el.insertBefore(node, want);
      cursor = node;
    }
    existing.forEach((n) => n.remove());

    if (pinned && appended) this._scrollToBottom();
  }

  // A stable per-message key for DOM reconciliation. `msgId` is unique per chat; fall back to
  // time+text for any malformed entry without one (still stable across renders for that message).
  _msgKey(msg) {
    const id = msg && msg.msgId;
    return id ? String(id) : `k:${this._stamp(msg)}:${this._text(msg)}`;
  }

  // Build a detached bubble DOM node from the bubble HTML, wiring media load-failure handling: a
  // broken image/audio/video flags its wrap so the CSS swaps in the "unavailable" fallback (and the
  // next render rebuilds it to retry -- see `_renderMessages`).
  _bubbleNode(msg) {
    const tpl = document.createElement("template");
    tpl.innerHTML = this._bubble(msg).trim();
    const node = tpl.content.firstElementChild;
    node.querySelectorAll(".media-img, .media-audio, .media-video").forEach((m) => {
      m.addEventListener(
        "error",
        () => {
          const wrap = m.closest(".media-wrap");
          if (wrap) wrap.classList.add("broken");
        },
        { once: true },
      );
    });
    node.querySelectorAll(".video-play-btn").forEach((btn) => {
      btn.addEventListener(
        "click",
        () => {
          const wrap = btn.closest(".media-wrap");
          if (!wrap) return;
          const poster = wrap.querySelector(".video-poster");
          const video = wrap.querySelector(".media-video");
          if (poster) poster.hidden = true;
          if (video) video.play().catch(() => {});
        },
        { once: true },
      );
    });
    return node;
  }

  // Pin the list to the newest message. Media (images/videos) load asynchronously and grow the
  // list after the initial scroll, which would otherwise strand the view mid-list -- so re-pin to
  // the bottom as each media element finishes (or errors).
  _scrollToBottom() {
    if (!this._listEl) return;
    const el = this._listEl;
    const jump = () => (el.scrollTop = el.scrollHeight);
    requestAnimationFrame(jump);
    el.querySelectorAll("img, video").forEach((m) => {
      // Skip media that already has its dimensions (img -> `complete`; video -> metadata loaded),
      // so reconciled (reused) nodes don't accumulate dead one-shot listeners on every update.
      if (m.complete || (m.tagName === "VIDEO" && m.readyState >= 1)) return;
      const onReady = () => requestAnimationFrame(jump);
      m.addEventListener("load", onReady, { once: true });
      m.addEventListener("loadedmetadata", onReady, { once: true });
      m.addEventListener("error", onReady, { once: true });
    });
  }

  _buildShell() {
    const style = document.createElement("style");
    style.textContent = this._css();
    this.shadowRoot.appendChild(style);

    this._card = document.createElement("ha-card");
    this.shadowRoot.appendChild(this._card);

    // Header (title + refresh button).
    const header = document.createElement("div");
    header.className = "header";
    this._titleEl = document.createElement("span");
    this._titleEl.className = "title";
    const refresh = document.createElement("ha-icon-button");
    refresh.className = "refresh-btn";
    refresh.setAttribute("label", "Refresh");
    refresh.innerHTML = `<ha-icon icon="mdi:refresh"></ha-icon>`;
    refresh.addEventListener("click", () => this._refresh());
    this._refreshBtn = refresh;
    const fsBtn = document.createElement("ha-icon-button");
    fsBtn.className = "fs-btn";
    fsBtn.setAttribute("label", "Full screen");
    fsBtn.innerHTML = `<ha-icon icon="mdi:fullscreen"></ha-icon>`;
    fsBtn.addEventListener("click", () => this._toggleFullscreen());
    this._fsBtn = fsBtn;
    const titleWrap = document.createElement("div");
    titleWrap.className = "title-wrap";
    const titleIcon = document.createElement("ha-icon");
    titleIcon.className = "title-icon";
    titleIcon.setAttribute("icon", "mdi:message-text");
    titleWrap.appendChild(titleIcon);
    titleWrap.appendChild(this._titleEl);
    const actions = document.createElement("div");
    actions.className = "header-actions";
    actions.appendChild(refresh);
    actions.appendChild(fsBtn);
    header.appendChild(titleWrap);
    header.appendChild(actions);
    this._card.appendChild(header);

    // Message list (scrolling).
    this._listEl = document.createElement("div");
    this._listEl.className = "messages";
    // Delegated: clicking an image opens it in the in-card lightbox instead of a new browser tab.
    // (The anchor keeps its href so right-click "open/save image" still works as a fallback.)
    this._listEl.addEventListener("click", (ev) => {
      const link = ev.target.closest && ev.target.closest("a.media-link");
      if (link) {
        ev.preventDefault();
        this._openLightbox(link.getAttribute("href"));
      }
    });
    this._card.appendChild(this._listEl);

    // Composer (built once so typing survives background re-renders).
    this._composer = document.createElement("div");
    this._composer.className = "composer";
    this._composer.innerHTML = `
      <textarea class="msg-input" rows="1" placeholder="Type a message…" autocomplete="off"></textarea>
      <ha-icon-button class="send-btn" label="Send"><ha-icon icon="mdi:send"></ha-icon></ha-icon-button>`;
    const input = this._composer.querySelector(".msg-input");
    // Enter sends; Shift+Enter inserts a newline. Auto-grow up to a few rows.
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        this._send();
      }
    });
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 120) + "px";
      this._syncControls();
    });
    this._composer.querySelector(".send-btn").addEventListener("click", () => this._send());
    this._card.appendChild(this._composer);

    this._built = true;
  }

  /* ------------------------------------------------------------ lightbox */

  // Show a tapped image full-screen over the card. Built once and reused; close via the X button,
  // a click on the backdrop, or Escape. Lives in the shadow root so it inherits the card's CSS and
  // sits above native/CSS fullscreen (z-index over the .fullscreen overlay).
  _openLightbox(url) {
    if (!url) return;
    if (!this._lightbox) {
      this._lightbox = document.createElement("div");
      this._lightbox.className = "lightbox";
      this._lightbox.innerHTML = `
        <ha-icon-button class="lightbox-close" label="Close"><ha-icon icon="mdi:close"></ha-icon></ha-icon-button>
        <img class="lightbox-img" alt="Image" />`;
      // Close on backdrop click or the X; clicks on the image itself do nothing.
      this._lightbox.addEventListener("click", (ev) => {
        if (ev.target === this._lightbox || (ev.target.closest && ev.target.closest(".lightbox-close"))) this._closeLightbox();
      });
      this.shadowRoot.appendChild(this._lightbox);
    }
    this._lightbox.querySelector(".lightbox-img").src = url;
    this._lightbox.classList.add("open");
    window.addEventListener("keydown", this._onLightboxKey);
  }

  _closeLightbox() {
    if (!this._lightbox) return;
    this._lightbox.classList.remove("open");
    const img = this._lightbox.querySelector(".lightbox-img");
    if (img) img.src = ""; // free the decoded image while hidden
    window.removeEventListener("keydown", this._onLightboxKey);
  }

  /* ------------------------------------------------------------ full screen */

  // Whether the card is currently expanded. `_native` is set when our own requestFullscreen()
  // promise resolves (the only reliable per-element signal in a shadow tree); `_cssExpanded` is the
  // maximize fallback.
  _isExpanded() {
    return this._native || this._cssExpanded;
  }

  _toggleFullscreen() {
    if (this._isExpanded()) {
      // Exit whichever mode is active.
      if (this._native && document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
      this._native = false;
      this._cssExpanded = false;
      this._syncFullscreen();
      return;
    }
    // Prefer the native Fullscreen API (hides the browser chrome); fall back to a CSS maximize
    // overlay where it isn't available or is blocked (e.g. some embedded contexts). A resolved
    // requestFullscreen() promise is authoritative for "we are now fullscreen".
    if (this.requestFullscreen) {
      this.requestFullscreen()
        .then(() => {
          this._native = true;
          this._syncFullscreen();
        })
        .catch(() => {
          this._cssExpanded = true;
          this._syncFullscreen();
        });
    } else {
      this._cssExpanded = true;
      this._syncFullscreen();
    }
  }

  // Reconcile the class + button with the actual expanded state. Idempotent and safe to call from
  // both the click handler and the `fullscreenchange` event.
  _syncFullscreen() {
    const on = this._isExpanded();
    this.classList.toggle("fullscreen", on);
    if (this._fsBtn) {
      const icon = this._fsBtn.querySelector("ha-icon");
      if (icon) icon.setAttribute("icon", on ? "mdi:fullscreen-exit" : "mdi:fullscreen");
      this._fsBtn.setAttribute("label", on ? "Exit full screen" : "Full screen");
    }
    // Keep the newest message in view after the layout change.
    if (on) this._scrollToBottom();
  }

  // Reflect busy/refresh state on the controls without rebuilding them (keeps input focus).
  _syncControls() {
    if (this._refreshBtn) {
      this._refreshBtn.disabled = this._refreshing;
      const icon = this._refreshBtn.querySelector("ha-icon");
      if (icon) icon.classList.toggle("spin", this._refreshing);
    }
    if (this._composer) {
      const input = this._composer.querySelector(".msg-input");
      const send = this._composer.querySelector(".send-btn");
      if (send) send.disabled = this._busy || !input || !input.value.trim();
    }
  }

  _media(msg) {
    const id = msg && msg.msgId;
    if (!id) return "";
    const eid = this._esc(id);
    switch (msg.type) {
      case "IMAGE": {
        const url = `${MEDIA.image}/${eid}.jpeg`;
        return `<div class="media-wrap">
                  <a class="media-link" href="${url}" target="_blank" rel="noopener">
                    <img class="media-img" src="${url}" alt="Image" loading="lazy" />
                  </a>
                  ${this._downloadBtn(url, `${eid}.jpeg`)}
                  ${this._mediaFallback("IMAGE")}
                </div>`;
      }
      case "VOICE": {
        const url = `${MEDIA.voice}/${eid}.mp3`;
        return `<div class="media-wrap media-wrap-audio">
                  <audio class="media-audio" controls preload="metadata" src="${url}"></audio>
                  ${this._downloadBtn(url, `${eid}.mp3`)}
                  ${this._mediaFallback("VOICE")}
                </div>`;
      }
      case "SHORT_VIDEO": {
        const url = `${MEDIA.video}/${eid}.mp4`;
        const thumb = `${MEDIA.video}/thumb/${eid}.jpeg`;
        return `<div class="media-wrap">
                  <video class="media-video" preload="none" controls>
                    <source src="${url}" type="video/mp4" />
                  </video>
                  <div class="video-poster" style="background-image:url('${thumb}')">
                    <button class="video-play-btn" type="button" aria-label="Play video">
                      <ha-icon icon="mdi:play-circle-outline"></ha-icon>
                    </button>
                  </div>
                  ${this._downloadBtn(url, `${eid}.mp4`)}
                  ${this._mediaFallback("SHORT_VIDEO")}
                </div>`;
      }
      default:
        return "";
    }
  }

  // Hidden-by-default placeholder shown (via the `.media-wrap.broken` class) when a media file can't
  // be loaded -- e.g. it hasn't been downloaded yet -- so a failed attachment degrades to a labelled
  // chip instead of a broken element with a stray, mis-positioned download button.
  _mediaFallback(type) {
    return `<div class="media-broken"><ha-icon icon="${this._typeIcon(type)}"></ha-icon>${this._esc(this._typeName(type))} unavailable</div>`;
  }

  // A small overlay/inline download link. Media lives under /local/... (same-origin), so the
  // browser's `download` attribute saves the file directly instead of navigating to it.
  _downloadBtn(url, filename) {
    return `<a class="media-download" href="${url}" download="${this._esc(filename)}" title="Download" aria-label="Download">
              <ha-icon icon="mdi:download"></ha-icon>
            </a>`;
  }

  // The numeric emoticon code for an EMOTICON message (e.g. "1009"), or "" if it isn't one. The
  // code can live in EITHER `data.emoji_id` or `data.emoticon_id`, and the fields are not
  // consistent: in practice `emoji_id` carries the number ("1009") while `emoticon_id` carries the
  // actual glyph ("😜") -- but older/other payloads use "M1009" or the bare number in either. So
  // just take the digits from whichever field has them.
  _emojiCode(msg) {
    const d = (msg && msg.data) || {};
    for (const v of [d.emoji_id, d.emoticon_id]) {
      const s = String(v == null ? "" : v);
      if (!s || s === "UNKNOWN__") continue;
      const m = s.match(/(\d+)/); // "1009" / "M1009" -> "1009"
      if (m) return m[1];
    }
    return "";
  }

  // The Unicode glyph for an EMOTICON message, or "" if it isn't one. `emoticon_id` is usually the
  // actual glyph already (e.g. "😜"); otherwise map the numeric code via EMOJI_MAP. Rendering the
  // glyph directly (rather than the bundled PNG art) keeps emojis working without depending on the
  // `emojis` folder being served under /local -- which isn't guaranteed.
  _emojiGlyph(msg) {
    const raw = String(((msg && msg.data) || {}).emoticon_id || "");
    if (raw && raw !== "UNKNOWN__" && !/^M?\d+$/.test(raw)) return raw; // already a glyph
    const code = this._emojiCode(msg);
    return code && EMOJI_MAP["M" + code] ? EMOJI_MAP["M" + code] : "";
  }

  _bubble(msg) {
    const incoming = this._incoming(msg);
    const side = incoming ? "in" : "out";
    const time = this._fmtTime(this._stamp(msg));
    const name = incoming ? this._senderName(msg) : "";
    const text = this._text(msg);
    const media = this._media(msg);
    const isEmoji = msg.type === "EMOTICON" || !!this._emojiCode(msg);
    const glyph = isEmoji ? this._emojiGlyph(msg) : "";

    // What to show when there's no plain text: the emoji glyph (large), otherwise a small typed
    // label for anything we can't render inline yet (media not downloaded, or an emoji with no
    // known glyph) so a message never shows up empty.
    let extra = "";
    if (glyph) {
      extra = `<div class="bubble-emoji">${this._esc(glyph)}</div>`;
    } else if (!media && msg.type && msg.type !== "TEXT") {
      extra = `<div class="media-pending"><ha-icon icon="${this._typeIcon(msg.type)}"></ha-icon>${this._esc(this._typeName(msg.type))}</div>`;
    }

    // Enlarge an emoji-only text bubble so messages we send (their emoji live inside the text)
    // match the size of the watch's EMOTICON glyphs.
    const emojiOnly = !glyph && this._isEmojiOnly(text);

    return `
      <div class="bubble-row ${side}" data-key="${this._esc(this._msgKey(msg))}">
        <div class="bubble ${side}">
          ${name ? `<div class="bubble-name">${this._esc(name)}</div>` : ""}
          ${media}
          ${extra}
          ${text ? `<div class="bubble-text${emojiOnly ? " emoji-only" : ""}">${this._richText(text)}</div>` : ""}
          ${time ? `<div class="bubble-time">${this._esc(time)}</div>` : ""}
        </div>
      </div>`;
  }

  _typeIcon(type) {
    if (type === "VOICE") return "mdi:microphone";
    if (type === "IMAGE") return "mdi:image";
    if (type === "SHORT_VIDEO") return "mdi:video";
    if (type === "EMOTICON") return "mdi:emoticon-happy-outline";
    return "mdi:paperclip";
  }

  _typeName(type) {
    if (type === "VOICE") return "Voice message";
    if (type === "IMAGE") return "Image";
    if (type === "SHORT_VIDEO") return "Video";
    if (type === "EMOTICON") return "Emoji";
    return "Attachment";
  }

  _esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _css() {
    return `
      :host { display: block; }
      ha-card { overflow: hidden; display: flex; flex-direction: column; color: var(--primary-text-color); }

      .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 12px 8px 8px 16px; flex: 0 0 auto; }
      .title-wrap { display: flex; align-items: center; gap: 10px; min-width: 0; }
      .title { font-size: 1.25rem; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .title-icon { color: var(--primary-color); --mdc-icon-size: 22px; flex: 0 0 auto; }
      .header-actions { display: flex; align-items: center; gap: 2px; flex: 0 0 auto; }
      .refresh-btn, .fs-btn { color: var(--secondary-text-color); }
      @keyframes xplora-spin { to { transform: rotate(360deg); } }
      .refresh-btn ha-icon.spin { animation: xplora-spin 0.9s linear infinite; }

      /* ---- full screen / maximized ---- */
      :host(.fullscreen) {
        position: fixed; inset: 0; z-index: 9999;
        background: var(--card-background-color, var(--ha-card-background, #fff));
      }
      :host(.fullscreen) ha-card { height: 100%; max-height: none; border-radius: 0; box-shadow: none; }
      :host(.fullscreen) .messages { max-height: none; }

      /* ---- message list ---- */
      .messages {
        flex: 1 1 auto; min-height: 220px; max-height: 60vh; overflow-y: auto;
        display: flex; flex-direction: column; gap: 8px;
        padding: 8px 12px; border-top: 1px solid var(--divider-color);
      }
      .bubble-row { display: flex; }
      .bubble-row.in { justify-content: flex-start; }
      .bubble-row.out { justify-content: flex-end; }
      .bubble {
        max-width: 78%; padding: 8px 12px; border-radius: 16px;
        font-size: 0.95rem; line-height: 1.35; word-wrap: break-word; overflow-wrap: anywhere;
      }
      /* Incoming (from the watch): a soft, theme-aware tint of the brand colour, left-aligned with
         a tucked bottom-left corner. The plain --secondary-background-color is the fallback for
         themes/browsers without color-mix. */
      .bubble.in {
        background: var(--secondary-background-color);
        background: color-mix(in srgb, var(--primary-color) 14%, var(--card-background-color, var(--ha-card-background, #fff)));
        color: var(--primary-text-color);
        border-bottom-left-radius: 4px;
      }
      /* Outgoing (sent from Home Assistant): solid brand colour, right-aligned. */
      .bubble.out {
        background: var(--primary-color);
        color: var(--text-primary-color, #fff);
        border-bottom-right-radius: 4px;
      }
      .bubble-name { font-size: 0.78rem; font-weight: 600; opacity: 0.85; margin-bottom: 2px; }
      .bubble-text { white-space: pre-wrap; }
      .bubble-time { font-size: 0.7rem; opacity: 0.7; margin-top: 4px; text-align: right; }

      /* Media is sized to min(360px, 90vw) and capped to the bubble with max-width:100%; height
         follows the width (height:auto preserves the aspect ratio). NOTE: the width MUST be
         viewport-based, not a percentage. The bubble is shrink-to-fit (width:auto, max-width:78%),
         so it sizes to its content -- but a <video> reports 0x0 until its metadata loads, giving
         the bubble nothing to size to, so a percentage width can't resolve and the whole chain
         collapses (images survive because they expose intrinsic size). A vw width is always
         definite, so the wrap gets a real width regardless of when metadata arrives; max-width:100%
         then keeps it within the bubble on narrow layouts. */
      .media-link { display: block; width: 100%; }
      .media-wrap { position: relative; display: inline-block; }
      .media-wrap:not(.media-wrap-audio) { width: min(360px, 90vw); max-width: 100%; }
      .media-img, .media-video { width: 100%; height: auto; border-radius: 10px; display: block; }
      /* Xplora watch videos are always 480x480 (square). Fixed 1:1 ratio reserves the box before
         metadata loads; object-fit:contain handles any edge case without layout-shift. */
      .media-video { aspect-ratio: 1; object-fit: contain; background: #000; }
      .video-poster {
        position: absolute; inset: 0; border-radius: 10px;
        background: #111 center/cover no-repeat; overflow: hidden;
      }
      .video-play-btn {
        position: absolute; inset: 0; width: 100%; height: 100%; border: none; padding: 0;
        background: rgba(0,0,0,.3); display: flex; align-items: center; justify-content: center;
        cursor: pointer;
      }
      .video-play-btn ha-icon { --mdc-icon-size: 56px; color: rgba(255,255,255,.9); }
      .media-wrap-audio { display: flex; align-items: center; gap: 8px; width: min(360px, 90vw); max-width: 100%; }
      .media-audio { flex: 1 1 auto; min-width: 0; margin: 2px 0; }
      .media-download {
        display: inline-flex; align-items: center; justify-content: center; flex: none;
        width: 30px; height: 30px; border-radius: 50%; text-decoration: none;
        background: rgba(0, 0, 0, 0.55); color: #fff;
      }
      .media-wrap:not(.media-wrap-audio) .media-download { position: absolute; top: 6px; right: 6px; }
      .media-download ha-icon { --mdc-icon-size: 18px; }

      /* Failed media: hide the broken element + download button and show the labelled fallback. */
      .media-broken { display: none; align-items: center; gap: 6px; font-size: 0.82rem; opacity: 0.85; }
      .media-broken ha-icon { --mdc-icon-size: 18px; }
      .media-wrap.broken { width: auto; max-width: none; }
      .media-wrap.broken .media-link,
      .media-wrap.broken .media-img,
      .media-wrap.broken .video-poster,
      .media-wrap.broken .media-video,
      .media-wrap.broken .media-audio,
      .media-wrap.broken .media-download { display: none; }
      .media-wrap.broken .media-broken { display: inline-flex; }

      /* Tapped-image lightbox: full-viewport backdrop with the image centered. z-index sits above
         the .fullscreen card overlay (9999) so it works even when the card is expanded. */
      .lightbox {
        display: none; position: fixed; inset: 0; z-index: 10000;
        align-items: center; justify-content: center; box-sizing: border-box; padding: 24px;
        background: rgba(0, 0, 0, 0.85);
      }
      .lightbox.open { display: flex; }
      .lightbox-img {
        max-width: 96vw; max-height: 92vh; object-fit: contain;
        border-radius: 8px; box-shadow: 0 6px 30px rgba(0, 0, 0, 0.5);
      }
      .lightbox-close { position: fixed; top: 12px; right: 12px; color: #fff; }
      .bubble-emoji { font-size: 2.6rem; line-height: 1.15; }
      /* Emoji inside mixed text render a bit larger than the words around them. */
      .bubble-text .emoji { font-size: 1.45em; line-height: 1; }
      /* Emoji-only text bubbles (e.g. emoji we sent) get the same large glyph as EMOTICON messages;
         the inner spans inherit that size rather than scaling on top of it. */
      .bubble-text.emoji-only { font-size: 2.6rem; line-height: 1.2; }
      .bubble-text.emoji-only .emoji { font-size: 1em; }
      .media-pending { display: inline-flex; align-items: center; gap: 4px; font-size: 0.82rem; opacity: 0.85; }
      .media-pending ha-icon { --mdc-icon-size: 18px; }

      /* ---- placeholder / empty ---- */
      .placeholder { margin: auto; display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 24px 20px; color: var(--secondary-text-color); }
      .placeholder ha-icon { --mdc-icon-size: 44px; color: var(--disabled-text-color); }
      .placeholder code { background: var(--secondary-background-color); padding: 2px 6px; border-radius: 6px; font-size: 0.85rem; }
      .empty-title { font-size: 1.05rem; font-weight: 500; color: var(--primary-text-color); }

      /* ---- composer ---- */
      .composer { display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--divider-color); flex: 0 0 auto; }
      .msg-input {
        flex: 1; resize: none; font: inherit; font-size: 0.95rem; line-height: 1.35;
        color: var(--primary-text-color); background: var(--secondary-background-color);
        border: 1px solid var(--divider-color); border-radius: 18px;
        padding: 10px 14px; min-height: 22px; max-height: 120px; box-sizing: border-box;
        color-scheme: var(--xplora-color-scheme, light dark);
      }
      .msg-input:focus { outline: none; border-color: var(--primary-color); }
      .send-btn { color: var(--primary-color); flex: 0 0 auto; }
      .send-btn[disabled] { color: var(--disabled-text-color); }
    `;
  }
}

if (!customElements.get("xplora-watch-chat-card")) {
  customElements.define("xplora-watch-chat-card", XploraWatchChatCard);
}

window.customCards.push({
  type: "xplora-watch-chat-card",
  name: "Xplora Watch Chat",
  description: "View a watch's chat history and send a message (text, with voice/image/video attachments shown).",
  preview: true,
});

console.info(
  "%c XPLORA-WATCH-CARD %c loaded ",
  "background:#03a9f4;color:#fff;border-radius:3px 0 0 3px;padding:1px 4px",
  "background:#555;color:#fff;border-radius:0 3px 3px 0;padding:1px 4px"
);
