"""Const for Xplora® Watch Version 2."""

from __future__ import annotations

from typing import Final

from homeassistant.const import CONF_EMAIL

DOMAIN: Final = "xplora_watch"
MANUFACTURER: Final = "Xplora®"
DEVICE_NAME: Final = "Xplora® Watch"
ATTRIBUTION: Final = "Data provided by Xplora®"

# Dev/screenshot-only escape hatch (see demo.py): an entry whose sign-in email is one of these
# sentinels gets `DemoPyXploraApi` -- a network-free stand-in seeded with synthetic watch data --
# instead of a real `PyXploraApi`. The `.invalid` TLD (RFC 6761) can never be a real Xplora account,
# so a real user would never type it and a real entry can never carry it: matching on the email alone
# is a safe, self-sufficient switch that needs no environment variable and never touches a real entry.
# The sentinels seed different accounts so the multi-account service fan-out (ADR 0004) can be
# exercised in the live UI: a primary Guardian, a second Guardian (a different child), a Contact, and
# a Guardian whose watch is offline (control actions are refused -> the `watch_offline` surfacing).
DEMO_ACCOUNT_EMAIL: Final = "demo@xplora-watch.invalid"
DEMO_SECOND_PARENT_ACCOUNT_EMAIL: Final = "demo-second-parent@xplora-watch.invalid"
DEMO_CONTACT_ACCOUNT_EMAIL: Final = "demo-contact@xplora-watch.invalid"
DEMO_OFFLINE_ACCOUNT_EMAIL: Final = "demo-offline@xplora-watch.invalid"

URL_OPENSTREETMAP = "https://nominatim.openstreetmap.org/reverse?lat={}&lon={}&format=jsonv2&accept-language={}"
URL_MAPBOX = "https://api.mapbox.com/geocoding/v5/mapbox.places/{},{}.json?types=address&limit=1&access_token={}&language={}"
API_KEY_MAPBOX: Final = "pk.eyJ1IjoieHBsb3JhdGVjaG5vbG9naWVzIiwiYSI6ImNrenpoYnFodzBhZTUzbG83aTFrNG91aXoifQ.ih4DP1EH9xSrQnzr7QaDvw"

ATTR_SERVICE_SEE: Final = "see"
ATTR_SERVICE_REFRESH_FUNCTIONS: Final = "refresh_functions"
# Fetch + cache one past day's location track (default: yesterday). Meant to be automated daily so HA
# keeps an archive beyond the few days the watch's API still serves. `ATTR_SERVICE_DATE` overrides
# the day (YYYY-MM-DD).
ATTR_SERVICE_FETCH_HISTORY: Final = "fetch_history"
ATTR_SERVICE_DATE: Final = "date"
ATTR_SERVICE_DELETE_MSG: Final = "delete_message_from_app"
ATTR_SERVICE_READ_MSG: Final = "read_message"
ATTR_SERVICE_SEND_MSG: Final = "send_message"
ATTR_SERVICE_SHUTDOWN: Final = "shutdown"
ATTR_SERVICE_REBOOT: Final = "reboot"
ATTR_SERVICE_LOGOUT: Final = "logout"
ATTR_SERVICE_MSG: Final = "message"
ATTR_SERVICE_MSGID: Final = "message_id"
# `user` is still surfaced as a per-watch state attribute (the account display name); it is no longer
# a service selector -- services target HA devices (ADR 0003).
ATTR_SERVICE_USER: Final = "user"

