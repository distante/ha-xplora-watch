"""Support for Xplora® Watch Version 2 send/read message, manually refresh and shutdown.

Services are targeted at Home Assistant **devices** (ADR 0003). Each account's copy of a watch
is its own HA device (named ``"<Ward> Watch (<account token>)"``, ADR 0002), so a single device
pick identifies both the account (config entry / coordinator) and the watch (``wuid``) -- replacing
the old bespoke ``user`` + ``target`` selectors and the magic ``all`` string. The handler resolves
each selected device (or every Xplora device in a targeted area) back to its ``(account, wuid)``;
account-level services (``logout``) act on the config entry behind the device. Control actions stay
gated to a watch's primary Guardian (ADR 0001) per resolved ``wuid``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .config import resolve
from .const import (
    ATTR_ALARM,
    ATTR_SERVICE_ALARM_ID,
    ATTR_SERVICE_CREATE_ALARM,
    ATTR_SERVICE_CREATE_SILENT,
    ATTR_SERVICE_DATE,
    ATTR_SERVICE_DELETE_ALARM,
    ATTR_SERVICE_DELETE_MSG,
    ATTR_SERVICE_DELETE_SILENT,
    ATTR_SERVICE_ENABLED,
    ATTR_SERVICE_END,
    ATTR_SERVICE_FETCH_HISTORY,
    ATTR_SERVICE_LOGOUT,
    ATTR_SERVICE_MSG,
    ATTR_SERVICE_MSGID,
    ATTR_SERVICE_NAME,
    ATTR_SERVICE_READ_MSG,
    ATTR_SERVICE_REBOOT,
    ATTR_SERVICE_REFRESH_FUNCTIONS,
    ATTR_SERVICE_SEE,
    ATTR_SERVICE_SEND_MSG,
    ATTR_SERVICE_SET_ALARM_ENABLED,
    ATTR_SERVICE_SET_SILENT_ENABLED,
    ATTR_SERVICE_SHUTDOWN,
    ATTR_SERVICE_SILENT_ID,
    ATTR_SERVICE_START,
    ATTR_SERVICE_TURN_ALL_ALARMS_OFF,
    ATTR_SERVICE_TURN_ALL_ALARMS_ON,
    ATTR_SERVICE_TURN_ALL_SILENTS_OFF,
    ATTR_SERVICE_TURN_ALL_SILENTS_ON,
    ATTR_SERVICE_UPDATE_ALARM,
    ATTR_SERVICE_UPDATE_SILENT,
    ATTR_SERVICE_WEEKDAYS,
    ATTR_SILENT,
    DOMAIN,
    SENSOR_MESSAGE,
)
from .coordinator import XploraDataUpdateCoordinator
from .helper import (
    chat_media_cached,
    encoded_base64_string_to_file,
    encoded_base64_string_to_mp3_file,
    time_str_to_minutes,
    weekdays_to_week_repeat,
)
from .log import Log
from .pyxplora_api.exception_classes import AuthError, RateLimitError
from .pyxplora_api.exception_classes import ConnectionError as XploraConnectionError
from .pyxplora_api.pyxplora_api_async import FetchError


def _target_schema(extra: dict[Any, Any] | None = None) -> vol.Schema:
    """Schema for a device-targeted service: HA ``device_id`` / ``area_id`` / ``entity_id`` plus fields.

    The watch is chosen in the UI via the ``device_id`` field (a ``device`` selector filtered to this
    integration -- see ``services.yaml``). ``device_id`` is ``Optional`` here (not required) because
    programmatic callers -- the bundled Lovelace card -- instead pass ``entity_id``, and an empty
    resolution is rejected at runtime by ``XploraService._accounts`` with a friendly
    ``ServiceValidationError`` rather than a schema error. ``area_id`` / ``entity_id`` are accepted
    from YAML automations and the card; both resolve to the watch's device below.
    """
    fields: dict[Any, Any] = {
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ENTITY_ID): vol.All(cv.ensure_list, [cv.string]),
    }
    if extra:
        fields.update(extra)
    return vol.Schema(fields, extra=vol.ALLOW_EXTRA)


BASE_SHUTDOWN_SERVICE_SCHEMA = _target_schema()
BASE_REBOOT_SERVICE_SCHEMA = _target_schema()
BASE_LOGOUT_SERVICE_SCHEMA = _target_schema()
BASE_DELETE_MESSAGE_SERVICE_SCHEMA = _target_schema({vol.Required(ATTR_SERVICE_MSGID): cv.string})
BASE_READ_MESSAGE_SERVICE_SCHEMA = _target_schema()
BASE_SEND_MESSAGE_SERVICE_SCHEMA = _target_schema({vol.Required(ATTR_SERVICE_MSG): cv.string})
BASE_SEE_SERVICE_SCHEMA = _target_schema()
# On-demand refresh of alarms/silent-times/safe-zones (the "functions" data that has its own,
# default-off poll interval).
BASE_REFRESH_FUNCTIONS_SERVICE_SCHEMA = _target_schema()
# Fetch + cache one past day's location track (default: yesterday). `date` is an optional
# "YYYY-MM-DD" override; intended to be automated daily so HA archives days beyond the watch's window.
BASE_FETCH_HISTORY_SERVICE_SCHEMA = _target_schema({vol.Optional(ATTR_SERVICE_DATE): cv.string})
# Alarm / silent-time CRUD. The device target(s) select the watch(es); `start`/`end` are "HH:MM"
# strings and `weekdays` is a list of canonical day keys (see `WEEKDAY_KEYS`) -- both converted before
# the call.
BASE_CREATE_ALARM_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_START): cv.string,
        vol.Required(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SERVICE_NAME, default=""): cv.string,
    }
)
BASE_UPDATE_ALARM_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_ALARM_ID): cv.string,
        vol.Optional(ATTR_SERVICE_START): cv.string,
        vol.Optional(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SERVICE_NAME): cv.string,
    }
)
BASE_DELETE_ALARM_SERVICE_SCHEMA = _target_schema({vol.Required(ATTR_SERVICE_ALARM_ID): cv.string})
BASE_SET_ALARM_ENABLED_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_ALARM_ID): cv.string,
        vol.Required(ATTR_SERVICE_ENABLED): cv.boolean,
    }
)
BASE_CREATE_SILENT_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_START): cv.string,
        vol.Required(ATTR_SERVICE_END): cv.string,
        vol.Required(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
    }
)
BASE_UPDATE_SILENT_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_SILENT_ID): cv.string,
        vol.Optional(ATTR_SERVICE_START): cv.string,
        vol.Optional(ATTR_SERVICE_END): cv.string,
        vol.Optional(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
    }
)
BASE_DELETE_SILENT_SERVICE_SCHEMA = _target_schema({vol.Required(ATTR_SERVICE_SILENT_ID): cv.string})
BASE_SET_SILENT_ENABLED_SERVICE_SCHEMA = _target_schema(
    {
        vol.Required(ATTR_SERVICE_SILENT_ID): cv.string,
        vol.Required(ATTR_SERVICE_ENABLED): cv.boolean,
    }
)
# Bulk enable/disable. No per-entry id: the handler enumerates every alarm / silent-time window on
# the target watch(es) itself, so only the device target is required.
BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA = _target_schema()
BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA = _target_schema()


@dataclass(slots=True)
class _AccountTargets:
    """One account's slice of a service call: its coordinator/logger plus the watches targeted on it.

    A single call may target devices belonging to several accounts (multi-select or an area); the
    resolver groups them so each handler runs its existing per-account logic once per account.
    """

    entry_id: str
    log: Log
    coordinator: XploraDataUpdateCoordinator
    wuids: list[str] = field(default_factory=list)


def _xplora_device_ids(hass: HomeAssistant, data: dict[str, Any]) -> list[str]:
    """Every device id referenced by a call's ``device_id`` / ``area_id`` / ``entity_id`` target.

    Order-preserving: explicitly-picked ``device_id``s first, then the device behind each
    ``entity_id``, then the Xplora devices in each ``area_id`` (devices whose identifiers carry our
    ``DOMAIN``, so a non-Xplora device sharing the area is never dragged in). Duplicates are dropped.
    ``entity_id`` is how the bundled Lovelace card targets a watch (it binds entities, not devices).
    """
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    device_ids: list[str] = []

    def _add(device_id: str) -> None:
        if device_id not in device_ids:
            device_ids.append(device_id)

    for device_id in cv.ensure_list(data.get(ATTR_DEVICE_ID, [])):
        _add(device_id)
    for entity_id in cv.ensure_list(data.get(ATTR_ENTITY_ID, [])):
        entry = entities.async_get(entity_id)
        if entry and entry.device_id:
            _add(entry.device_id)
    for area_id in cv.ensure_list(data.get(ATTR_AREA_ID, [])):
        for device in dr.async_entries_for_area(registry, area_id):
            if any(domain == DOMAIN for domain, _ in device.identifiers):
                _add(device.id)
    return device_ids


def _resolve_device(hass: HomeAssistant, device_id: str) -> tuple[str, XploraDataUpdateCoordinator, str] | None:
    """Resolve one HA device id to ``(entry_id, coordinator, wuid)``; ``None`` if it isn't an Xplora watch.

    A device's ``config_entries`` give the candidate accounts; the matching coordinator is the one
    registered in ``hass.data[DOMAIN]``. The ``wuid`` is recovered from the device's
    ``(DOMAIN, "{entry.unique_id}_{wuid}")`` identifier by matching it against the coordinator's known
    wuids (ADR 0003) -- not a naive ``rsplit("_")``, since the account's ``unique_id`` (an email /
    phone number) may itself contain separators.
    """
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return None
    for entry_id in device.config_entries:
        coordinator: XploraDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None or getattr(coordinator, "controller", None) is None:
            continue
        prefix = coordinator._entry.unique_id
        for wuid in coordinator.controller.getWatchUserIDs():
            if (DOMAIN, f"{prefix}_{wuid}") in device.identifiers:
                return entry_id, coordinator, wuid
    return None


def _log_api_error(action: str, entry_id: str, error: Exception) -> None:
    """Log a clean, per-type warning when a manual service call hits a recoverable controller error.

    Service controller calls route through the coordinator's centralized single-flight recovery
    gate (`_with_recovery`), so an expired token is recovered there (bounded refresh -> at-most-one
    re-login -> one retry) before it ever reaches this handler. An exception arriving here therefore
    means recovery was either not applicable (a 429 / connection drop -- those bypass the gate) or
    was attempted and exhausted. The message is tailored per type so the user sees what actually
    happened instead of one generic "session expired" for everything:

    - `RateLimitError` (HTTP 429): never retried -- retrying inside a rate-limit window worsens a
      ban -- so the call is abandoned for this run.
    - `XploraConnectionError`: a transport failure; the command's fate is unknown.
    - `AuthError`: the token is expired account-wide and the bounded recovery could not restore it;
      the next coordinator poll will, so the caller simply retries later. Services stop iterating
      the remaining watches rather than repeating the doomed call.
    """
    log = Log(entry_id=entry_id)
    if isinstance(error, RateLimitError):
        log.warning("%s skipped: Xplora API rate limit (HTTP 429); not retried to avoid a ban -- please retry later.", action)
    elif isinstance(error, XploraConnectionError):
        log.warning("%s failed: could not reach the Xplora server (%s) -- please retry.", action, error)
    else:
        log.warning("%s skipped: Xplora session token expired; it will refresh on the next update -- please retry.", action)


@callback
async def async_setup_services(hass: HomeAssistant, entry_id: str) -> None:
    """Set up services for Xplora® Watch integration."""

    delete_message_from_app_service = XploraDeleteMessageFromAppService(hass, entry_id)
    shutdown_service = XploraShutdownService(hass, entry_id)
    reboot_service = XploraRebootService(hass, entry_id)
    logout_service = XploraLogoutService(hass, entry_id)
    sensor_update_service = XploraMessageSensorUpdateService(hass, entry_id)
    notify_service = XploraMessageService(hass, entry_id)
    see_service = XploraSeeService(hass, entry_id)
    refresh_functions_service = XploraRefreshFunctionsService(hass, entry_id)
    fetch_history_service = XploraFetchHistoryService(hass, entry_id)
    alarm_service = XploraAlarmService(hass, entry_id)
    silent_service = XploraSilentService(hass, entry_id)

    async def async_see(service: ServiceCall) -> None:
        await see_service.async_see(kwargs=dict(service.data))

    async def async_refresh_functions(service: ServiceCall) -> None:
        await refresh_functions_service.async_refresh_functions(kwargs=dict(service.data))

    async def async_fetch_history(service: ServiceCall) -> None:
        await fetch_history_service.async_fetch_history(kwargs=dict(service.data))

    async def async_delete_message_from_app(service: ServiceCall) -> None:
        await delete_message_from_app_service.async_delete_message_from_app(kwargs=dict(service.data))

    async def async_send_message(service: ServiceCall) -> None:
        await notify_service.async_send_message(kwargs=dict(service.data))

    async def async_read_message(service: ServiceCall) -> None:
        await sensor_update_service.async_read_message(kwargs=dict(service.data))

    async def async_shutdown(service: ServiceCall) -> None:
        await shutdown_service.async_shutdown(kwargs=dict(service.data))

    async def async_reboot(service: ServiceCall) -> None:
        await reboot_service.async_reboot(kwargs=dict(service.data))

    async def async_logout(service: ServiceCall) -> None:
        await logout_service.async_logout(kwargs=dict(service.data))

    async def async_create_alarm(service: ServiceCall) -> None:
        await alarm_service.async_create(kwargs=dict(service.data))

    async def async_update_alarm(service: ServiceCall) -> None:
        await alarm_service.async_update(kwargs=dict(service.data))

    async def async_delete_alarm(service: ServiceCall) -> None:
        await alarm_service.async_delete(kwargs=dict(service.data))

    async def async_set_alarm_enabled(service: ServiceCall) -> None:
        await alarm_service.async_set_enabled(kwargs=dict(service.data))

    async def async_create_silent(service: ServiceCall) -> None:
        await silent_service.async_create(kwargs=dict(service.data))

    async def async_update_silent(service: ServiceCall) -> None:
        await silent_service.async_update(kwargs=dict(service.data))

    async def async_delete_silent(service: ServiceCall) -> None:
        await silent_service.async_delete(kwargs=dict(service.data))

    async def async_set_silent_enabled(service: ServiceCall) -> None:
        await silent_service.async_set_enabled(kwargs=dict(service.data))

    async def async_turn_all_alarms_on(service: ServiceCall) -> None:
        await alarm_service.async_set_all_enabled(True, kwargs=dict(service.data))

    async def async_turn_all_alarms_off(service: ServiceCall) -> None:
        await alarm_service.async_set_all_enabled(False, kwargs=dict(service.data))

    async def async_turn_all_silents_on(service: ServiceCall) -> None:
        await silent_service.async_set_all_enabled(True, kwargs=dict(service.data))

    async def async_turn_all_silents_off(service: ServiceCall) -> None:
        await silent_service.async_set_all_enabled(False, kwargs=dict(service.data))

    hass.services.async_register(DOMAIN, ATTR_SERVICE_SHUTDOWN, async_shutdown, schema=BASE_SHUTDOWN_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_REBOOT, async_reboot, schema=BASE_REBOOT_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_LOGOUT, async_logout, schema=BASE_LOGOUT_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_DELETE_MSG, async_delete_message_from_app, schema=BASE_DELETE_MESSAGE_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_READ_MSG, async_read_message, schema=BASE_READ_MESSAGE_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_SEND_MSG, async_send_message, schema=BASE_SEND_MESSAGE_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_SEE, async_see, schema=BASE_SEE_SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_REFRESH_FUNCTIONS, async_refresh_functions, schema=BASE_REFRESH_FUNCTIONS_SERVICE_SCHEMA
    )
    hass.services.async_register(DOMAIN, ATTR_SERVICE_FETCH_HISTORY, async_fetch_history, schema=BASE_FETCH_HISTORY_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_CREATE_ALARM, async_create_alarm, schema=BASE_CREATE_ALARM_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_UPDATE_ALARM, async_update_alarm, schema=BASE_UPDATE_ALARM_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_DELETE_ALARM, async_delete_alarm, schema=BASE_DELETE_ALARM_SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_SET_ALARM_ENABLED, async_set_alarm_enabled, schema=BASE_SET_ALARM_ENABLED_SERVICE_SCHEMA
    )
    hass.services.async_register(DOMAIN, ATTR_SERVICE_CREATE_SILENT, async_create_silent, schema=BASE_CREATE_SILENT_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_UPDATE_SILENT, async_update_silent, schema=BASE_UPDATE_SILENT_SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, ATTR_SERVICE_DELETE_SILENT, async_delete_silent, schema=BASE_DELETE_SILENT_SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_SET_SILENT_ENABLED, async_set_silent_enabled, schema=BASE_SET_SILENT_ENABLED_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_TURN_ALL_ALARMS_ON, async_turn_all_alarms_on, schema=BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_TURN_ALL_ALARMS_OFF, async_turn_all_alarms_off, schema=BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_TURN_ALL_SILENTS_ON, async_turn_all_silents_on, schema=BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, ATTR_SERVICE_TURN_ALL_SILENTS_OFF, async_turn_all_silents_off, schema=BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Xplora® Watch send_message services."""
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_SHUTDOWN)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_REBOOT)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_LOGOUT)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_DELETE_MSG)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_READ_MSG)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_SEND_MSG)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_SEE)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_REFRESH_FUNCTIONS)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_CREATE_ALARM)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_UPDATE_ALARM)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_DELETE_ALARM)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_SET_ALARM_ENABLED)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_CREATE_SILENT)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_UPDATE_SILENT)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_DELETE_SILENT)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_SET_SILENT_ENABLED)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_TURN_ALL_ALARMS_ON)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_TURN_ALL_ALARMS_OFF)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_TURN_ALL_SILENTS_ON)
    hass.services.async_remove(DOMAIN, ATTR_SERVICE_TURN_ALL_SILENTS_OFF)


