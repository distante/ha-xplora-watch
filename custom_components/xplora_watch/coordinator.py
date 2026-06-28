"""Coordinator for Xplora® Watch Version 2."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.components.device_tracker.const import ATTR_BATTERY, ATTR_LOCATION_NAME
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_COUNTRY_CODE, CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .config import ResolvedOptions, resolve, resolve_language
from .const import (
    API_KEY_MAPBOX,
    ATTR_HISTORY_ADDR,
    ATTR_HISTORY_CITY,
    ATTR_HISTORY_LAT,
    ATTR_HISTORY_LNG,
    ATTR_HISTORY_LOCATE_TYPE,
    ATTR_HISTORY_POI,
    ATTR_HISTORY_RAD,
    ATTR_HISTORY_TM,
    ATTR_LAST_UPDATE_STATUS,
    ATTR_LAST_UPDATE_TIME,
    ATTR_LOCATION_HISTORY,
    ATTR_TRACKER_ADDR,
    ATTR_TRACKER_IMEI,
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LICENCE,
    ATTR_TRACKER_LNG,
    ATTR_TRACKER_POI,
    ATTR_TRACKER_RAD,
    ATTR_WATCH,
    AUTO_FETCH_HISTORY_HOUR,
    BINARY_SENSOR_SAFEZONE,
    CONF_PHONENUMBER,
    CONF_TIMEZONE,
    CONF_USERLANG,
    CONF_WATCHES,
    DOMAIN,
    LAST_UPDATE_NO_RESPONSE,
    LAST_UPDATE_OK,
    LOC_HISTORY_ATTR_MAX_POINTS,
    LOC_HISTORY_ATTR_WINDOW_HOURS,
    LOC_HISTORY_FETCH_LIMIT,
    LOCATE_POLL_DELAYS,
    MAPS,
    SCAN_INTERVAL_FUNCTIONS_WITH_POLL,
    SCAN_INTERVAL_OFF,
    SENSOR_ALARMS,
    SENSOR_LOCATION_HISTORY,
    SENSOR_MESSAGE,
    SENSOR_SILENTS,
    SENSOR_XCOIN,
    URL_MAPBOX,
    URL_OPENSTREETMAP,
)
from .demo import make_controller
from .geocoder import OpenCageGeocodeUA
from .log import Log
from .pyxplora_api.const import ALL_WATCH_FUNCTIONS, DEFAULT_TIMEOUT, WatchFunction
from .pyxplora_api.exception_classes import AuthError, Error, LoginError, RateLimitError
from .pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from .pyxplora_api.model import ChatsNew
from .pyxplora_api.pyxplora_api_async import PyXploraApi, TokenRefreshOutcome
from .pyxplora_api.status import LocationType, WatchOnlineStatus

# Return type of a controller-call factory routed through `_with_recovery`.
_T = TypeVar("_T")

# Which entities consume each optional per-watch data set, as (platform, unique_id-marker) pairs.
# The coordinator skips fetching a data set whose consuming entities are all disabled (see
# `_consumers_all_disabled`). The markers are the `_<watch>_<key>_` segment every unique_id embeds
# (see sensor.py / device_tracker.py), delimited by underscores so they can't match a stray
# substring. Each "functions" data set is keyed by its `WatchFunction` so it can be gated
# *individually* -- disabling the alarms sensor suppresses only the `Alarms` request, not the whole
# group. Safe-zone *definitions* are consumed only by the device_tracker entities (the binary_sensor
# uses the live in/out status from `deviceList`, not these), hence the device_tracker platform filter.
_FUNCTION_CONSUMERS: dict[WatchFunction, tuple[tuple[Platform, str], ...]] = {
    WatchFunction.ALARMS: ((Platform.SENSOR, f"_{ATTR_WATCH}_{SENSOR_ALARMS}_"),),
    WatchFunction.SILENT_TIMES: ((Platform.SENSOR, f"_{ATTR_WATCH}_{SENSOR_SILENTS}_"),),
    WatchFunction.SAFE_ZONES: ((Platform.DEVICE_TRACKER, f"_{ATTR_WATCH}_{BINARY_SENSOR_SAFEZONE}_"),),
}
# Consumer of the accumulated location-history data: the single disabled-by-default
# location-history sensor. Like the chat data, it is fetched by its own standalone request
# (`getWatchLocHistory`, NOT part of the `setDevices` `WatchFunction` bundle), so it is gated
# separately here -- disabled sensor -> the `LocHistory` request is never issued. It is also kept OFF
# the regular `see` / periodic-poll path: it is fetched only on an explicit force refresh (and only
# when enabled), so a normal location update never drags history along. See `_fetch_watch_entry`.
_HISTORY_CONSUMERS: tuple[tuple[Platform, str], ...] = ((Platform.SENSOR, f"_{ATTR_WATCH}_{SENSOR_LOCATION_HISTORY}_"),)

# Version of the persisted-session storage schema (HA `Store`). Bump if `dump_session`'s shape
# changes incompatibly; an unreadable/old blob is ignored and the integration just logs in again.
STORAGE_VERSION = 1


class XploraDataUpdateCoordinator(DataUpdateCoordinator):
    """Create XploraDataUpdateCoordinator that manages data updates."""

    location_name: str | None = None
    licence: str | None = None
    controller: PyXploraApi
    lat: float | None = None
    lng: float | None = None
    poi: str | None = None
    location_accuracy: int = -1
    locate_type: str = LocationType.UNKNOWN.value
    last_track_time: str | None = None
    unread_msg: int = -1
    battery: int = -1
    is_charging: bool = False
    is_safezone: bool = False
    is_online: bool = False
    device: dict[str, Any] = {}
    username: str
    user_id: str
    is_admin: dict[str, bool] = {}
    alarm: list = []
    silent: list = []
    imei: str = ""
    watch_id: str | None = None
    os_version: str = "n/a"
    model: str = "GPS-Watch"
    entity_picture: str = ""
    _step_day: dict
    _xcoin: int

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize Xplora® data updater."""
        self._entry: ConfigEntry = entry
        # Per-entry child logger: lets one config entry be flipped to DEBUG without raising
        # the level for every other entry (the per-poll loop below is the noisiest source).
        self._log = Log(entry_id=entry.entry_id)
        # Per-instance admin cache (keyed by wuid). MUST be an instance attribute: the
        # class-level `is_admin = {}` default is shared across every coordinator, so once it is
        # keyed by `wuid` (not the old per-entry `user_id+entry_id`) two entries -- or two tests
        # -- for the same watch would collide and wrongly skip the one-time `isAdmin` fetch.
        self.is_admin: dict[str, bool] = {}
        # Resolve user options once: `options_update_listener` reloads the entry (rebuilding
        # this coordinator) on every options change, so a single resolution here is always
        # current. `resolve()` snaps the scan interval to a supported preset internally.
        self._resolved: ResolvedOptions = resolve(entry.options)
        self._opencage_apikey = self._resolved.opencage_apikey
        self._maps = self._resolved.maps
        # Per-watch timestamp of the last successful "functions" (alarm/silent/safezone) fetch,
        # used by the separate functions-poll interval to decide when a refresh is due. Empty
        # until the first fetch; OFF means it is only ever populated by an on-demand refresh.
        self._last_functions_fetch: dict[str, datetime] = {}
        # Persisted-session store (HA `.storage`): the Xplora token lives ~35 days but is held
        # only in memory, so without this every restart spends a fresh login. We cache the token
        # blob here and reload it on `set_controller` so a restart within the token's life makes
        # zero login calls. `_persisted_token` tracks what is on disk so we only rewrite on change.
        self._session_store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.session")
        self._persisted_token: str | None = None
        # The per-watch functions-fetch timestamps above are also persisted (HA `.storage`) so a
        # restart within the functions-poll window does NOT re-run the one-time seed fetch.
        # Otherwise every HA restart spends an Alarms/SafeZones/SlientTimes call per watch even with
        # the functions interval OFF -- exactly the traffic the ban-defense default exists to avoid.
        # Kept in its OWN store (not folded into the session blob) so the token blob, which is
        # restored verbatim via `restore_session`, is never reshaped.
        self._functions_store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.functions")
        # Accumulated per-watch location history, persisted (HA `.storage`) so it survives restarts
        # and -- crucially -- grows *beyond* the app's ~3-day window. `self._loc_history` is the
        # in-memory mirror, bucketed by local (watch-tz) calendar day so each day can be cached
        # independently: `{wuid: {"YYYY-MM-DD": [ {tm(ms), lat, lng, rad, locateType, poi, addr,
        # city}, ... ]}}` (each bucket sorted ascending by `tm`). TODAY is always re-fetched fresh;
        # past days are immutable and cached. Pruned to `history_retention_days`. Kept in its own
        # store (not the session/functions blobs) so neither of those is reshaped.
        self._history_store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.history")
        self._loc_history: dict[str, dict[str, list[dict[str, Any]]]] = {}
        # Per-watch reverse-geocode cache: last (lat, lng) -> (location_name, licence). Reverse
        # geocoding is a third-party HTTP call per watch per refresh; a stationary watch echoes the
        # same fix every poll, so we skip the call when the fix is unchanged (see `get_map`).
        self._geocode_cache: dict[str, tuple[float, float, str | None, str | None]] = {}
        # In-flight coalescer for `async_update_xplora_data`: maps a request signature
        # (targets + force_functions) to the running fetch task. Concurrent callers with the same
        # signature -- e.g. two cards rendering at once, or a button press racing a service call /
        # scheduled poll for the same watch(es) -- await the SAME network fan-out instead of each
        # launching their own. This is the authoritative dedup; the cards' render-time guard is now
        # just a UX nicety on top of it. Keyed entries are removed as soon as the fetch settles.
        self._inflight_updates: dict[tuple[tuple[str, ...] | None, bool], asyncio.Task[dict[str, Any]]] = {}
        # Centralized, single-flight token recovery (see `_with_recovery` / `_recover_token`). Every
        # controller call routes through `_with_recovery`, so an expired token is recovered at ONE
        # choke-point. `_token_recovery` is the in-flight recovery task: concurrent callers that hit
        # `AuthError` at the same generation coalesce onto it instead of each firing their own
        # `RefreshToken` (the rate-limit storm this integration exists to avoid). `_token_generation`
        # is bumped on each successful recovery so a *stale* AuthError -- one whose request began
        # before a recovery that has since completed -- is recognised and skipped rather than
        # triggering a needless second refresh.
        self._token_generation: int = 0
        self._token_recovery: asyncio.Task[None] | None = None
        # Unsubscribe callback for the optional 01:00 daily history auto-fetch listener.
        # None when the feature is disabled or not yet set up.
        self._cancel_history_scheduler: Callable[[], None] | None = None
        name = f"{DOMAIN}-"
        if CONF_PHONENUMBER in entry.data:
            name += entry.data[CONF_PHONENUMBER][5:]
        elif CONF_EMAIL in entry.data:
            name += entry.data[CONF_EMAIL]

        # OFF (the default) means no recurring polling at all -- updates then come only from
        # `xplora_watch.see` (manual or via a user automation), keeping the integration off the
        # rate-limit radar by default.
        _scan_interval = self._resolved.scan_interval
        if _scan_interval == SCAN_INTERVAL_OFF:
            _update_interval = None
            self._log.debug("Update interval disabled (polling off)")
        else:
            _update_interval = timedelta(seconds=_scan_interval)
        super().__init__(
            hass,
            self._log.underlying_logger,
            name=f"{DOMAIN}-{entry.data[CONF_PHONENUMBER][5:] if CONF_EMAIL not in entry.data else ''}",
            update_method=self.async_update_xplora_data,
            update_interval=_update_interval,
        )

    async def set_controller(self, session: aiohttp.ClientSession | None) -> None:
        """Set the controller to use, reusing an already-authenticated one across polls.

        The Xplora token is reactive (reused for ~35 days until the server rejects it, see
        `AuthError`/`refresh()`), so rebuilding `PyXploraApi` per poll -- and therefore
        forcing a fresh login every time -- was the dominant driver of the rate-limit bans
        this integration exists to fix. The aiohttp session is a hass singleton, so it's
        safe to keep this controller (and its token) alive for the coordinator's lifetime.
        """
        if getattr(self, "controller", None) is not None:
            return
        data = self._entry.data
        options = self._entry.options
        self.controller = make_controller(
            countrycode=data.get(CONF_COUNTRY_CODE, ""),
            phoneNumber=data.get(CONF_PHONENUMBER, ""),
            password=data[CONF_PASSWORD],
            userLang=data[CONF_USERLANG],
            timeZone=data[CONF_TIMEZONE],
            wuid=options.get(CONF_WATCHES),
            email=data.get(CONF_EMAIL),
            session=session,
        )
        await self._restore_session()
        await self._restore_functions_fetch()
        await self._restore_loc_history()

    async def _restore_session(self) -> None:
        """Load a persisted token into the freshly built controller, if one is stored.

        Runs once per coordinator build (HA start / reload). When the stored token is still valid,
        the following `controller.init(forceLogin=False)` makes NO network call at all -- no
        `signInWithEmailOrPhone`, no `Contacts` -- so a restart within the ~35-day token life is
        login-free. A missing/corrupt blob is ignored (a normal login then happens).
        """
        try:
            blob = await self._session_store.async_load()
        except Exception as err:  # noqa: BLE001 -- never let a bad/corrupt store block setup
            self._log.debug("Ignoring unreadable persisted session: %s", err)
            return
        if blob and self.controller.restore_session(blob):
            # Track what is on disk so `_persist_session` only rewrites when the token changes.
            self._persisted_token = self.controller.dump_session().get("issue_token", {}).get("token")
            self._log.debug("Restored persisted Xplora session; login is skipped while the token is valid")

    async def _persist_session(self) -> None:
        """Save the current session to `.storage`, but only when the token actually changed.

        Called after `init()` (covers a fresh login) and after the `AuthError` recovery (covers a
        `RefreshToken`/re-login, which rotates the refresh token -- persisting it is essential or
        the next restore would carry a stale refresh token). A no-op when the token is unchanged,
        so steady-state polling does not hammer the disk.
        """
        token = self.controller.dump_session().get("issue_token", {}).get("token")
        if not token or token == self._persisted_token:
            return
        await self._session_store.async_save(self.controller.dump_session())
        self._persisted_token = token
        self._log.debug("Persisted Xplora session to storage")

    async def _restore_functions_fetch(self) -> None:
        """Load persisted per-watch functions-fetch timestamps so a restart doesn't re-seed.

        The functions-poll interval (default OFF) seeds a never-fetched watch once so its sensors
        aren't empty when first enabled. That state lived only in memory, so each HA restart
        re-triggered that seed fetch; restoring it here means a restart within the interval window
        issues zero extra Alarms/SafeZones/SlientTimes calls. A missing/corrupt store (or an entry
        with an unparseable timestamp) is ignored -- the seed simply happens, as it did before.
        """
        try:
            blob = await self._functions_store.async_load()
        except Exception as err:  # noqa: BLE001 -- never let a bad/corrupt store block setup
            self._log.debug("Ignoring unreadable functions-fetch store: %s", err)
            return
        if not blob:
            return
        restored: dict[str, datetime] = {}
        for wuid, iso in blob.items():
            try:
                restored[wuid] = datetime.fromisoformat(iso)
            except TypeError, ValueError:
                # Skip a corrupt/garbage entry rather than failing the whole restore; that watch
                # just re-seeds on its first fetch.
                continue
        self._last_functions_fetch = restored
        if restored:
            self._log.debug("Restored functions-fetch timestamps for %d watch(es)", len(restored))

    async def _persist_functions_fetch(self) -> None:
        """Save the per-watch functions-fetch timestamps to `.storage` as ISO-8601 strings.

        Called whenever a functions fetch advances the timestamps so a later restart honors the
        interval instead of re-seeding. `datetime` is not JSON-serializable, hence isoformat.
        """
        await self._functions_store.async_save({wuid: dt.isoformat() for wuid, dt in self._last_functions_fetch.items()})

    async def _restore_loc_history(self) -> None:
        """Load the persisted accumulated location history into memory, if any is stored.

        Runs once per coordinator build (HA start / reload) so the retained track (which can span
        far more than the app's ~3-day window) survives restarts. A missing/corrupt blob is ignored
        -- the history just starts empty and re-accumulates from the next fetch. Only well-formed
        per-watch point lists are kept; a garbage value for one watch is skipped, not fatal.
        """
        try:
            blob = await self._history_store.async_load()
        except Exception as err:  # noqa: BLE001 -- never let a bad/corrupt store block setup
            self._log.debug("Ignoring unreadable location-history store: %s", err)
            return
        if not isinstance(blob, dict):
            return
        tzinfo = self._history_tzinfo()
        restored: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for wuid, value in blob.items():
            if isinstance(value, dict):
                # Current per-day shape: keep well-formed buckets.
                restored[wuid] = {
                    day: [p for p in pts if isinstance(p, dict) and ATTR_HISTORY_TM in p]
                    for day, pts in value.items()
                    if isinstance(pts, list)
                }
            elif isinstance(value, list):
                # Legacy flat-list shape -> regroup into day buckets by each point's own day.
                days: dict[str, list[dict[str, Any]]] = {}
                for p in value:
                    if isinstance(p, dict) and ATTR_HISTORY_TM in p:
                        days.setdefault(self._day_key_from_ms(p[ATTR_HISTORY_TM], tzinfo), []).append(p)
                restored[wuid] = days
        self._loc_history = restored
        total = sum(len(bucket) for buckets in restored.values() for bucket in buckets.values())
        if total:
            self._log.debug("Restored %d location-history point(s) across %d watch(es)", total, len(restored))

    async def _persist_loc_history(self) -> None:
        """Save the in-memory accumulated location history to `.storage`.

        Called only after a merge actually changed a watch's point list, so steady-state polling
        of an unchanged track does not hammer the disk.
        """
        await self._history_store.async_save(self._loc_history)

    async def init(self, session: aiohttp.ClientSession | None = None) -> None:
        """Init Coordinator.

        `forceLogin=False` lets `PyXploraApi._login`'s own `_isConnected()`/
        `_hasTokenExpired()` gate decide whether a login is actually needed -- on every poll
        after the first this is a cheap no-op, not a fresh `signInWithEmailOrPhone`. A
        genuinely expired token is still caught and re-logged-in inside `_login`.
        """
        try:
            await self.set_controller(session)
            await self.controller.init(forceLogin=False)
        except RateLimitError, XploraConnectionError:
            # Transient (429 / connection): the controller object is fine, it just couldn't
            # reach the server this poll. Keep it (and any cached token) so the next poll
            # retries cheaply instead of rebuilding and forcing a fresh full login -- the
            # very re-login churn this integration exists to avoid. Surfaced to the caller,
            # which maps it to UpdateFailed.
            raise
        except Error as err:
            # Non-transient (e.g. LoginError): the controller can't authenticate as-is, so
            # drop it to force a clean rebuild on the next attempt. Nothing useful is lost --
            # `_login` had already failed, so there is no valid token to preserve.
            if getattr(self, "controller", None) is not None:
                del self.controller
            # HA's generic "Error setting up entry" line only shows the (shared) entry title, so
            # on a multi-account setup it can't tell you *which* account failed. Name it here --
            # the account identifier (email / phone unique_id) plus the entry id -- so the cause
            # is obvious in the log.
            self._log.error("Xplora setup/login failed for account %s (entry_id %s): %s", self._account_label(), self._entry.entry_id, err)
            raise

        self.username = self.controller.getUserName()
        self.user_id = self.controller.getUserID()
        # `is_admin` is no longer fetched here: it is derived for free from each watch's
        # `guardianType` in the account-wide `deviceList` response (see `_update_is_admin`,
        # called after `setDevices`). The old code issued a `Contacts` GraphQL request per watch
        # on setup just to learn the (static) admin flag -- pure waste now that `deviceList`
        # already carries it.

        # Persist the session if a (fresh) login just produced a new token; a no-op when the token
        # was restored unchanged from storage.
        await self._persist_session()

    async def async_clear_persisted_session(self) -> None:
        """Delete the persisted session blob (entry removal / server-side logout).

        Best-effort: a storage error must never block entry removal.
        """
        self._persisted_token = None
        try:
            await self._session_store.async_remove()
        except Exception as err:  # noqa: BLE001 -- removal must succeed regardless of storage state
            self._log.debug("Failed to remove persisted session (ignored): %s", err)
        try:
            await self._functions_store.async_remove()
        except Exception as err:  # noqa: BLE001 -- removal must succeed regardless of storage state
            self._log.debug("Failed to remove persisted functions-fetch store (ignored): %s", err)
        try:
            await self._history_store.async_remove()
        except Exception as err:  # noqa: BLE001 -- removal must succeed regardless of storage state
            self._log.debug("Failed to remove persisted location-history store (ignored): %s", err)

    def _update_is_admin(self, wuids: list[str]) -> None:
        """Derive the per-watch admin flag from the already-fetched `deviceList` data.

        `guardianType == "FIRST"` means the logged-in user is the watch's primary guardian (admin).
        It rides along on each `deviceList` `WatchListItem` (see `_setDevice`), so reading it here
        costs no extra request -- replacing the old per-watch `Contacts`/`isAdmin` call on setup.
        A watch missing/lacking the field keeps its previous flag (defaulting to False).
        """
        for wuid in wuids:
            guardian_type = self.controller.getDevice(wuid).get("guardianType")
            if guardian_type is not None:
                self.is_admin[wuid] = guardian_type == "FIRST"

    def _account_label(self) -> str:
        """A human-recognizable account identifier for logs (email / phone), never the password."""
        data = self._entry.data
        return self._entry.unique_id or data.get(CONF_EMAIL) or data.get(CONF_PHONENUMBER) or self._entry.entry_id

    def _consumers_all_disabled(self, consumers: tuple[tuple[Platform, str], ...]) -> bool:
        """True only when the data's consuming entities are registered AND all disabled.

        Lets the coordinator skip fetching data nobody is displaying (e.g. the alarm/silent/safezone
        or chat data when those entities are disabled -- which they are by default). Returns False
        when no matching entity is registered yet (during first setup the entities don't exist
        until after the first refresh, so we can't tell -- don't skip), so a brand-new install still
        seeds once. An explicit on-demand refresh bypasses this entirely (`force_functions`).
        """
        registry = er.async_get(self.hass)
        matched = [
            entity
            for entity in er.async_entries_for_config_entry(registry, self._entry.entry_id)
            if any(entity.domain == platform and marker in entity.unique_id for platform, marker in consumers)
        ]
        return bool(matched) and all(entity.disabled_by is not None for entity in matched)

    def _consumers_any_enabled(self, consumers: tuple[tuple[Platform, str], ...]) -> bool:
        """True only when at least one matching consuming entity is registered AND enabled.

        The strict opposite of `_consumers_all_disabled`'s "seed when unknown" stance: this returns
        False when nothing is registered yet. Used to gate the opt-in location-history fetch so its
        `LocHistory` request is issued ONLY once the sensor actually exists and is enabled -- never
        on the first-setup seed (before any entity exists) and never while the default-disabled
        sensor is off. That keeps the ban-defense default (a watch nobody is watching pays nothing).
        """
        registry = er.async_get(self.hass)
        for entity in er.async_entries_for_config_entry(registry, self._entry.entry_id):
            if entity.disabled_by is None and any(
                entity.domain == platform and marker in entity.unique_id for platform, marker in consumers
            ):
                return True
        return False

    def _functions_fetch_due(self, wuids: list[str]) -> bool:
        """Decide whether the slow-changing "functions" data should be (re)fetched this cycle.

        Driven by the separate `scan_interval_functions` option: WITH_POLL always fetches, OFF
        never auto-fetches (refresh is on-demand only), and a positive interval fetches when at
        least one watch is past due (or has never been fetched). When this returns False,
        `_setDevice` carries the last-known alarm/silent/safezone values forward.
        """
        interval = self._resolved.scan_interval_functions
        if interval == SCAN_INTERVAL_FUNCTIONS_WITH_POLL:
            return True
        if interval == SCAN_INTERVAL_OFF:
            return False
        now = datetime.now()
        due = timedelta(seconds=interval)
        for wuid in wuids:
            last = self._last_functions_fetch.get(wuid)
            if last is None or (now - last) >= due:
                return True
        return False

    async def _fetch_watch_entry(self, targets: list[str] | None, force_functions: bool = False) -> dict[str, Any]:
        """Resolve the target watch UUIDs and fetch their data via `data_loop`.

        Extracted out of `async_update_xplora_data` so the `AuthError` recovery path below
        can retry this exact fan-out once after a refresh/re-login, without duplicating it.

        `force_functions` (an on-demand refresh) bypasses the interval gate; otherwise the
        alarm/silent/safezone calls are issued only when `_functions_fetch_due` says so.
        """
        # Candidate watch ids for the gate decision, resolved without a network call (configured
        # list, else the local login-derived ids); the targets path uses the requested ids.
        candidate = targets or self._configured_wuids
        # The coordinator is the single, authoritative decision point for what this poll fetches, so
        # the debug line below matches the real requests. The "functions" data sets (alarms / silent
        # times / safe-zone definitions) are gated *individually* by two rules:
        #   1. Don't fetch a data set whose consuming entities are all disabled -- it would be
        #      useless (they are disabled-by-default entities). Each set is gated on its OWN
        #      consumer, so enabling just the alarms sensor fetches only `Alarms`.
        #   2. Otherwise honor the functions-poll interval, seeding a never-fetched watch once so
        #      its sensors aren't empty when first enabled.
        # An explicit on-demand refresh (`force_functions`) fetches every set, bypassing both rules.
        first_time = any(wuid not in self._last_functions_fetch for wuid in candidate)
        due = first_time or self._functions_fetch_due(candidate)
        if force_functions:
            functions = ALL_WATCH_FUNCTIONS
        elif due:
            functions = frozenset(fn for fn, consumers in _FUNCTION_CONSUMERS.items() if not self._consumers_all_disabled(consumers))
        else:
            functions = frozenset()
        # Chats are deliberately NOT part of the regular `see` / periodic-poll cycle: that path only
        # refreshes the deviceList-derived live status (battery, charging, location, online status,
        # steps) plus the gated functions/history. Chat messages -- and their voice/video/image media
        # downloads -- are fetched exclusively by the standalone `read_message` service (which calls
        # `message_data` directly), so they stay decoupled from the location/status refresh. A plain
        # update carries the last-known chats forward (see `data_loop`'s `include_chats=False` branch)
        # instead of issuing the per-watch `Chats` request.
        include_chats = False
        # Location history is deliberately NOT part of the regular `see` / periodic-poll cycle: a plain
        # update (`force_functions` is False) never issues the `LocHistory` request. It is fetched only
        # on an explicit force -- `async_refresh_functions`, which the card triggers when you open the
        # Location history view -- and only when the (opt-in, default-disabled) sensor is enabled. The
        # popup itself always pulls today fresh over the websocket independently; this branch is just
        # what refreshes the sensor's own count/attribute. (Archiving PAST days beyond the ~3 the
        # backend serves is the separate `fetch_history` service.)
        include_history = force_functions and self._consumers_any_enabled(_HISTORY_CONSUMERS)
        # Pairs with the per-operation `GraphQL request ->` lines from graphql_client to confirm the
        # actual fan-out against what was decided here. `functions` is the exact set of per-watch
        # function queries this poll will issue (empty == none).
        self._log.debug(
            "fetch decision: functions=%s (forced=%s, first_time=%s, due=%s, interval=%s), chats=%s, history=%s",
            sorted(fn.name for fn in functions),
            force_functions,
            first_time,
            due,
            self._resolved.scan_interval_functions,
            include_chats,
            include_history,
        )

        if targets:
            wuids = await self.controller.setDevices(targets, functions=functions)
        else:
            wuids = self._entry.options.get(CONF_WATCHES, await self.controller.setDevices(functions=functions))

        # Advance the functions-fetch clock only when a functions set was actually fetched, so the
        # next poll honors the functions interval. History is deliberately NOT counted here: it rides
        # the device/`see` cadence now (fetched every update), so letting it advance this clock would
        # wrongly suppress alarm/silent/safezone refreshes. Persist so a restart within the interval
        # window skips the one-time functions seed fetch.
        if functions:
            now = datetime.now()
            for wuid in wuids:
                self._last_functions_fetch[wuid] = now
            await self._persist_functions_fetch()

        # `setDevices` just refreshed `deviceList`, which carries each watch's `guardianType`;
        # derive is_admin from it (no extra request) so the `(Admin)` label and the setup log stay
        # correct without the old per-watch `Contacts` call.
        self._update_is_admin(wuids)

        opts = self._resolved
        return await self.data_loop(
            wuids, opts.message, opts.remove_message, opts.auto_mark_read, include_chats=include_chats, include_history=include_history
        )

    async def async_update_xplora_data(
        self, targets: list[str] | None = None, new_data: dict | None = None, force_functions: bool = False
    ) -> dict[str, Any]:
        """Fetch data from Xplora.

        `force_functions` forces the alarm/silent/safezone fetch regardless of the functions
        poll interval -- used by the on-demand `refresh_functions` path.

        Concurrent calls sharing the same request signature (`targets` + `force_functions`) are
        coalesced onto a single in-flight network fan-out via `_inflight_updates`: two cards
        rendering at once, or a button press racing a service call / scheduled poll for the same
        watch(es), all await the SAME request instead of each hitting the API. The `new_data`
        injection path is local-only (no network) and is never coalesced.
        """
        # Initialize the watch entry data
        if new_data:
            self._log.debug("new data from Message Service")
            if self.data:
                self.data.update(new_data)
            else:
                self.data = new_data
            self.async_set_updated_data(self.data)
            return self.data

        # Coalesce concurrent fetches with the same signature. `targets` is normalized (sorted
        # tuple, or None for "all configured watches") so call order doesn't defeat the match; a
        # force_functions fetch is a different signature (it pulls a different data set), so it is
        # never merged with a plain refresh.
        key = (tuple(sorted(targets)) if targets else None, force_functions)
        existing = self._inflight_updates.get(key)
        if existing is not None and not existing.done():
            self._log.debug("Coalescing update onto in-flight request (targets=%s, force_functions=%s)", key[0], force_functions)
            return await existing

        task = self.hass.async_create_task(self._fetch_and_store_xplora_data(targets, force_functions))
        # Retrieve the task's result/exception when it settles so a caller cancelled mid-await (e.g.
        # the coordinator's update timeout firing) doesn't leave an "exception never retrieved".
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        self._inflight_updates[key] = task
        try:
            return await task
        finally:
            self._inflight_updates.pop(key, None)

    async def _with_recovery(self, coro_factory: Callable[[], Awaitable[_T]]) -> _T:
        """Run a controller call through the bounded, single-flight token-recovery ladder.

        This is THE choke-point every controller call should route through. On an expired token
        (`AuthError`) it runs the centralized, single-flight recovery (`_recover_token`) ONCE and
        retries the call once against the fresh token.

        `coro_factory` must build a FRESH awaitable each time it is called -- it may be invoked
        twice (the initial attempt and the post-recovery retry), and a coroutine cannot be awaited
        twice. Typed exceptions other than `AuthError` (`RateLimitError`, `XploraConnectionError`),
        and an `AuthError` re-raised when recovery could not help, propagate to the caller's
        terminal handler (the poll path translates them to `UpdateFailed`; services/buttons log
        them cleanly). Recovery is bounded: at most one refresh + one re-login + one retry.
        """
        seen_generation = self._token_generation
        try:
            return await coro_factory()
        except AuthError:
            await self._recover_token(seen_generation=seen_generation)
            return await coro_factory()

    async def _recover_token(self, *, seen_generation: int) -> None:
        """Centralized, single-flight token recovery.

        `seen_generation` is the token generation the caller observed *before* its request. Two
        guards keep recovery to a single `RefreshToken` no matter how many calls hit `AuthError`
        at once:

        - **Generation guard:** if the token has already rotated since the caller's request began
          (`self._token_generation != seen_generation`), this is a stale `AuthError` -- the token
          is already fresh, so return immediately without a refresh.
        - **In-flight coalescer:** if a recovery is already running, await it and share its outcome
          (success *and* failure) instead of starting another. The check-and-set below has no
          `await` between reading `_token_recovery` and assigning it, so the event loop cannot
          interleave two creators -- exactly the pattern `_inflight_updates` uses for poll fetches.

        Lets transient exceptions from the ladder propagate untouched so all coalesced callers see
        the same failure and nobody loops.
        """
        if self._token_generation != seen_generation:
            return
        existing = self._token_recovery
        if existing is not None and not existing.done():
            await existing
            return
        task = self.hass.async_create_task(self._do_recover_token())
        # Retrieve the result/exception when it settles so a caller cancelled mid-await does not
        # leave an "exception never retrieved" (same guard as `_inflight_updates`).
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        self._token_recovery = task
        try:
            await task
        finally:
            self._token_recovery = None

    async def _do_recover_token(self) -> None:
        """The bounded recovery ladder, run exactly once per in-flight recovery.

        `refresh()` first; escalate to exactly one full re-login ONLY when the server actively
        refused the refresh token (`AUTH_REFUSED`). A transient failure during the refresh (429 /
        connection / server error) is NOT an authorization problem -- re-logging in there would
        hammer the auth endpoint during an outage/ban window, so those exceptions are left to
        propagate (the generation is NOT bumped, so coalesced followers share the failure). The
        refresh/re-login rotates the token (and its refresh token) -- persist it now so a restart
        resumes from the new token instead of a stale one (must happen here, not just in `init()`,
        because polling may be off and there is no next `init()` for a long time). Only on a
        confirmed success is the generation bumped.
        """
        self._log.debug("Xplora token expired (E000004); attempting single-flight RefreshToken recovery")
        outcome = await self.controller.refresh()
        if outcome is TokenRefreshOutcome.AUTH_REFUSED:
            # `_logoff` (ISSUE-5) makes this `forceLogin=True` genuinely re-authenticate.
            # Mirror `init()`'s cleanup: a re-login that fails non-transiently (e.g. LoginError
            # after a password change / locked account) must drop the controller so
            # `set_controller()` rebuilds it fresh on the next poll. Without this, the stale,
            # un-authenticatable controller stays in place (`set_controller` skips rebuild while it
            # is non-None) and every subsequent poll fails with UpdateFailed until the entry is
            # reloaded. A transient failure (429 / connection) keeps the controller -- nothing is
            # wrong with it -- and propagates so coalesced followers share the failure.
            try:
                await self.controller.init(forceLogin=True)
            except RateLimitError, XploraConnectionError:
                raise
            except Error:
                if getattr(self, "controller", None) is not None:
                    del self.controller
                raise
        elif outcome is not TokenRefreshOutcome.REFRESHED:
            # FAILED -> transient/unknown refresh outcome; do not escalate to a re-login. Surface as
            # an AuthError so the caller's terminal handler treats it as an auth failure rather than
            # a silent success (the retry would just fail again on the still-expired token).
            raise AuthError("Xplora token refresh failed; no auth error, skipping re-login")
        await self._persist_session()
        self._token_generation += 1

    @property
    def _configured_wuids(self) -> list[str]:
        """The watch ids this entry operates on, resolved without a network call.

        The canonical resolution used across the integration (poll fan-out, services, the 01:00
        auto-fetch): the saved `CONF_WATCHES` option, falling back to the login-derived ids when it
        is not (yet) present in options.

        The controller fallback is guarded: a non-transient login failure evicts `self.controller`
        (see `init()`), and the 01:00 auto-fetch fires independently of the poll. Touching the
        attribute unconditionally would raise `AttributeError` there -- which its handlers do not
        catch -- crashing the timer callback. With no controller and no saved option there are no
        watches to act on, so resolve to an empty list (the auto-fetch then cleanly no-ops).
        """
        watches: list[str] | None = self._entry.options.get(CONF_WATCHES)
        if watches:
            return watches
        if getattr(self, "controller", None) is not None:
            return list(self.controller.getWatchUserIDs())
        return []

    async def _init_and_fetch(self, targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        """One login-gated network fetch, as a unit routed through `_with_recovery`.

        `init(forceLogin=False)` is a cheap no-op once the token is valid (its own
        `_isConnected()`/`_hasTokenExpired()` gate decides), so re-running it on the post-recovery
        retry costs nothing -- it just guarantees the fetch always runs against a live session.
        """
        await self.init(aiohttp_client.async_get_clientsession(self.hass))
        return await self._fetch_watch_entry(targets, force_functions=force_functions)

    async def _fetch_and_store_xplora_data(self, targets: list[str] | None, force_functions: bool) -> dict[str, Any]:
        """Perform the actual network fetch and store the result (one run per request signature).

        Split out of `async_update_xplora_data` so the coalescer there can share a single run of
        this across concurrent callers. Auth recovery is centralized in `_with_recovery`; this
        method only translates the typed exceptions it may surface into `UpdateFailed`.
        """
        watch_entry = {}
        if self.data:
            watch_entry.update(self.data)

        # Route the whole login+fetch through the centralized single-flight recovery gate: an
        # expired token (E000004) is recovered once (bounded refresh -> at-most-one re-login) and
        # the fetch retried once, all in `_with_recovery`. Here we only translate the typed
        # exceptions it may surface into `UpdateFailed` so HA logs a clean warning and retries on
        # the next scan interval (and a first refresh becomes ConfigEntryNotReady) instead of an
        # "Unexpected error" traceback. Order matters: `XploraConnectionError`/`LoginError`
        # subclass `Error`, so they must precede the generic `Error` clause.
        try:
            watch_entry.update(await self._with_recovery(lambda: self._init_and_fetch(targets, force_functions)))
        except RateLimitError as err:
            # Never retry a 429 ourselves -- that's the whole point of RateLimitError bypassing the
            # per-field retry loops; a re-auth inside a rate-limit window worsens a ban.
            raise UpdateFailed(f"Xplora API rate limit exceeded: {err}") from err
        except XploraConnectionError as err:
            # A connection/timeout failure means we genuinely don't know the watch state; fail the
            # poll (entities go `unavailable`) rather than silently keeping stale values. In-memory
            # `coordinator.data` and `RestoreEntity` still hold the last good value.
            raise UpdateFailed(f"Xplora connection error: {err}") from err
        except (AuthError, LoginError, Error) as err:
            # Recovery was attempted and could not help (refused refresh + failed re-login, an
            # unknown/empty refresh outcome surfaced as AuthError, or a generic client error).
            raise UpdateFailed(f"Xplora auth failed: {err}") from err
        if not self.data:
            self.data = watch_entry
        else:
            self.data.update(watch_entry)
        self.async_set_updated_data(self.data)
        return self.data

    async def async_refresh_functions(self, targets: list[str] | None = None) -> dict[str, Any]:
        """On-demand refresh of the alarm/silent/safezone data.

        Bypasses the `scan_interval_functions` gate (which defaults to OFF). Backed by the
        normal fetch path so the data shape and auth recovery are identical -- it also refreshes
        the core status for `targets`, which is the desired behavior for an explicit user
        action (the `xplora_watch.refresh_functions` service or tapping the overview card).
        """
        return await self.async_update_xplora_data(targets=targets, force_functions=True)

    async def data_loop(
        self,
        wuids: list[str],
        message_limit: int,
        remove_message: bool,
        auto_mark_read: bool = False,
        include_chats: bool = True,
        include_history: bool = False,
    ) -> dict:
        """Fetch and parse Xplora data.

        `include_chats=False` skips the per-watch chat fetch (the `Chats` request) and carries the
        last-known messages forward. The `see` / periodic-poll path always passes `False`: chats are
        owned exclusively by the standalone `read_message` service now, so this refresh only touches
        the deviceList-derived live status (battery, charging, location, online status, steps) plus
        the gated functions/history. `include_history=True` additionally issues the `LocHistory` request and
        merges the result into the retained, accumulated location-history store (the bounded slice
        the sensor shows is always read from that store, so a skipped fetch just carries it forward).
        """
        data = {}
        for wuid in wuids:
            self._log.debug("Fetch data from Xplora: %s", wuid[25:])
            self.device = self.controller.getDevice(wuid=wuid)
            # Force a fresh fix before reading: `deviceList` only carries the watch's *last
            # stored* position/battery, so without this every refresh echoes stale data until
            # something else nudges the watch to report. Trigger a pull-to-refresh here and
            # overlay the result onto the deviceList status below. The return tells us whether
            # the watch actually reported (fix advanced) -> the last-update status.
            responded = await self._refresh_watch_fix(wuid)
            if include_chats:
                res_chats = await self.controller.getWatchChatsRaw(
                    wuid, limit=message_limit, show_del_msg=remove_message, mark_as_read=auto_mark_read
                )
                if isinstance(res_chats, ChatsNew):
                    res_chats = res_chats.to_dict()
                chats = ChatsNew.from_dict(res_chats).to_dict()
            else:
                # Chats are not fetched here -> reuse the last-known messages (updated only by the
                # `read_message` service) instead of issuing a fresh request.
                chats = (self.data or {}).get(wuid, {}).get(SENSOR_MESSAGE, {}) or {}

            # Battery/charging/location/unread-count come from the `deviceList` call
            # `setDevices()` already made (see ISSUE-12), overlaid with the fresh fix above.
            self.unread_msg = self.device.get("unreadChatMessageCount", -1)
            self.battery = self.device.get("watch_battery", -1)
            self.is_charging = self.device.get("watch_charging", False)
            self.get_location()

            # NOTE: `is_safezone` is intentionally INVERTED relative to its name -- it feeds a
            # binary_sensor with device_class SAFETY, where `on` means "problem/unsafe". So
            # `isInSafeZone == True` -> sensor off (safe), outside the zone -> sensor on (alert).
            self.is_safezone = False if self.device.get("isInSafeZone", False) else True

            self.is_online = (
                True
                if self.device.get("getWatchOnlineStatus", WatchOnlineStatus.UNKNOWN.value) == WatchOnlineStatus.ONLINE.value
                else False
            )

            await self.get_watch_functions(wuid, self.device)
            await self.get_map(wuid)
            if include_history:
                await self._fetch_loc_history(wuid)
            watch_data = self.get_data(wuid, chats)
            # Record the per-watch update outcome: the watch accepted the locate request (ok) or it
            # is off / out of reach (no_response). The `last_update` sensor and the cards read this
            # so users can tell a real update from a stale echo.
            update_status = LAST_UPDATE_OK if responded else LAST_UPDATE_NO_RESPONSE
            watch_data[wuid][ATTR_LAST_UPDATE_STATUS] = update_status
            watch_data[wuid][ATTR_LAST_UPDATE_TIME] = datetime.now().isoformat()
            # Debug trace of the update verdict (kept at DEBUG, not WARNING: a watch being briefly
            # out of reach is a normal, frequent outcome -- warning on every no_response would spam
            # the log). This line is the breadcrumb to look for when a user reports "it shows
            # 'didn't respond'": it ties the verdict to `askWatchLocate`'s reachability result.
            self._log.debug("update outcome for watch ...%s: %s (askWatchLocate responded=%s)", wuid[25:], update_status, responded)
            data.update(watch_data)
            self._log_watch_values(wuid, watch_data[wuid])
        return data

    async def _refresh_watch_fix(self, wuid: str) -> bool:
        """Force a fresh location fix and overlay it onto `self.device`.

        Implements a pull-to-refresh sequence (ref:XW-002): tell the watch to take a new fix
        with `askWatchLocate` (its Boolean return is the reachability verdict), then read
        `WatchLastLocate` to overlay the freshest stored fix. `deviceList` alone only ever returns
        the *last stored* fix (and its `tm` lags `WatchLastLocate`'s), so without this
        `xplora_watch.see` keeps echoing stale battery/position until something else nudges the
        watch. When the watch accepted (`responded`), poll at `LOCATE_POLL_DELAYS` (≈ 1s then 5s)
        to pick up the fix it is about to report; otherwise a single read suffices.

        Best-effort and bounded: an offline / unresponsive watch just keeps the last known values
        rather than stalling or hammering the API. Auth / rate-limit / connection failures propagate
        so the caller's recovery + `UpdateFailed` handling runs.

        Returns ``True`` if the watch accepted the locate request (reachable -> a fresh fix follows),
        ``False`` if it did not respond (offline / out of reach). This is taken from
        ``askWatchLocate``'s boolean -- the watch's own "could not update the location" signal --
        not from a `tm` comparison (``deviceList``'s `tm` lags ``WatchLastLocate``'s, so comparing
        them falsely reported a fresh fix every time). The per-watch last-update status derives
        from this.
        """
        debug = self._log.isEnabledFor(logging.DEBUG)

        # `askWatchLocate` returns False when the watch is off / out of reach: the locate request
        # could not be delivered. This -- NOT a stale `tm` -- is the real "could not update the
        # location" signal (capture 2026-06-25): `deviceList`'s `location.tm` can trail
        # `WatchLastLocate.tm` by ~a day, so the old tm comparison "advanced" every time. `True`
        # means the watch accepted the request and is reachable -> a fresh fix follows.
        responded = bool(await self.controller.askWatchLocate(wuid))
        if debug:
            self._log.debug("fresh-fix ...%s: askWatchLocate -> %s", wuid[25:], responded)

        # Read the latest stored fix to overlay -- it is fresher than deviceList regardless of the
        # outcome. When the watch accepted, poll briefly to pick up the fix it is about to report;
        # otherwise a single read suffices. `baseline_tm` (deviceList) is only an early-stop hint --
        # it does NOT decide the outcome.
        baseline_tm = self.device.get("tm")
        fresh: dict[str, Any] = {}
        for poll, delay in enumerate(LOCATE_POLL_DELAYS if responded else (0,), start=1):
            if delay:
                await asyncio.sleep(delay)
            # `with_ask=False`: ask once above, then only *read* WatchLastLocate.
            location = await self.controller.loadWatchLocation(wuid, with_ask=False)
            if debug:
                self._log.debug("fresh-fix ...%s: poll #%d (after %ss) %s", wuid[25:], poll, delay, self._fix_sig(location))
            if location:
                fresh = location
                new_tm = (location.get("watch_last_location") or {}).get("tm")
                if new_tm and new_tm != baseline_tm:
                    break  # got a fix newer than deviceList had -> stop early.

        if not fresh:
            return responded  # nothing read -> still report reachability from askWatchLocate.

        # Overlay the fresh fix onto the deviceList status dict the loop reads from next. The
        # `WatchLastLocate` Location is the same shape embedded in `deviceList[].location`, so
        # these map 1:1 onto the keys `get_location()`/battery reads below.
        watch_last_location = fresh.get("watch_last_location") or {}
        if fresh.get("watch_battery") is not None:
            self.device["watch_battery"] = fresh["watch_battery"]
        self.device["watch_charging"] = fresh.get("watch_charging", self.device.get("watch_charging", False))
        for key in (ATTR_TRACKER_LAT, ATTR_TRACKER_LNG, ATTR_TRACKER_RAD, ATTR_TRACKER_POI, "locateType", "isInSafeZone"):
            if watch_last_location.get(key) is not None:
                self.device[key] = watch_last_location[key]
        if fresh.get("tm"):
            self.device["lastTrackTime"] = fresh["tm"]
        if watch_last_location.get("tm"):
            self.device["tm"] = watch_last_location["tm"]
        return responded

    @staticmethod
    def _fix_sig(location: dict[str, Any] | None) -> dict[str, Any]:
        """Compact signature of a `loadWatchLocation` result for the fresh-fix DIAGNOSTIC log.

        Pulls the candidate "did the watch report?" fields out of the raw `watch_last_location`
        Location so a single log line shows what changes (or doesn't) across the poll -- used to
        confirm whether `tm` (or `isAdjusted` / `locateType` / position) is the real freshness
        signal. DEBUG-only; includes coordinates, so treat the log as sensitive.
        """
        wll = (location or {}).get("watch_last_location") or {}
        return {
            "tm": wll.get("tm"),
            "isAdjusted": wll.get("isAdjusted"),
            "locateType": wll.get("locateType"),
            "lat": wll.get("lat"),
            "lng": wll.get("lng"),
            "rad": wll.get("rad"),
            "batteryTm": wll.get("batteryTm"),
        }

    def _log_watch_values(self, wuid: str, entry: dict[str, Any]) -> None:
        """Debug-log every value the integration can surface for one watch.

        `entry` is exactly what `get_data` keyed under this `wuid` -- the single dict every
        entity (sensor/binary_sensor/device_tracker/switch) reads from. Logging the full set
        here (not just the deviceList status subset, which `_setDevices` logs separately) lets
        a "stuck unknown"/"not updating" report be diagnosed against the resolved values the
        UI actually shows. The bulky/sensitive `message` chats blob is reduced to a count.

        Off unless this entry's logger is at DEBUG. NOTE: includes coordinates (lat/lng) and
        the reverse-geocoded address -- treat the debug log as sensitive.
        """
        if not self._log.isEnabledFor(logging.DEBUG):
            return
        chats = entry.get(SENSOR_MESSAGE) or {}
        chat_count = len(chats.get("list", [])) if isinstance(chats, dict) else 0
        self._log.debug(
            "watch ...%s values: battery=%s charging=%s online=%s safezone=%s "
            "step_day=%s xcoin=%s unread=%s lat=%s lng=%s poi=%s address=%s locateType=%s "
            "accuracy=%s lastTrack=%s alarms=%d silents=%d model=%s os=%s imei=%s chats=%d",
            wuid[25:],
            entry.get(ATTR_BATTERY),
            entry.get("isCharging"),
            entry.get("isOnline"),
            entry.get("isSafezone"),
            entry.get("step_day"),
            entry.get(SENSOR_XCOIN),
            entry.get("unreadMsg"),
            entry.get(ATTR_TRACKER_LAT),
            entry.get(ATTR_TRACKER_LNG),
            entry.get(ATTR_TRACKER_POI),
            entry.get(ATTR_LOCATION_NAME),
            entry.get("locateType"),
            entry.get("location_accuracy"),
            entry.get("lastTrackTime"),
            len(entry.get("alarm", []) or []),
            len(entry.get("silent", []) or []),
            entry.get("model"),
            entry.get("os_version"),
            entry.get(ATTR_TRACKER_IMEI),
            chat_count,
        )

    async def get_watch_functions(self, wuid: str, device: dict[str, Any]) -> None:
        """Get functions that need to be called when a watch is created.

        Reads alarm/silent data from the already-fetched `device` dict (populated by
        `PyXploraApi._setDevice`) rather than re-fetching from the API -- avoids a redundant
        login + Alarms request per watch per poll against a rate-limit-sensitive server.
        """
        self.alarm = device.get("getWatchAlarm", [])
        self.silent = device.get("getSilentTime", [])

        sw_version: dict[str, Any] = device.get("getWatches", {})
        self.imei = sw_version.get(ATTR_TRACKER_IMEI, wuid)
        self.watch_id = wuid
        self.os_version = sw_version.get("osVersion", "n/a")
        self.model = sw_version.get("model", "GPS-Watch")
        self.entity_picture = device.get("getWatchUserIcons", "")

        self._step_day = device.get("getWatchUserSteps", {}).get("day")
        self._xcoin = device.get("getWatchUserXCoins", 0)

    def get_location(self) -> None:
        """Get location information from device.

        Sourced entirely from `self.device` (the `deviceList` status subset `setDevices()`
        already fetched, see ISSUE-12) -- `poi`/`locateType` used to need a separate
        `loadWatchLocation` call here; `_setDevice` now stores them on the device dict too.
        """
        self.lat = float(self.device.get(ATTR_TRACKER_LAT, 0.0)) if self.device.get(ATTR_TRACKER_LAT, None) else None
        self.lng = float(self.device.get(ATTR_TRACKER_LNG, 0.0)) if self.device.get(ATTR_TRACKER_LNG, None) else None
        self.poi = self.device.get(ATTR_TRACKER_POI, None)
        self.location_accuracy = self.device.get(ATTR_TRACKER_RAD, -1)
        self.locate_type = self.device.get("locateType", LocationType.UNKNOWN.value)
        self.last_track_time = self.device.get("lastTrackTime", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    async def get_map(self, wuid: str) -> None:
        """Reverse-geocode the current fix to a human-readable address (cached when unchanged).

        Reverse geocoding (OpenStreetMap / OpenCage, with a Mapbox fallback) is a third-party HTTP
        request issued per watch per refresh. A watch that has not moved echoes the *identical*
        lat/lng every poll -- very common, since an asleep/offline watch just re-serves its last
        stored `deviceList` fix -- so re-geocoding the same coordinates is pure waste, and both
        OpenStreetMap and OpenCage rate-limit. Cache the resolved address per watch keyed on its
        exact fix and reuse it while the fix is unchanged, only hitting the network on a real
        position change. Exact-match (not rounded): any change at all re-geocodes, so a watch that
        moved can never show a stale address.
        """
        lat, lng = self.lat, self.lng
        if not (lat and lng):
            return
        cached = self._geocode_cache.get(wuid)
        if cached and cached[0] == lat and cached[1] == lng:
            # Same fix as the previous refresh -> reuse the resolved address, skip the request.
            self.location_name, self.licence = cached[2], cached[3]
            self._log.debug("geocode cache hit for ...%s (fix unchanged) -- skipping reverse-geocode", wuid[25:])
            return
        # Clear any carried-over value first: `location_name`/`licence` are shared instance
        # attributes that are NOT reset per watch, and the providers only assign `location_name`
        # on a successful lookup (OpenStreetMap only when the response has an address, Mapbox only
        # when it has features). Without this reset, a provider that returns no address would leave
        # the previous watch's label in place -- and we would then cache that wrong label.
        self.location_name = None
        self.licence = None
        if self._maps == MAPS[1]:
            await self.opencagedata()
        elif self._maps == MAPS[0]:
            await self.openstreetmap()
        else:
            return  # no recognized maps provider -> nothing fetched, nothing to cache
        # Only cache a genuinely-resolved address. A failed/empty lookup leaves `location_name`
        # None and stays uncached, so the next poll retries it (catching transient provider
        # hiccups) instead of serving -- and pinning -- a blank for as long as the watch sits still.
        if self.location_name is not None:
            self._geocode_cache[wuid] = (lat, lng, self.location_name, self.licence)

    async def mapbox(self) -> None:
        """Get mapbox information for the location."""
        language = resolve_language(self._entry)
        # Reuse the shared hass session instead of spinning up (and tearing down) a fresh
        # ClientSession per geocode call -- this runs once per watch per refresh on the maps path.
        session = aiohttp_client.async_get_clientsession(self.hass)
        url = URL_MAPBOX.format(self.lng, self.lat, API_KEY_MAPBOX, language)
        async with session.get(url) as response:
            data = await response.json()
            if data["features"]:
                self.location_name = data["features"][0]["place_name"]
            self.licence = data["attribution"]

    async def opencagedata(self) -> None:
        """Get opencagedata.com information for the location.."""
        try:
            async with OpenCageGeocodeUA(self._opencage_apikey) as geocoder:
                results: list[Any] = await geocoder.reverse_geocode_async(
                    self.lat, self.lng, no_annotations=1, pretty=1, no_record=1, no_dedupe=1, limit=1, abbrv=1
                )
                self.location_name = results[0]["formatted"]
                self.licence = (await geocoder.licenses_async(self.lat, self.lng))[0]["url"]
                self._log.debug("load address from opencagedata.com")
        except aiohttp.ContentTypeError:
            self._log.debug("error about open.com using mapbox.com")
            await self.mapbox()

    async def openstreetmap(self) -> None:
        """Get OpenStreetMap.org information for the location.."""
        try:
            language = resolve_language(self._entry)
            # Reuse the shared hass session (default maps path -- runs once per watch per refresh).
            session = aiohttp_client.async_get_clientsession(self.hass)
            async with session.get(
                URL_OPENSTREETMAP.format(self.lat, self.lng, language),
                timeout=aiohttp.ClientTimeout(DEFAULT_TIMEOUT),
            ) as response:
                res: dict[str, Any] = await response.json()
                self.licence = res.get(ATTR_TRACKER_LICENCE)
                address: dict[str, str] = res.get(ATTR_TRACKER_ADDR, {})
                if address:
                    self.location_name = res.get("display_name", "")
                    self._log.debug("load address from openstreetmap.org")
        except aiohttp.ContentTypeError:
            self._log.debug("error about openstreetmap.org using mapbox.com")
            await self.mapbox()

    @staticmethod
    def _to_epoch_ms(tm: Any) -> int | None:
        """Normalize a point timestamp to epoch milliseconds, or ``None`` if unusable.

        The `LocHistory` API's `tm` unit is not contractually guaranteed (seconds vs. ms), so
        normalize defensively: a value below 1e12 is treated as epoch *seconds* and scaled up
        (1e12 ms ~= year 2001, well before any real fix), giving the card a `tm` it can always
        pass straight to `new Date(tm)`.
        """
        try:
            value = int(tm)
        except TypeError, ValueError:
            return None
        if value <= 0:
            return None
        return value * 1000 if value < 1_000_000_000_000 else value

    @staticmethod
    def _parse_loc_history(raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize a raw `LocHistory` response into compact point dicts.

        Keeps only points with a usable timestamp and coordinates; everything else (malformed
        entries, the GraphQL envelope) is dropped. `tm` is normalized to epoch milliseconds.
        """
        points: list[dict[str, Any]] = []
        entries = (raw or {}).get("locHistory", {}).get("list", []) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            tm = XploraDataUpdateCoordinator._to_epoch_ms(entry.get("tm"))
            lat, lng = entry.get("lat"), entry.get("lng")
            if tm is None or lat is None or lng is None:
                continue
            points.append(
                {
                    ATTR_HISTORY_TM: tm,
                    ATTR_HISTORY_LAT: float(lat),
                    ATTR_HISTORY_LNG: float(lng),
                    ATTR_HISTORY_RAD: entry.get("rad"),
                    ATTR_HISTORY_LOCATE_TYPE: entry.get("locateType"),
                    ATTR_HISTORY_POI: entry.get("poi"),
                    ATTR_HISTORY_ADDR: entry.get("addr"),
                    ATTR_HISTORY_CITY: entry.get("city"),
                }
            )
        return points

    def _history_tz(self) -> str | None:
        """The watch's configured timezone string (falls back to HA's)."""
        return self._entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone

    def _history_tzinfo(self) -> ZoneInfo | None:
        """ZoneInfo for the watch tz, or None (-> system local) when the string is missing/invalid."""
        tz = self._history_tz()
        if not tz:
            return None
        try:
            return ZoneInfo(tz)
        except Exception:  # noqa: BLE001 -- a bad tz string must never break history
            return None

    def _today_key(self, tzinfo: ZoneInfo | None) -> str:
        """Today's calendar-day key (YYYY-MM-DD) in the watch timezone."""
        return datetime.now(tzinfo).strftime("%Y-%m-%d")

    @staticmethod
    def _day_key_from_ms(ms: int, tzinfo: ZoneInfo | None) -> str:
        """Calendar-day key (YYYY-MM-DD) for an epoch-ms timestamp, in the watch timezone."""
        return datetime.fromtimestamp(ms / 1000, tzinfo).strftime("%Y-%m-%d")

    @staticmethod
    def _day_key_to_date_param(day_key: str, tzinfo: ZoneInfo | None) -> int | None:
        """Epoch *seconds* at noon of `day_key` in the watch tz -- the API `date` arg for that day."""
        try:
            year, month, day = (int(part) for part in day_key.split("-"))
            return int(datetime(year, month, day, 12, 0, 0, tzinfo=tzinfo).timestamp())
        except TypeError, ValueError:
            return None

    async def _fetch_loc_history(self, wuid: str) -> None:
        """Refresh TODAY's location track for `wuid` -- the only day fetched on the regular poll.

        Today is always fetched fresh (its track is still being written). Past days are immutable
        and fetched lazily/cached on demand via `async_fetch_history_day` (the websocket command the
        card calls when a past date is picked). Auth / rate-limit / connection failures propagate so
        the caller's recovery runs; a benign history error is swallowed inside the day fetch.
        """
        await self.async_fetch_history_day(wuid, self._today_key(self._history_tzinfo()), force=True)

    async def async_fetch_history_day(self, wuid: str, day_key: str, *, force: bool = False) -> list[dict[str, Any]]:
        """Return one day's points, hitting the network only when needed.

        The caching rule (the whole point of the day buckets): TODAY is always fetched fresh (it is
        still changing); a PAST day is served from the Store once cached and only hits the network on
        its first view (or when `force`). An empty result is cached too, so an empty past day is not
        re-requested. Returns the day's points (ascending by `tm`); on a benign fetch error returns
        whatever is already cached for that day. Auth/rate-limit/connection errors propagate.
        """
        tzinfo = self._history_tzinfo()
        today = self._today_key(tzinfo)
        cached = self._loc_history.get(wuid, {})
        if not force and day_key != today and day_key in cached:
            return list(cached[day_key])  # immutable past day already cached -> no network
        # Day 0 (today) keeps the proven `date=None` call; a past day passes its explicit epoch date.
        date_param = None if day_key == today else self._day_key_to_date_param(day_key, tzinfo)
        try:
            # Routed through the centralized gate so a single expired token is recovered ONCE here
            # at the source (bounded refresh -> retry) instead of surfacing to every caller
            # (websocket, 01:00 auto-fetch, `fetch_history` service) to recover independently.
            raw = await self._with_recovery(
                lambda: self.controller.getWatchLocHistory(wuid, date=date_param, tz=self._history_tz(), limit=LOC_HISTORY_FETCH_LIMIT)
            )
        except RateLimitError, XploraConnectionError, AuthError:
            raise
        except Error as err:
            self._log.debug("location-history fetch failed for ...%s day %s (ignored): %s", wuid[25:], day_key, err)
            return list(cached.get(day_key, []))
        points = sorted(self._parse_loc_history(raw), key=lambda p: p[ATTR_HISTORY_TM])
        # Debug breadcrumb: the line to read when verifying the `date`/`tm` semantics -- it shows the
        # request params and the FIRST raw `tm` (so seconds-vs-ms is obvious). Coordinates are
        # deliberately NOT logged (location is sensitive); only counts/timestamps.
        if self._log.isEnabledFor(logging.DEBUG):
            raw_list = (raw or {}).get("locHistory", {}).get("list", []) or []
            sample_raw_tm = raw_list[0].get("tm") if raw_list and isinstance(raw_list[0], dict) else None
            self._log.debug(
                "LocHistory ...%s day %s (date=%s, tz=%s) -> %d raw / %d parsed; first tm raw=%s",
                wuid[25:],
                day_key,
                date_param,
                self._history_tz(),
                len(raw_list),
                len(points),
                sample_raw_tm,
            )
        await self._store_day(wuid, day_key, points, tzinfo)
        return points

    async def _store_day(self, wuid: str, day_key: str, points: list[dict[str, Any]], tzinfo: ZoneInfo | None) -> None:
        """Replace `day_key`'s bucket with `points`, prune buckets past retention, persist on change."""
        days = self._loc_history.setdefault(wuid, {})
        changed = days.get(day_key) != points
        days[day_key] = points
        # Drop buckets older than retention (lexicographic compare works on YYYY-MM-DD).
        cutoff = (datetime.now(tzinfo) - timedelta(days=self._resolved.history_retention_days)).strftime("%Y-%m-%d")
        for stale_key in [k for k in days if k < cutoff]:
            del days[stale_key]
            changed = True
        if changed:
            await self._persist_loc_history()

    def _all_points(self, wuid: str) -> list[dict[str, Any]]:
        """All retained points for `wuid` across its day buckets, ascending by `tm`."""
        flat = [p for bucket in self._loc_history.get(wuid, {}).values() for p in bucket]
        flat.sort(key=lambda p: p[ATTR_HISTORY_TM])
        return flat

    def cached_history_days(self, wuid: str) -> list[str]:
        """Day keys (YYYY-MM-DD) this watch has cached, ascending.

        Surfaced to the card so its selector can offer archived days (built up by the daily
        `fetch_history` service) alongside the always-available recent days.
        """
        return sorted(self._loc_history.get(wuid, {}).keys())

    def history_yesterday_key(self) -> str:
        """Yesterday's day key (YYYY-MM-DD) in the watch timezone -- the `fetch_history` default."""
        return (datetime.now(self._history_tzinfo()) - timedelta(days=1)).strftime("%Y-%m-%d")

    def setup_history_scheduler(self) -> None:
        """Register a daily listener to auto-fetch yesterday's history (if opted in).

        Called once from ``async_setup_entry`` after the coordinator is initialized; a no-op when
        ``auto_fetch_history`` is off. Cancelling any prior subscription first keeps the method
        idempotent so a re-invocation can never leak an un-cancellable timer.
        """
        if not self._resolved.auto_fetch_history:
            return
        self.async_teardown()  # idempotent: never stack two listeners / leak the old cancel handle
        self._cancel_history_scheduler = async_track_time_change(
            self.hass, self._async_auto_fetch_yesterday, hour=AUTO_FETCH_HISTORY_HOUR, minute=0, second=0
        )
        self._log.debug("History auto-fetch scheduler registered (fires at %02d:00 local time)", AUTO_FETCH_HISTORY_HOUR)

    async def _async_auto_fetch_yesterday(self, _now: datetime) -> None:
        """Fetch yesterday's location track for each configured watch, then refresh entities.

        Scheduled via ``async_track_time_change`` at ``AUTO_FETCH_HISTORY_HOUR`` in Home Assistant's
        local time; the day itself (``history_yesterday_key``) is resolved in the watch timezone --
        the same default the manual ``fetch_history`` service uses, so the two agree except when the
        watch timezone differs sharply from HA's. ``async_fetch_history_day`` already serves an
        already-cached past day from the Store without a network call (``force=False``), so this is a
        no-op for days already archived -- no separate pre-check needed.

        Errors are handled like the ``fetch_history`` service rather than propagating out of the
        timer callback: a refused/expired token (or a transient rate-limit/connection failure) is
        logged once and stops the run -- the next nightly run retries -- instead of hammering the
        endpoint for every watch and surfacing an unhandled-exception traceback.
        """
        yesterday = self.history_yesterday_key()
        try:
            for wuid in self._configured_wuids:
                self._log.debug("Auto-fetching history for watch %s, day %s", wuid, yesterday)
                await self.async_fetch_history_day(wuid, yesterday)
        except AuthError:
            self._log.warning("Auto-fetch history: Xplora session expired; skipping until the next login")
        except (RateLimitError, XploraConnectionError) as err:
            self._log.warning("Auto-fetch history aborted (%s): %s", type(err).__name__, err)
        # Push the (possibly) refreshed cache to the entities so the history sensor's day list /
        # count updates -- matching the `fetch_history` service.
        self.async_update_listeners()

    def async_teardown(self) -> None:
        """Cancel the history auto-fetch scheduler, if active.

        Registered via ``entry.async_on_unload`` so it runs on every unload/reload before the
        coordinator is discarded; also called by ``setup_history_scheduler`` to stay idempotent.
        """
        if self._cancel_history_scheduler is not None:
            self._cancel_history_scheduler()
            self._cancel_history_scheduler = None
            self._log.debug("History auto-fetch scheduler cancelled")

    def _bounded_history(self, wuid: str) -> tuple[list[dict[str, Any]], int]:
        """Return the bounded recent slice the sensor exposes, plus the full retained count.

        The slice is capped to `LOC_HISTORY_ATTR_WINDOW_HOURS` and `LOC_HISTORY_ATTR_MAX_POINTS`
        (most recent points) so the entity attribute stays small; full per-day sets are reached via
        `async_fetch_history_day` (the websocket command).
        """
        full = self._all_points(wuid)
        cutoff = int(datetime.now().timestamp() * 1000) - LOC_HISTORY_ATTR_WINDOW_HOURS * 60 * 60 * 1000
        recent = [p for p in full if p[ATTR_HISTORY_TM] >= cutoff]
        return recent[-LOC_HISTORY_ATTR_MAX_POINTS:], len(full)

    def get_data(self, wuid: str, chats: dict[str, Any]) -> dict[str, Any]:
        """Get data for a given wuid."""
        history_points, history_total = self._bounded_history(wuid)
        return {
            wuid: {
                "unreadMsg": self.unread_msg,
                ATTR_BATTERY: self.battery if self.battery != -1 else None,
                "isCharging": self.is_charging if self.battery != -1 else None,
                "isOnline": self.is_online,
                "isSafezone": self.is_safezone,
                "alarm": self.alarm,
                "silent": self.silent,
                "step_day": self._step_day,
                SENSOR_XCOIN: self._xcoin,
                ATTR_TRACKER_LAT: self.lat if self.is_online else None,
                ATTR_TRACKER_LNG: self.lng if self.is_online else None,
                ATTR_TRACKER_POI: self.poi if self.poi else None,
                ATTR_LOCATION_NAME: self.location_name,
                ATTR_TRACKER_IMEI: self.imei,
                "location_accuracy": self.location_accuracy,
                "entity_picture": self.entity_picture,
                "os_version": self.os_version,
                "model": self.model,
                "watch_id": self.watch_id,
                "locateType": self.locate_type,
                "lastTrackTime": self.last_track_time,
                ATTR_TRACKER_LICENCE: self.licence,
                SENSOR_MESSAGE: chats,
                # Bounded recent slice for the sensor attribute; the full retained set (which can
                # exceed the app's ~3-day window) lives only in the history Store.
                ATTR_LOCATION_HISTORY: {"points": history_points, "total": history_total},
            }
        }

    async def message_data(self, wuid: str, message_limit: int, remove_message: bool) -> dict[str, Any]:
        """Fetch message chats from Xplora."""
        watch_entry = {}
        if self.data:
            watch_entry.update(self.data)
        self._log.debug("Fetch message data from Xplora: %s", wuid[25:])
        auto_mark_read = self._resolved.auto_mark_read
        res_chats = await self.controller.getWatchChatsRaw(
            wuid, limit=message_limit, show_del_msg=remove_message, mark_as_read=auto_mark_read
        )
        if isinstance(res_chats, ChatsNew):
            res_chats = res_chats.to_dict()
        chats = ChatsNew.from_dict(res_chats).to_dict()
        watch_entry.update({wuid: {SENSOR_MESSAGE: chats}})
        self.data = watch_entry
        return res_chats