# Alarm / silent-time CRUD services and their field names.
ATTR_SERVICE_CREATE_ALARM: Final = "create_alarm"
ATTR_SERVICE_UPDATE_ALARM: Final = "update_alarm"
ATTR_SERVICE_DELETE_ALARM: Final = "delete_alarm"
ATTR_SERVICE_SET_ALARM_ENABLED: Final = "set_alarm_enabled"
ATTR_SERVICE_CREATE_SILENT: Final = "create_silent"
ATTR_SERVICE_UPDATE_SILENT: Final = "update_silent"
ATTR_SERVICE_DELETE_SILENT: Final = "delete_silent"
ATTR_SERVICE_SET_SILENT_ENABLED: Final = "set_silent_enabled"
# Bulk enable/disable: toggle every alarm / silent-time window on the target watch(es) in one call.
ATTR_SERVICE_TURN_ALL_ALARMS_ON: Final = "turn_all_alarms_on"
ATTR_SERVICE_TURN_ALL_ALARMS_OFF: Final = "turn_all_alarms_off"
ATTR_SERVICE_TURN_ALL_SILENTS_ON: Final = "turn_all_silents_on"
ATTR_SERVICE_TURN_ALL_SILENTS_OFF: Final = "turn_all_silents_off"
ATTR_SERVICE_ALARM_ID: Final = "alarm_id"
ATTR_SERVICE_SILENT_ID: Final = "silent_id"
ATTR_SERVICE_START: Final = "start"
ATTR_SERVICE_END: Final = "end"
ATTR_SERVICE_WEEKDAYS: Final = "weekdays"
ATTR_SERVICE_NAME: Final = "name"
ATTR_SERVICE_ENABLED: Final = "enabled"

ATTR_TRACKER_ADDR: Final = "address"
ATTR_TRACKER_DISTOHOME: Final = "Home Distance (m)"
ATTR_TRACKER_IMEI: Final = "imei"
ATTR_TRACKER_LAST_TRACK: Final = "last tracking"
ATTR_TRACKER_LAT: Final = "lat"
ATTR_TRACKER_LICENCE: Final = "licence"
ATTR_TRACKER_LNG: Final = "lng"
ATTR_TRACKER_POI: Final = "poi"
ATTR_TRACKER_RAD: Final = "rad"

ATTR_WATCH: Final = "watch"

# Fresh-fix poll cadence (seconds) (ref:XW-001): after `askWatchLocate` tells the watch to
# take a new fix, poll `WatchLastLocate` at ~1s then ~5s, stopping as soon as `Location.tm`
# advances. (A continuous 1s-for-25s loop is used for a separate "follow"/live-tracking
# flow -- intentionally NOT replicated here.)
LOCATE_POLL_DELAYS: Final = (1, 5)

CONF_HOME_SAFEZONE: Final = "home_is_safezone"
CONF_HOME_LATITUDE: Final = "home_latitude"
CONF_HOME_LONGITUDE: Final = "home_longitude"
CONF_HOME_RADIUS: Final = "home_radius"
CONF_AUTO_MARK_READ: Final = "auto_mark_read"
CONF_MAPS: Final = "maps"
CONF_MESSAGE: Final = "message"
CONF_OPENCAGE_APIKEY: Final = "opencage_apikey"
CONF_PHONENUMBER: Final = "phonenumber"
CONF_REMOVE_MESSAGE: Final = "remove_message"
# Separate, optional poll cadence for the slow-changing "functions" data (alarms, silent
# times, safe-zone definitions). These need their own per-watch queries (`deviceList` cannot
# carry them) but rarely change, so they default to OFF and are refreshed on demand instead.
CONF_SCAN_INTERVAL_FUNCTIONS: Final = "scan_interval_functions"
# When enabled, the custom Lovelace cards trigger an on-demand refresh of the data they show as
# soon as they are rendered (alarms/silent times via `refresh_functions`, location via the watch
# `update`/`see` flow, chat via `read_message`). OFF by default to stay off the rate-limit radar;
# the cards de-duplicate so several cards in one view only refresh each watch's data set once.
CONF_REFRESH_ON_CARD_RENDER: Final = "refresh_on_card_render"
# When enabled, the integration automatically fetches the previous day's location track at 01:00
# local time — only if the day's data is not already cached. Removes the need for a manual
# automation calling `xplora_watch.fetch_history` daily.
CONF_AUTO_FETCH_HISTORY: Final = "auto_fetch_history"
CONF_SIGNIN_TYP: Final = "signin_typ"
CONF_TIMEZONE: Final = "timezone"
CONF_USERLANG: Final = "userlang"
CONF_WATCHES: Final = "watches"
# User-chosen, human-readable label for the account (e.g. "Dad", "Mom"). The same physical watch
# can be linked to several accounts; the alias is the top of the account-token chain that
# differentiates those copies in the device name and entity slug (alias → account display name →
# opaque account id). Captured (required, pre-filled with the display name) in the config flow and
# editable later via the options flow; options override data.
CONF_ACCOUNT_ALIAS: Final = "account_alias"