class _ApiCallGuard:
    """Mutable flag set by `XploraService._api_call_guard` when it catches a recoverable API error.

    Lets a handler iterating multiple watches `break` after the first failure (so it stops hammering
    the remaining ones once the token is expired / the API is rate-limited -- the ban-defense
    behavior) instead of repeating a call that is doomed for every target.
    """

    __slots__ = ("failed",)

    def __init__(self) -> None:
        """Start un-failed; `_api_call_guard` flips this to True if it catches a recoverable error."""
        self.failed = False


class XploraService:
    """Common base for Xplora® service."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the entry."""
        self._hass = hass
        self._entry_id = entry_id

    @contextmanager
    def _api_call_guard(self, action: str, entry_id: str) -> Iterator[_ApiCallGuard]:
        """Single choke-point for the recoverable-API-error handling every service shares.

        Wrap a controller-call body in ``with self._api_call_guard(action, entry_id) as guard:``.
        The recoverable errors -- ``AuthError`` (raised when the coordinator's bounded single-flight
        recovery in ``_with_recovery`` is exhausted) plus ``RateLimitError`` / ``XploraConnectionError``
        (which bypass that gate) -- are caught here and logged once via ``_log_api_error``. The
        exception tuple lives in this one place, so adding a new recoverable type is a single edit
        instead of 14 parallel ones. Any other exception (a real bug) propagates untouched.

        ``guard.failed`` lets a per-watch loop stop after the first failure; the ``break``/``return``
        decision stays with the caller because it differs per handler (loop vs. single call), e.g.::

            for wuid in targets:
                with self._api_call_guard("Create alarm", entry_id) as guard:
                    await coordinator._with_recovery(lambda: coordinator.controller.add(...))
                if guard.failed:
                    break
        """
        guard = _ApiCallGuard()
        try:
            yield guard
        except (AuthError, RateLimitError, XploraConnectionError) as error:
            _log_api_error(action, entry_id, error)
            guard.failed = True

    def _accounts(self, data: dict[str, Any]) -> list[_AccountTargets]:
        """Resolve a call's device targets into per-account work units.

        Groups every resolved ``(account, wuid)`` by account, preserving target order and de-duping
        watches, so a multi-device or area selection fans out to each account once. Devices that
        aren't Xplora watches (or whose account isn't loaded) are silently skipped. Raises
        ``ServiceValidationError`` when the call resolves to no Xplora watch at all -- so a misfired
        call surfaces a clean message instead of silently doing nothing.
        """
        groups: dict[str, _AccountTargets] = {}
        order: list[str] = []
        for device_id in _xplora_device_ids(self._hass, data):
            resolved = _resolve_device(self._hass, device_id)
            if resolved is None:
                continue
            entry_id, coordinator, wuid = resolved
            group = groups.get(entry_id)
            if group is None:
                group = _AccountTargets(entry_id, Log(entry_id=entry_id), coordinator)
                groups[entry_id] = group
                order.append(entry_id)
            if wuid not in group.wuids:
                group.wuids.append(wuid)
        if not order:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_xplora_device")
        return [groups[entry_id] for entry_id in order]

    def _guardian_targets(
        self,
        coordinator: XploraDataUpdateCoordinator,
        wuids: list[str],
        action: str,
        log: Log,
    ) -> list[str]:
        """Restrict a Guardian-only service call to the watches this account actually guards.

        Shared gate for the reboot, shutdown and alarm/silent-time CRUD handlers. Drops every watch
        the account is only a *Contact* of and returns the rest. Restricting these control actions to
        the watch's Guardian is a client policy (ref:XW-009), not a server rejection.

        Fails open: a watch is dropped only when its role is a *confirmed* Contact; an
        unknown/unresolved role is treated as a Guardian, so incomplete data never blocks a real
        Guardian's control. A warning is logged for each skipped watch. If the account guards none of
        the targeted watches, raises Home Assistant's ``ServiceValidationError`` (localized via the
        ``not_guardian`` key and worded as a client policy), so the control mutation is never sent.

        ``action`` is the short phrase shown to the user for what was blocked (e.g. ``"reboot the
        watch"``); it fills the error's ``{action}`` placeholder and the per-watch warning.
        """
        allowed: list[str] = []
        for wuid in wuids:
            if coordinator.is_confirmed_contact(wuid):
                log.warning(
                    "Skipping '%s' for watch %s: this account is a contact of it, not its primary guardian.",
                    action,
                    wuid,
                )
            else:
                allowed.append(wuid)
        if not allowed:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_guardian",
                translation_placeholders={"action": action},
            )
        return allowed

    async def _refresh_list(self, coordinator: XploraDataUpdateCoordinator, wuid: str, data_key: str) -> None:
        """Re-fetch just the mutated alarm/silent list for one watch and push it to the entities.

        Uses ``async_set_updated_data`` (a single targeted fetch) rather than a full
        ``async_refresh`` poll, keeping the integration off the rate-limit radar as intended.
        """
        if data_key == ATTR_ALARM:
            new_items = await coordinator._with_recovery(lambda: coordinator.controller.getWatchAlarm(wuid))
        else:
            new_items = await coordinator._with_recovery(lambda: coordinator.controller.getSilentTime(wuid))
        if isinstance(new_items, FetchError):
            Log(entry_id=self._entry_id).warning("%s: %s", new_items.operation, new_items.message)
            return
        data = coordinator.data or {}
        if wuid in data:
            data[wuid][data_key] = new_items
            coordinator.async_set_updated_data(data)


class XploraSeeService(XploraService):
    """Create a service that can be update information from Watch."""

    async def async_see(self, **kwargs: Any) -> None:
        """Update watch information."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            account.log.debug("%s: update all information: %s", account.coordinator.controller.getUserName(), ", ".join(account.wuids))
            await account.coordinator.async_update_xplora_data(account.wuids)


class XploraRefreshFunctionsService(XploraService):
    """Create a service that refreshes the alarm/silent/safezone data on demand."""

    async def async_refresh_functions(self, **kwargs: Any) -> None:
        """Force a functions (alarm/silent/safezone) refresh, bypassing the functions interval."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            account.log.debug("%s: refresh functions data: %s", account.coordinator.controller.getUserName(), ", ".join(account.wuids))
            await account.coordinator.async_refresh_functions(account.wuids)


class XploraFetchHistoryService(XploraService):
    """Fetch and cache one past day's location history on demand (default: yesterday).

    The watch's API only serves the last few days, so automating this daily lets Home Assistant
    keep a long-term archive: each run stores that day's complete track in the per-day history Store.
    """

    async def async_fetch_history(self, **kwargs: Any) -> None:
        """Fetch `date` (default yesterday) for the target watch(es), forcing a fresh pull + cache."""
        data = kwargs["kwargs"]
        date = data.get(ATTR_SERVICE_DATE)
        for account in self._accounts(data):
            coordinator = account.coordinator
            day_key = (date or "").strip() or coordinator.history_yesterday_key()
            for watch_id in account.wuids:
                account.log.debug("fetch location history for %s on %s", watch_id, day_key)
                with self._api_call_guard("Fetch history", account.entry_id) as guard:
                    await coordinator.async_fetch_history_day(watch_id, day_key, force=True)
                if guard.failed:
                    break
            # Push the refreshed cache to the entities so the sensor's day list / count update.
            coordinator.async_update_listeners()