SENSOR_BATTERY: Final = "battery"
SENSOR_DISTANCE: Final = "distance"
SENSOR_MESSAGE: Final = "message"
SENSOR_STEP_DAY: Final = "step_day"
SENSOR_XCOIN: Final = "xcoin"
SENSOR_ALARMS: Final = "alarms"
SENSOR_SILENTS: Final = "silents"
SENSOR_LAST_UPDATE: Final = "last_update"
# Optional, opt-in (disabled-by-default) sensor surfacing the watch's accumulated location
# history. The state is the number of points in the bounded recent window; the points
# themselves live in attributes (bounded -- see below) and in a persistent Store (the full,
# retained set). See `coordinator` (fetch/accumulate) and `sensor.XploraHistorySensor`.
SENSOR_LOCATION_HISTORY: Final = "location_history"

BINARY_SENSOR_CHARGING: Final = "charging"
BINARY_SENSOR_SAFEZONE: Final = "safezone"
BINARY_SENSOR_STATE: Final = "state"

# Button platform: direct, per-watch action buttons. Each button is bound to one watch, so
# pressing it runs the action for that child (and the guardian/account owning the config entry)
# directly -- the same effect as the matching service but without picking a target/user.
# `reboot`/`shutdown` reuse the service action names; `update` mirrors the `see` manual refresh.
BUTTON_REBOOT: Final = "reboot"
BUTTON_SHUTDOWN: Final = "shutdown"
BUTTON_UPDATE: Final = "update"
# `refresh_functions` pulls the slow-changing "functions" data (alarms, silent times, safe-zone
# definitions) on demand -- the same effect as the `refresh_functions` service. Unlike `update`
# (which refreshes location/battery via `see`), this is the only control that re-fetches alarms &
# silent times, so it carries a descriptive name on the controls card.
BUTTON_REFRESH_FUNCTIONS: Final = "refresh_functions"

# Entity description keys that belong to a watch's *Guardian* (`guardianType == "FIRST"`) only and
# are not created for an account that is merely a *Contact* of the watch. A Contact is sent no
# battery, location or alarm/silent data (so those sensors would sit permanently unavailable), and
# the integration restricts watch-control actions to the Guardian as a client policy (ref:XW-009).
# Kept in one place so the entity platforms, the upgrade cleanup sweep, and the service gate can't
# drift on which kinds are Guardian-only. The device-tracker entities (main tracker + per-zone
# safe-zone trackers) carry no description key and are gated at the platform level instead (a
# Contact gets no trackers at all), so they are intentionally not listed here.
#
# NOT restricted -- kept for a Contact: online status (the `state` binary sensor), steps
# (`step_day`), xcoin, chat (`message`), last-update, and the `update` button.
GUARDIAN_ONLY_KEYS: Final[frozenset[str]] = frozenset(
    {
        SENSOR_BATTERY,
        SENSOR_DISTANCE,
        SENSOR_ALARMS,
        SENSOR_SILENTS,
        SENSOR_LOCATION_HISTORY,
        BINARY_SENSOR_CHARGING,
        BINARY_SENSOR_SAFEZONE,
        BUTTON_REBOOT,
        BUTTON_SHUTDOWN,
        BUTTON_REFRESH_FUNCTIONS,
    }
)

# Per-watch "last update" outcome, recorded by the coordinator each refresh and surfaced by the
# `last_update` diagnostic sensor (and the cards). It distinguishes a watch that reported fresh data
# (`ok`) from one that did not respond -- off / out of reach -- (`no_response`) and from a failed
# request (`error`). `_refresh_watch_fix` is the source of truth: it polls until the watch's fix
# time advances, so "no advance" == the watch never reported.
ATTR_LAST_UPDATE_STATUS: Final = "last_update_status"  # key inside coordinator data[wuid]
ATTR_LAST_UPDATE_TIME: Final = "last_update_time"
LAST_UPDATE_OK: Final = "ok"
LAST_UPDATE_NO_RESPONSE: Final = "no_response"
LAST_UPDATE_ERROR: Final = "error"

# Keys under each watch's coordinator data holding the raw alarm / silent-time lists
# (see `coordinator.get_data()`); also the per-entry list attribute name on the sensors.
ATTR_ALARM: Final = "alarm"
ATTR_SILENT: Final = "silent"