class XploraDeleteMessageFromAppService(XploraService):
    """Create a service that can be remove message from Watch."""

    async def async_delete_message_from_app(self, **kwargs: Any) -> None:
        """Delete a message to one Watch."""
        data = kwargs["kwargs"]
        accounts = self._accounts(data)
        msg_id = str(data[ATTR_SERVICE_MSGID]).strip()
        if not msg_id:
            accounts[0].log.warning("You must provide an ID!")
            return
        for account in accounts:
            coordinator = account.coordinator
            for watch_id in account.wuids:
                account.log.debug("remove message %s from %s", msg_id, watch_id)
                with self._api_call_guard("Delete message", account.entry_id) as guard:
                    if not await coordinator._with_recovery(
                        lambda: coordinator.controller.deleteMessageFromApp(wuid=watch_id, msgId=msg_id)
                    ):
                        account.log.error("Message cannot deleted!")
                if guard.failed:
                    break


class XploraMessageService(XploraService):
    """Create a service that can be send message to Watch."""

    async def async_send_message(self, **kwargs: Any) -> None:
        """Send message to Watch."""
        data = kwargs["kwargs"]
        accounts = self._accounts(data)
        msg = str(data[ATTR_SERVICE_MSG]).strip()
        if not msg:
            accounts[0].log.warning("Message is empty!")
            return
        for account in accounts:
            coordinator = account.coordinator
            for watch_id in account.wuids:
                account.log.debug("Sending message '%s' to '%s'", msg, watch_id)
                with self._api_call_guard("Send message", account.entry_id) as guard:
                    if not await coordinator._with_recovery(lambda: coordinator.controller.sendText(text=msg, wuid=watch_id)):
                        account.log.error("Message cannot send!")
                if guard.failed:
                    break


class XploraMessageSensorUpdateService(XploraService):
    """Create a service that can be used to read messages from Watch."""

    coordinator: XploraDataUpdateCoordinator

    async def async_read_message(self, **kwargs: Any) -> None:
        """Read the messages from account."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            coordinator = account.coordinator
            self.coordinator = coordinator
            old_state: dict[str, Any] = coordinator.data
            resolved = resolve(coordinator._entry.options)
            limit: int = resolved.message
            show_remove_msg = resolved.remove_message
            # Token expired / rate-limited / connection drop mid-read: the guard logs it cleanly (the
            # gate already attempted recovery for an expired token) and we still persist whatever was
            # gathered before the failure, letting the next coordinator poll recover the session.
            with self._api_call_guard("Read messages", account.entry_id):
                for watch in account.wuids:
                    res_chats = await coordinator._with_recovery(lambda: coordinator.message_data(watch, limit, show_remove_msg))
                    if res_chats:
                        for chat in res_chats.get("list") or []:
                            chat_type = chat.get("type")
                            msg_id = chat.get("msgId")
                            if chat_type == "VOICE":
                                await self._fetch_chat_voice(watch, msg_id)
                            elif chat_type == "SHORT_VIDEO":
                                await self._fetch_chat_short_video(watch, msg_id)
                            elif chat_type == "IMAGE":
                                await self._fetch_chat_image(watch, msg_id)
                        new_data_msg: dict[str, Any] = old_state.get(watch, {}) if isinstance(old_state, dict) else {}
                        if new_data_msg:
                            new_data_msg.update({SENSOR_MESSAGE: res_chats})
                            old_state.update({watch: new_data_msg})
            await coordinator.async_update_xplora_data(new_data=old_state)

    async def _fetch_chat_voice(self, watch_id: str, msg_id: str) -> None:
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "mp3", "voice"):
            return
        voice = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_chat_voice(watch_id, msg_id))
        if voice:
            await encoded_base64_string_to_mp3_file(self._hass, voice, msg_id)

    async def _fetch_chat_short_video(self, watch_id: str, msg_id: str) -> None:
        # Skip the remote fetch only once BOTH the video and its thumbnail are cached.
        if chat_media_cached(self._hass, msg_id, "mp4", "video") and chat_media_cached(self._hass, msg_id, "jpeg", "video/thumb"):
            return
        video = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_short_video(watch_id, msg_id))
        if video:
            await encoded_base64_string_to_file(self._hass, video, msg_id, "mp4", "video")
        thumb = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_short_video_cover(watch_id, msg_id))
        if thumb:
            await encoded_base64_string_to_file(self._hass, thumb, msg_id, "jpeg", "video/thumb")

    async def _fetch_chat_image(self, watch: str, msg_id: str) -> None:
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "jpeg", "image"):
            return
        image = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_chat_image(watch, msg_id))
        if image:
            await encoded_base64_string_to_file(self._hass, image, msg_id, "jpeg", "image")


class XploraShutdownService(XploraService):
    """Create a service that shuts down Xplora."""

    async def async_shutdown(self, **kwargs: Any) -> None:
        """Turn off watch."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            coordinator = account.coordinator
            for watch in self._guardian_targets(coordinator, account.wuids, "shut down the watch", account.log):
                with self._api_call_guard("Shutdown", account.entry_id) as guard:
                    accepted = await coordinator._with_recovery(lambda: coordinator.controller.shutdown(watch))
                    account.log.debug("Shutdown result: %s", accepted)
                    if not accepted:
                        # False == the backend refused (typically the watch is off/offline).
                        account.log.warning("Shutdown was not accepted for watch %s (it may be off or offline)", watch[25:])
                if guard.failed:
                    return