# --- Location history ------------------------------------------------------------------------
# Coordinator-data key holding the *bounded* recent location-history slice the sensor exposes
# (the full retained set lives only in the per-entry history `Store`, never on entity state).
ATTR_LOCATION_HISTORY: Final = "location_history"
# Attribute names on the location-history sensor. `ATTR_HISTORY_POINTS` is intentionally
# excluded from the recorder (see `sensor._unrecorded_attributes`) so the point list never
# bloats the DB nor risks the ~16 KB attribute truncation.
ATTR_HISTORY_POINTS: Final = "history_points"
ATTR_HISTORY_TOTAL_POINTS: Final = "history_total_points"
ATTR_HISTORY_WINDOW_HOURS: Final = "history_window_hours"
# Compact per-point keys (subset of the API's `SimpleLocation`) carried in attributes / Store /
# websocket responses. `tm` is the point timestamp in epoch milliseconds.
ATTR_HISTORY_TM: Final = "tm"
ATTR_HISTORY_LAT: Final = "lat"
ATTR_HISTORY_LNG: Final = "lng"
ATTR_HISTORY_RAD: Final = "rad"
ATTR_HISTORY_LOCATE_TYPE: Final = "locateType"
ATTR_HISTORY_POI: Final = "poi"
ATTR_HISTORY_ADDR: Final = "addr"
ATTR_HISTORY_CITY: Final = "city"

# How long the accumulated history is retained in the Store before older points are pruned.
# User-configurable in the options flow (`CONF_HISTORY_RETENTION_DAYS`); genuinely stores more
# than the app's ~3-day window.
CONF_HISTORY_RETENTION_DAYS: Final = "history_retention_days"
DEFAULT_HISTORY_RETENTION_DAYS: Final = 14
HISTORY_RETENTION_DAYS_MIN: Final = 1
HISTORY_RETENTION_DAYS_MAX: Final = 90