class XploraRebootService(XploraService):
    """Create a service that reboots a watch (parity with the app's `reboot(uid)` mutation)."""

    async def async_reboot(self, **kwargs: Any) -> None:
        """Reboot watch."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            coordinator = account.coordinator
            for watch in self._guardian_targets(coordinator, account.wuids, "reboot the watch", account.log):
                with self._api_call_guard("Reboot", account.entry_id) as guard:
                    accepted = await coordinator._with_recovery(lambda: coordinator.controller.reboot(watch))
                    account.log.debug("Reboot result: %s", accepted)
                    if not accepted:
                        # False == the backend refused (typically the watch is off/offline).
                        account.log.warning("Reboot was not accepted for watch %s (it may be off or offline)", watch[25:])
                if guard.failed:
                    return


class XploraLogoutService(XploraService):
    """Create a service that logs out the account(s) behind the targeted device(s).

    Account-level, not per-watch: it invalidates the session token shared by every watch on the
    account behind the targeted device. When polling is enabled (`CONF_SCAN_INTERVAL` > 0) the next
    coordinator poll re-logs in automatically, so it also works as a "force re-auth". With polling
    disabled (`CONF_SCAN_INTERVAL` == 0) nothing re-authenticates until the next manual `see`/reload
    -- the account simply stays logged out, which is the expected logout behavior.
    """

    async def async_logout(self, **kwargs: Any) -> None:
        """Log out each account behind the targeted device(s) (one logout per account)."""
        data = kwargs["kwargs"]
        for account in self._accounts(data):
            try:
                acknowledged = await account.coordinator.controller.logout()
                account.log.debug("Logout result (server acknowledged: %s)", acknowledged)
            except (RateLimitError, XploraConnectionError, AuthError) as error:
                # Best-effort: the local token was already cleared inside `logout()`, so the next
                # poll re-logs in regardless. Just surface a clean warning instead of a traceback.
                account.log.warning("Logout could not reach the Xplora server (%s); local session cleared anyway.", type(error).__name__)


class XploraAlarmService(XploraService):
    """Create / update / delete / enable-disable alarms on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new alarm on each target watch."""
        data = kwargs["kwargs"]
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])
        name = data.get(ATTR_SERVICE_NAME, "")
        for account in self._accounts(data):
            coordinator = account.coordinator
            for wuid in self._guardian_targets(coordinator, account.wuids, "change the watch's alarms", account.log):
                with self._api_call_guard("Create alarm", account.entry_id) as guard:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.addAlarmTime(wuid, occur_min, week_repeat, name))
                    account.log.debug("Create alarm on %s: %s", wuid, ok)
                    await self._refresh_list(coordinator, wuid, ATTR_ALARM)
                if guard.failed:
                    return

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing alarm (time, repeat days and/or name)."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None
        name = data.get(ATTR_SERVICE_NAME)
        for account in self._accounts(data):
            coordinator = account.coordinator
            # Gate up front: this handler fires the mutation before the per-watch refresh loop, so the
            # Contact check must run first or the control mutation would leak out before the raise.
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's alarms", account.log)
            with self._api_call_guard("Update alarm", account.entry_id):
                ok = await coordinator._with_recovery(
                    lambda: coordinator.controller.modifyAlarmTime(alarm_id, occur_min, week_repeat, name)
                )
                account.log.debug("Update alarm %s: %s", alarm_id, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete an alarm."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        for account in self._accounts(data):
            coordinator = account.coordinator
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's alarms", account.log)
            with self._api_call_guard("Delete alarm", account.entry_id):
                ok = await coordinator._with_recovery(lambda: coordinator.controller.removeAlarmTime(alarm_id))
                account.log.debug("Delete alarm %s: %s", alarm_id, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable an alarm."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        enabled = data[ATTR_SERVICE_ENABLED]
        for account in self._accounts(data):
            coordinator = account.coordinator
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's alarms", account.log)
            with self._api_call_guard("Set alarm enabled", account.entry_id):
                if enabled:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.setEnableAlarmTime(alarm_id))
                else:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.setDisableAlarmTime(alarm_id))
                account.log.debug("Set alarm %s enabled=%s: %s", alarm_id, enabled, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every alarm on each target watch in one call.

        The current list is fetched FRESH per watch (not read from ``coordinator.data``) so the
        toggle is correct even when functions polling is off and the cached data is stale. One
        ``_api_call_guard`` wraps the whole per-watch unit (fetch + all toggles + entity refresh);
        on the first recoverable failure the loop breaks so we stop hammering the remaining watches.
        """
        data = kwargs["kwargs"]
        action = "Turn all alarms on" if enabled else "Turn all alarms off"
        for account in self._accounts(data):
            coordinator = account.coordinator
            for wuid in self._guardian_targets(coordinator, account.wuids, "change the watch's alarms", account.log):
                with self._api_call_guard(action, account.entry_id) as guard:
                    alarms = await coordinator._with_recovery(lambda: coordinator.controller.getWatchAlarm(wuid))
                    if isinstance(alarms, FetchError):
                        account.log.warning("%s: %s", alarms.operation, alarms.message)
                        continue
                    for alarm in alarms:
                        aid = alarm["id"]
                        if enabled:
                            await coordinator._with_recovery(lambda: coordinator.controller.setEnableAlarmTime(aid))
                        else:
                            await coordinator._with_recovery(lambda: coordinator.controller.setDisableAlarmTime(aid))
                        account.log.debug("Set alarm %s enabled=%s", aid, enabled)
                    await self._refresh_list(coordinator, wuid, ATTR_ALARM)
                if guard.failed:
                    return


class XploraSilentService(XploraService):
    """Create / update / delete / enable-disable silent-time windows on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new silent-time window on each target watch."""
        data = kwargs["kwargs"]
        start = time_str_to_minutes(data[ATTR_SERVICE_START])
        end = time_str_to_minutes(data[ATTR_SERVICE_END])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])
        for account in self._accounts(data):
            coordinator = account.coordinator
            for wuid in self._guardian_targets(coordinator, account.wuids, "change the watch's silent times", account.log):
                with self._api_call_guard("Create silent time", account.entry_id) as guard:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.addSilentTime(wuid, start, end, week_repeat))
                    account.log.debug("Create silent on %s: %s", wuid, ok)
                    await self._refresh_list(coordinator, wuid, ATTR_SILENT)
                if guard.failed:
                    return

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing silent-time window (start, end and/or repeat days)."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        start = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        end = time_str_to_minutes(data[ATTR_SERVICE_END]) if ATTR_SERVICE_END in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None
        for account in self._accounts(data):
            coordinator = account.coordinator
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's silent times", account.log)
            with self._api_call_guard("Update silent time", account.entry_id):
                ok = await coordinator._with_recovery(lambda: coordinator.controller.modifySilentTime(silent_id, start, end, week_repeat))
                account.log.debug("Update silent %s: %s", silent_id, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete a silent-time window."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        for account in self._accounts(data):
            coordinator = account.coordinator
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's silent times", account.log)
            with self._api_call_guard("Delete silent time", account.entry_id):
                ok = await coordinator._with_recovery(lambda: coordinator.controller.removeSilentTime(silent_id))
                account.log.debug("Delete silent %s: %s", silent_id, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable a silent-time window."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        enabled = data[ATTR_SERVICE_ENABLED]
        for account in self._accounts(data):
            coordinator = account.coordinator
            targets = self._guardian_targets(coordinator, account.wuids, "change the watch's silent times", account.log)
            with self._api_call_guard("Set silent enabled", account.entry_id):
                if enabled:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.setEnableSilentTime(silent_id))
                else:
                    ok = await coordinator._with_recovery(lambda: coordinator.controller.setDisableSilentTime(silent_id))
                account.log.debug("Set silent %s enabled=%s: %s", silent_id, enabled, ok)
                for wuid in targets:
                    await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every silent-time window on each target watch in one call.

        Mirror of :meth:`XploraAlarmService.async_set_all_enabled`: fresh per-watch fetch, all calls
        routed through the recovery gate, and the loop breaks on the first recoverable failure.
        """
        data = kwargs["kwargs"]
        action = "Turn all silents on" if enabled else "Turn all silents off"
        for account in self._accounts(data):
            coordinator = account.coordinator
            for wuid in self._guardian_targets(coordinator, account.wuids, "change the watch's silent times", account.log):
                with self._api_call_guard(action, account.entry_id) as guard:
                    silents = await coordinator._with_recovery(lambda: coordinator.controller.getSilentTime(wuid))
                    if isinstance(silents, FetchError):
                        account.log.warning("%s: %s", silents.operation, silents.message)
                        continue
                    for silent in silents:
                        sid = silent["id"]
                        if enabled:
                            await coordinator._with_recovery(lambda: coordinator.controller.setEnableSilentTime(sid))
                        else:
                            await coordinator._with_recovery(lambda: coordinator.controller.setDisableSilentTime(sid))
                        account.log.debug("Set silent %s enabled=%s", sid, enabled)
                    await self._refresh_list(coordinator, wuid, ATTR_SILENT)
                if guard.failed:
                    return