def normalize_history_retention_days(raw: int | str | None) -> int:
    """Clamp a stored/legacy history-retention value into the supported day range.

    Falls back to the default for unparseable values and clamps anything outside
    ``[HISTORY_RETENTION_DAYS_MIN, HISTORY_RETENTION_DAYS_MAX]`` so resolution never raises and
    a fat-fingered value can neither disable retention nor balloon the Store unbounded.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return DEFAULT_HISTORY_RETENTION_DAYS
    return max(HISTORY_RETENTION_DAYS_MIN, min(HISTORY_RETENTION_DAYS_MAX, value))


# Points requested from the API per day. A 2026-06-27 live capture showed the server does NOT
# strictly honor this downward (it returned 74 points for `limit=50`) and that `LocHistory` returns a
# SINGLE day's track, so this is a ceiling on one day's points. The regular poll fetches only TODAY
# (always fresh -- it is still being written); PAST days are fetched lazily and cached on demand when
# the user picks a date in the card (immutable, so cached permanently until retention prunes them).
LOC_HISTORY_FETCH_LIMIT: Final = 500
# Bound for the slice exposed on entity state: at most this many points, within this many hours.
# Keeps the (un-recorded) attribute comfortably small; longer ranges come from the websocket cmd.
LOC_HISTORY_ATTR_MAX_POINTS: Final = 50
LOC_HISTORY_ATTR_WINDOW_HOURS: Final = 24

# Websocket command the custom card calls to read history ranges longer than the bounded
# attribute (returns the full retained set from the Store for a wuid/time-range).
WS_TYPE_LOCATION_HISTORY: Final = f"{DOMAIN}/location_history"

# weekRepeat is a 7-char "0"/"1" string; index 0 = Sunday .. index 6 = Saturday, matching the
# `DAYS` lists below and the watch/app convention. These canonical keys are what the CRUD
# services and the custom card use to express repeat days in a language-neutral way.
WEEKDAY_KEYS: Final[list[str]] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

# Polling is the integration's only standing source of rate-limit/ban risk, so it is opt-in
# and coarse: OFF by default (no recurring cloud calls), with a few wide presets. Users who
# need faster or conditional updates create their own HA automation calling `xplora_watch.see`.
SCAN_INTERVAL_OFF: Final = 0
# Canonical preset values (seconds) -- the single source of truth for normalization.
SCAN_INTERVAL_PRESETS: Final[tuple[int, ...]] = (
    SCAN_INTERVAL_OFF,
    30 * 60,
    60 * 60,
    2 * 60 * 60,
)
# Per-language dropdown labels for the presets above, keyed by the seconds value. Localized the
# same way as HOME_SAFEZONE (pick by UI language, fall back to DEFAULT_LANGUAGE) rather
# than via HA's selector `translation_key`, to stay consistent with the other selects in this flow.
SCAN_INTERVAL_OPTIONS: Final[dict[str, dict[int, str]]] = {
    "en": {
        SCAN_INTERVAL_OFF: "Off (no polling)",
        30 * 60: "Every 30 minutes",
        60 * 60: "Every hour",
        2 * 60 * 60: "Every 2 hours",
    },
    "de": {
        SCAN_INTERVAL_OFF: "Aus (kein Abrufen)",
        30 * 60: "Alle 30 Minuten",
        60 * 60: "Jede Stunde",
        2 * 60 * 60: "Alle 2 Stunden",
    },
    "es": {
        SCAN_INTERVAL_OFF: "Desactivado (sin sondeo)",
        30 * 60: "Cada 30 minutos",
        60 * 60: "Cada hora",
        2 * 60 * 60: "Cada 2 horas",
    },
    "fr": {
        SCAN_INTERVAL_OFF: "Désactivé (aucune interrogation)",
        30 * 60: "Toutes les 30 minutes",
        60 * 60: "Toutes les heures",
        2 * 60 * 60: "Toutes les 2 heures",
    },
}
DEFAULT_SCAN_INTERVAL: Final = SCAN_INTERVAL_OFF


def normalize_scan_interval(raw: int | str | None) -> int:
    """Snap any stored/legacy scan interval to one of the supported presets (seconds).

    `0`/falsy stays OFF (no polling). Any positive value -- including legacy free-form
    intervals and the unsafe sub-minute values the old slider allowed -- maps to the *nearest*
    non-off preset. So on upgrade an existing poller is never silently disabled, and nothing
    can poll faster than 30 minutes, without needing a formal config-entry migration.
    """
    try:
        value = int(raw or 0)
    except TypeError, ValueError:
        return SCAN_INTERVAL_OFF
    if value <= 0:
        return SCAN_INTERVAL_OFF
    non_off = [seconds for seconds in SCAN_INTERVAL_PRESETS if seconds != SCAN_INTERVAL_OFF]
    return min(non_off, key=lambda preset: abs(preset - value))


# --- Functions (alarms / silent times / safe zones) poll interval ---------------------------
# These rarely change, so they get their own cadence decoupled from the main poll. Special
# sentinel WITH_POLL means "fetch on every main poll" (the pre-feature behavior); OFF (the
# default) means "never auto-fetch -- refresh on demand via `xplora_watch.refresh_functions`
# or by tapping the overview card".
SCAN_INTERVAL_FUNCTIONS_WITH_POLL: Final = -1
DEFAULT_SCAN_INTERVAL_FUNCTIONS: Final = SCAN_INTERVAL_OFF

# Default for CONF_REFRESH_ON_CARD_RENDER: OFF, matching the ban-defense "don't auto-fetch" stance.
DEFAULT_REFRESH_ON_CARD_RENDER: Final = False
# Default for CONF_AUTO_FETCH_HISTORY: OFF (opt-in).
DEFAULT_AUTO_FETCH_HISTORY: Final = False
# Local-time hour at which the opt-in auto-fetch runs (01:00). Late enough that the previous day is
# complete; early enough to archive it well within the backend's ~3-day serving window.
AUTO_FETCH_HISTORY_HOUR: Final = 1
SCAN_INTERVAL_FUNCTIONS_PRESETS: Final[tuple[int, ...]] = (
    SCAN_INTERVAL_OFF,
    6 * 60 * 60,
    24 * 60 * 60,
    SCAN_INTERVAL_FUNCTIONS_WITH_POLL,
)
SCAN_INTERVAL_FUNCTIONS_OPTIONS: Final[dict[str, dict[int, str]]] = {
    "en": {
        SCAN_INTERVAL_OFF: "Off (manual only)",
        6 * 60 * 60: "Every 6 hours",
        24 * 60 * 60: "Daily",
        SCAN_INTERVAL_FUNCTIONS_WITH_POLL: "With every poll",
    },
    "de": {
        SCAN_INTERVAL_OFF: "Aus (nur manuell)",
        6 * 60 * 60: "Alle 6 Stunden",
        24 * 60 * 60: "Täglich",
        SCAN_INTERVAL_FUNCTIONS_WITH_POLL: "Bei jedem Abruf",
    },
    "es": {
        SCAN_INTERVAL_OFF: "Desactivado (solo manual)",
        6 * 60 * 60: "Cada 6 horas",
        24 * 60 * 60: "Diariamente",
        SCAN_INTERVAL_FUNCTIONS_WITH_POLL: "En cada sondeo",
    },
    "fr": {
        SCAN_INTERVAL_OFF: "Désactivé (manuel uniquement)",
        6 * 60 * 60: "Toutes les 6 heures",
        24 * 60 * 60: "Quotidien",
        SCAN_INTERVAL_FUNCTIONS_WITH_POLL: "À chaque interrogation",
    },
}


def normalize_scan_interval_functions(raw: int | str | None) -> int:
    """Snap a stored functions-interval value to one of the supported presets (seconds).

    Unlike the main scan interval, this keeps the exact preset values (OFF, 6h, daily, and the
    WITH_POLL sentinel) rather than nearest-rounding, since the set is small and explicit. An
    unrecognized value falls back to the default (OFF).
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return DEFAULT_SCAN_INTERVAL_FUNCTIONS
    return value if value in SCAN_INTERVAL_FUNCTIONS_PRESETS else DEFAULT_SCAN_INTERVAL_FUNCTIONS


HOME: Final = "zone.home"
TRACKER_UPDATE_STR: Final = f"{DOMAIN}_tracker_update"

DATA_HASS_CONFIG: Final = "hass_config"

# Frontend: the bundled custom Lovelace card (`www/xplora-watch-card.js`) is served at this URL
# and auto-registered as an extra JS module so users don't have to add a dashboard resource by
# hand. `DATA_FRONTEND_REGISTERED` guards the one-time registration across config entries/reloads.
FRONTEND_SCRIPT_FILE: Final = "xplora-watch-card.js"
FRONTEND_SCRIPT_URL: Final = f"/{DOMAIN}_static/{FRONTEND_SCRIPT_FILE}"
DATA_FRONTEND_REGISTERED: Final = "frontend_card_registered"
# Home Assistant only wires up the `/local` static route (-> `config/www`) at startup *if* that
# directory already exists then. The integration creates `config/www/{voice,image,video}` during
# entry setup -- after startup -- so on a fresh install `/local` is never registered and cached
# media (voice/image/video) 404s until the next restart. We register those media sub-paths
# ourselves to serve them regardless; this flag guards the one-time registration across entries.
DATA_MEDIA_PATHS_REGISTERED: Final = "media_static_paths_registered"

MAPS: Final[list[str]] = ["openstreetmap.org (free)", "opencagedata.com (with Licence)"]

##########################
# Section: Multilanguage #
##########################

DEFAULT_LANGUAGE: Final[str] = "en"

XPLORA_USER_LANGS: Final[list[dict[str, str]]] = [
    {"en": "en-GB"},
    {"es": "es-ES"},
    {"de": "de-DE"},
    {"nb": "nb-NO"},
    {"sv": "sv-SE"},
    {"hu": "hu-HU"},
    {"it": "it-IT"},
    {"fr": "fr-FR"},
    {"hr": "hr-HR"},
    {"da": "da-DK"},
    {"fi": "fi-FI"},
]

SUPPORTED_LANGUAGES: Final[list[dict[str, str]]] = [{"de": "Deutsch"}, {"en": "English"}]

DAYS: Final[dict[str, list[str]]] = {
    "en": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
    "de": ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"],
}

HOME_SAFEZONE: Final[dict[str, dict[str, str]]] = {
    "en": {"off": "off", "on": "on"},
    "de": {"off": "aus", "on": "an"},
}

SIGNIN: Final[dict[str, dict[str, str]]] = {
    "en": {
        CONF_EMAIL: "Signed up with an email address",
        CONF_PHONENUMBER: "Signed up with a phone number",
    },
    "de": {
        CONF_EMAIL: "Mit E-Mail-Adresse angemeldet",
        CONF_PHONENUMBER: "Mit Telefonnummer angemeldet",
    },
}
