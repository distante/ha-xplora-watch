"""Support for Xplora® Watch Version 2 send/read message, manually refresh and shutdown."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback

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
    ATTR_SERVICE_TARGET,
    ATTR_SERVICE_TURN_ALL_ALARMS_OFF,
    ATTR_SERVICE_TURN_ALL_ALARMS_ON,
    ATTR_SERVICE_TURN_ALL_SILENTS_OFF,
    ATTR_SERVICE_TURN_ALL_SILENTS_ON,
    ATTR_SERVICE_UPDATE_ALARM,
    ATTR_SERVICE_UPDATE_SILENT,
    ATTR_SERVICE_USER,
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

BASE_SHUTDOWN_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_REBOOT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
# Account-level: logout has no per-watch `target`, only `user` (which encodes the entry id).
# `helper.set_watches` populates the `user` selector and skips the missing `target` field.
BASE_LOGOUT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_DELETE_MESSAGE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_MSGID): cv.string,
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_READ_MESSAGE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_SEND_MESSAGE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_MSG): cv.string,
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_SEE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
# On-demand refresh of alarms/silent-times/safe-zones (the "functions" data that has its own,
# default-off poll interval). Same target/user shape as `see`.
BASE_REFRESH_FUNCTIONS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
# Fetch + cache one past day's location track (default: yesterday). `date` is an optional
# "YYYY-MM-DD" override; intended to be automated daily so HA archives days beyond the watch's window.
BASE_FETCH_HISTORY_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SERVICE_DATE): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)
# Alarm / silent-time CRUD. `target` selects the watch(es); `start`/`end` are "HH:MM" strings and
# `weekdays` is a list of canonical day keys (see `WEEKDAY_KEYS`) -- both converted before the call.
BASE_CREATE_ALARM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_START): cv.string,
        vol.Required(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SERVICE_NAME, default=""): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_UPDATE_ALARM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_ALARM_ID): cv.string,
        vol.Optional(ATTR_SERVICE_START): cv.string,
        vol.Optional(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SERVICE_NAME): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_DELETE_ALARM_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_ALARM_ID): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_SET_ALARM_ENABLED_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_ALARM_ID): cv.string,
        vol.Required(ATTR_SERVICE_ENABLED): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_CREATE_SILENT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_START): cv.string,
        vol.Required(ATTR_SERVICE_END): cv.string,
        vol.Required(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_UPDATE_SILENT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_SILENT_ID): cv.string,
        vol.Optional(ATTR_SERVICE_START): cv.string,
        vol.Optional(ATTR_SERVICE_END): cv.string,
        vol.Optional(ATTR_SERVICE_WEEKDAYS): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_DELETE_SILENT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_SILENT_ID): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_SET_SILENT_ENABLED_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_SILENT_ID): cv.string,
        vol.Required(ATTR_SERVICE_ENABLED): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)
# Bulk enable/disable. No per-entry id: the handler enumerates every alarm / silent-time window on
# the target watch(es) itself, so only the watch + account selectors are required.
BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)
BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SERVICE_TARGET): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SERVICE_USER): vol.All(cv.ensure_list, [cv.string]),
    },
    extra=vol.ALLOW_EXTRA,
)


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
        kwargs = dict(service.data)
        await see_service.async_see(kwargs.get(ATTR_SERVICE_TARGET, ["all"]), kwargs=kwargs)

    async def async_refresh_functions(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await refresh_functions_service.async_refresh_functions(kwargs.get(ATTR_SERVICE_TARGET, ["all"]), kwargs=kwargs)

    async def async_fetch_history(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await fetch_history_service.async_fetch_history(
            kwargs.get(ATTR_SERVICE_TARGET, ["all"]), kwargs.get(ATTR_SERVICE_DATE), kwargs=kwargs
        )

    async def async_delete_message_from_app(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await delete_message_from_app_service.async_delete_message_from_app(
            kwargs[ATTR_SERVICE_MSGID], kwargs[ATTR_SERVICE_TARGET], kwargs=kwargs
        )

    async def async_send_message(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await notify_service.async_send_message(kwargs[ATTR_SERVICE_MSG], kwargs[ATTR_SERVICE_TARGET], kwargs=kwargs)

    async def async_read_message(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await sensor_update_service.async_read_message(kwargs[ATTR_SERVICE_TARGET], kwargs=kwargs)

    async def async_shutdown(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await shutdown_service.async_shutdown(kwargs[ATTR_SERVICE_TARGET], kwargs=kwargs)

    async def async_reboot(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await reboot_service.async_reboot(kwargs[ATTR_SERVICE_TARGET], kwargs=kwargs)

    async def async_logout(service: ServiceCall) -> None:
        kwargs = dict(service.data)
        await logout_service.async_logout(kwargs=kwargs)

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

    def _resolve(self, data: dict[str, Any]) -> tuple[str, Log, XploraDataUpdateCoordinator]:
        """Resolve the (entry_id, logger, coordinator) for a service call.

        ``data`` is the raw ``service.data`` dict. Mirrors the integration's calling convention:
        the target config entry id is the first token of the first ``user`` selector value
        (``"<entry_id> (<username>)"``).
        """
        entry_id = str(data[ATTR_SERVICE_USER][0]).split(" ", maxsplit=1)[0]
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        return entry_id, Log(entry_id=entry_id), coordinator

    @staticmethod
    def _resolve_targets(coordinator: XploraDataUpdateCoordinator, targets: list[str]) -> list[str]:
        """Expand the special ``all`` target to every watch user id on the account."""
        if "all" in targets:
            return list(coordinator.controller.getWatchUserIDs())
        return targets

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

    async def async_see(self, targets: list[str] | None = None, **kwargs: Any) -> None:
        """Update watch information."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            log.debug("%s: update all information: %s", coordinator.controller.getUserName(), {", ".join(targets)})
            await coordinator.async_update_xplora_data(targets)
            # await self._coordinator.async_refresh()
        else:
            log.warning("No watch id or type %s not allow!", type(targets))


class XploraRefreshFunctionsService(XploraService):
    """Create a service that refreshes the alarm/silent/safezone data on demand."""

    async def async_refresh_functions(self, targets: list[str] | None = None, **kwargs: Any) -> None:
        """Force a functions (alarm/silent/safezone) refresh, bypassing the functions interval."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            log.debug("%s: refresh functions data: %s", coordinator.controller.getUserName(), {", ".join(targets)})
            await coordinator.async_refresh_functions(targets)
        else:
            log.warning("No watch id or type %s not allow!", type(targets))


class XploraFetchHistoryService(XploraService):
    """Fetch and cache one past day's location history on demand (default: yesterday).

    The watch's API only serves the last few days, so automating this daily lets Home Assistant
    keep a long-term archive: each run stores that day's complete track in the per-day history Store.
    """

    async def async_fetch_history(self, targets: list[str] | None = None, date: str | None = None, **kwargs: Any) -> None:
        """Fetch `date` (default yesterday) for the target watch(es), forcing a fresh pull + cache."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if not isinstance(targets, list):
            log.warning("No watch id or type %s not allow!", type(targets))
            return
        if "all" in targets:
            targets = coordinator.controller.getWatchUserIDs()
        day_key = (date or "").strip() or coordinator.history_yesterday_key()
        for watch_id in targets:
            log.debug("fetch location history for %s on %s", watch_id, day_key)
            with self._api_call_guard("Fetch history", entry_id) as guard:
                await coordinator.async_fetch_history_day(watch_id, day_key, force=True)
            if guard.failed:
                break
        # Push the refreshed cache to the entities so the sensor's day list / count update.
        coordinator.async_update_listeners()


class XploraDeleteMessageFromAppService(XploraService):
    """Create a service that can be remove message from Watch."""

    async def async_delete_message_from_app(self, message_id: str = "", targets: list[str] | None = None, **kwargs: Any) -> None:
        """Delete a message to one Watch."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            msg_id = message_id.strip()
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            if not msg_id:
                log.warning("You must provide an ID!")
            else:
                for watch_id in targets:
                    log.debug("remove message %s from %s", msg_id, watch_id)
                    with self._api_call_guard("Delete message", entry_id) as guard:
                        if not await coordinator._with_recovery(
                            lambda: coordinator.controller.deleteMessageFromApp(wuid=watch_id, msgId=msg_id)
                        ):
                            log.error("Message cannot deleted!")
                    if guard.failed:
                        break
        else:
            log.warning("No watch id or type %s not allow!", type(targets))


class XploraMessageService(XploraService):
    """Create a service that can be send message to Watch."""

    async def async_send_message(self, message: str = "", targets: list[str] | None = None, **kwargs: Any) -> None:
        """Send message to Watch."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            msg = message.strip()
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            if not msg:
                log.warning("Message is empty!")
            else:
                for watch_id in targets:
                    log.debug("Sending message '%s' to '%s'", msg, watch_id)
                    with self._api_call_guard("Send message", entry_id) as guard:
                        if not await coordinator._with_recovery(lambda: coordinator.controller.sendText(text=msg, wuid=watch_id)):
                            log.error("Message cannot send!")
                    if guard.failed:
                        break
        else:
            log.warning("No watch id or type %s not allowed!", type(targets))


class XploraMessageSensorUpdateService(XploraService):
    """Create a service that can be used to read messages from Watch."""

    coordinator: XploraDataUpdateCoordinator

    async def async_read_message(self, targets: list[str] | None = None, **kwargs: Any) -> None:
        """Read the messages from account."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        self.coordinator = self._hass.data[DOMAIN][entry_id]
        if not isinstance(targets, list):
            log.warning("No watch id or type %s not allowed!", type(targets))
            return
        old_state: dict[str, Any] = self.coordinator.data
        config_entry = self.coordinator.config_entry
        resolved = resolve(config_entry.options if config_entry else {})
        limit: int = resolved.message
        show_remove_msg = resolved.remove_message
        if "all" in targets:
            targets = self.coordinator.controller.getWatchUserIDs()
        # Token expired / rate-limited / connection drop mid-read: the guard logs it cleanly (the gate
        # already attempted recovery for an expired token) and we still persist whatever was gathered
        # before the failure, letting the next coordinator poll recover the session.
        with self._api_call_guard("Read messages", entry_id):
            for watch in targets:
                res_chats = await self.coordinator._with_recovery(lambda: self.coordinator.message_data(watch, limit, show_remove_msg))
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
        await self.coordinator.async_update_xplora_data(new_data=old_state)

    async def _fetch_chat_voice(self, watch_id: str, msg_id: str) -> None:
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "mp3", "voice"):
            return
        voice = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_chat_voice(watch_id, msg_id))
        if voice:
            encoded_base64_string_to_mp3_file(self._hass, voice, msg_id)

    async def _fetch_chat_short_video(self, watch_id: str, msg_id: str) -> None:
        # Skip the remote fetch only once BOTH the video and its thumbnail are cached.
        if chat_media_cached(self._hass, msg_id, "mp4", "video") and chat_media_cached(self._hass, msg_id, "jpeg", "video/thumb"):
            return
        video = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_short_video(watch_id, msg_id))
        if video:
            encoded_base64_string_to_file(self._hass, video, msg_id, "mp4", "video")
        thumb = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_short_video_cover(watch_id, msg_id))
        if thumb:
            encoded_base64_string_to_file(self._hass, thumb, msg_id, "jpeg", "video/thumb")

    async def _fetch_chat_image(self, watch: str, msg_id: str) -> None:
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "jpeg", "image"):
            return
        image = await self.coordinator._with_recovery(lambda: self.coordinator.controller.get_chat_image(watch, msg_id))
        if image:
            encoded_base64_string_to_file(self._hass, image, msg_id, "jpeg", "image")


class XploraShutdownService(XploraService):
    """Create a service that shuts down Xplora."""

    async def async_shutdown(self, targets: list[str] | None = None, **kwargs: Any) -> None:
        """Turn off watch."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            for watch in targets:
                with self._api_call_guard("Shutdown", entry_id) as guard:
                    accepted = await coordinator._with_recovery(lambda: coordinator.controller.shutdown(watch))
                    log.debug("Shutdown result: %s", accepted)
                    if not accepted:
                        # False == the backend refused (typically the watch is off/offline).
                        log.warning("Shutdown was not accepted for watch %s (it may be off or offline)", watch[25:])
                if guard.failed:
                    break
        else:
            log.warning("No watch ID or type %s not allowed!", type(targets))


class XploraRebootService(XploraService):
    """Create a service that reboots a watch (parity with the app's `reboot(uid)` mutation)."""

    async def async_reboot(self, targets: list[str] | None = None, **kwargs: Any) -> None:
        """Reboot watch."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        if isinstance(targets, list):
            if "all" in targets:
                targets = coordinator.controller.getWatchUserIDs()
            for watch in targets:
                with self._api_call_guard("Reboot", entry_id) as guard:
                    accepted = await coordinator._with_recovery(lambda: coordinator.controller.reboot(watch))
                    log.debug("Reboot result: %s", accepted)
                    if not accepted:
                        # False == the backend refused (typically the watch is off/offline).
                        log.warning("Reboot was not accepted for watch %s (it may be off or offline)", watch[25:])
                if guard.failed:
                    break
        else:
            log.warning("No watch ID or type %s not allowed!", type(targets))


class XploraLogoutService(XploraService):
    """Create a service that logs out the account (server-side `ExpireToken` + local clear).

    Account-level, not per-watch: it invalidates the session token shared by every watch on
    the account. When polling is enabled (`CONF_SCAN_INTERVAL` > 0) the next coordinator poll
    re-logs in automatically, so it also works as a "force re-auth". With polling disabled
    (`CONF_SCAN_INTERVAL` == 0) nothing re-authenticates until the next manual `see`/reload --
    the account simply stays logged out, which is the expected logout behavior.
    """

    async def async_logout(self, **kwargs: Any) -> None:
        """Log out the account that owns the resolved config entry."""
        entry_id = str(kwargs["kwargs"]["user"][0]).split(" ", maxsplit=1)[0]
        log = Log(entry_id=entry_id)
        coordinator: XploraDataUpdateCoordinator = self._hass.data[DOMAIN][entry_id]
        try:
            acknowledged = await coordinator.controller.logout()
            log.debug("Logout result (server acknowledged: %s)", acknowledged)
        except (RateLimitError, XploraConnectionError, AuthError) as error:
            # Best-effort: the local token was already cleared inside `logout()`, so the next
            # poll re-logs in regardless. Just surface a clean warning instead of a traceback.
            log.warning("Logout could not reach the Xplora server (%s); local session cleared anyway.", type(error).__name__)


class XploraAlarmService(XploraService):
    """Create / update / delete / enable-disable alarms on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new alarm on each target watch."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])
        name = data.get(ATTR_SERVICE_NAME, "")
        for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
            with self._api_call_guard("Create alarm", entry_id) as guard:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.addAlarmTime(wuid, occur_min, week_repeat, name))
                log.debug("Create alarm on %s: %s", wuid, ok)
                await self._refresh_list(coordinator, wuid, ATTR_ALARM)
            if guard.failed:
                break

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing alarm (time, repeat days and/or name)."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None
        name = data.get(ATTR_SERVICE_NAME)
        with self._api_call_guard("Update alarm", entry_id):
            ok = await coordinator._with_recovery(lambda: coordinator.controller.modifyAlarmTime(alarm_id, occur_min, week_repeat, name))
            log.debug("Update alarm %s: %s", alarm_id, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete an alarm."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        with self._api_call_guard("Delete alarm", entry_id):
            ok = await coordinator._with_recovery(lambda: coordinator.controller.removeAlarmTime(alarm_id))
            log.debug("Delete alarm %s: %s", alarm_id, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable an alarm."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        enabled = data[ATTR_SERVICE_ENABLED]
        with self._api_call_guard("Set alarm enabled", entry_id):
            if enabled:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.setEnableAlarmTime(alarm_id))
            else:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.setDisableAlarmTime(alarm_id))
            log.debug("Set alarm %s enabled=%s: %s", alarm_id, enabled, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_ALARM)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every alarm on each target watch in one call.

        The current list is fetched FRESH per watch (not read from ``coordinator.data``) so the
        toggle is correct even when functions polling is off and the cached data is stale. One
        ``_api_call_guard`` wraps the whole per-watch unit (fetch + all toggles + entity refresh);
        on the first recoverable failure the loop breaks so we stop hammering the remaining watches.
        """
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        action = "Turn all alarms on" if enabled else "Turn all alarms off"
        for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
            with self._api_call_guard(action, entry_id) as guard:
                alarms = await coordinator._with_recovery(lambda: coordinator.controller.getWatchAlarm(wuid))
                if isinstance(alarms, FetchError):
                    log.warning("%s: %s", alarms.operation, alarms.message)
                    continue
                for alarm in alarms:
                    aid = alarm["id"]
                    if enabled:
                        await coordinator._with_recovery(lambda: coordinator.controller.setEnableAlarmTime(aid))
                    else:
                        await coordinator._with_recovery(lambda: coordinator.controller.setDisableAlarmTime(aid))
                    log.debug("Set alarm %s enabled=%s", aid, enabled)
                await self._refresh_list(coordinator, wuid, ATTR_ALARM)
            if guard.failed:
                break


class XploraSilentService(XploraService):
    """Create / update / delete / enable-disable silent-time windows on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new silent-time window on each target watch."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        start = time_str_to_minutes(data[ATTR_SERVICE_START])
        end = time_str_to_minutes(data[ATTR_SERVICE_END])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])
        for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
            with self._api_call_guard("Create silent time", entry_id) as guard:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.addSilentTime(wuid, start, end, week_repeat))
                log.debug("Create silent on %s: %s", wuid, ok)
                await self._refresh_list(coordinator, wuid, ATTR_SILENT)
            if guard.failed:
                break

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing silent-time window (start, end and/or repeat days)."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        start = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        end = time_str_to_minutes(data[ATTR_SERVICE_END]) if ATTR_SERVICE_END in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None
        with self._api_call_guard("Update silent time", entry_id):
            ok = await coordinator._with_recovery(lambda: coordinator.controller.modifySilentTime(silent_id, start, end, week_repeat))
            log.debug("Update silent %s: %s", silent_id, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete a silent-time window."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        with self._api_call_guard("Delete silent time", entry_id):
            ok = await coordinator._with_recovery(lambda: coordinator.controller.removeSilentTime(silent_id))
            log.debug("Delete silent %s: %s", silent_id, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable a silent-time window."""
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        enabled = data[ATTR_SERVICE_ENABLED]
        with self._api_call_guard("Set silent enabled", entry_id):
            if enabled:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.setEnableSilentTime(silent_id))
            else:
                ok = await coordinator._with_recovery(lambda: coordinator.controller.setDisableSilentTime(silent_id))
            log.debug("Set silent %s enabled=%s: %s", silent_id, enabled, ok)
            for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
                await self._refresh_list(coordinator, wuid, ATTR_SILENT)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every silent-time window on each target watch in one call.

        Mirror of :meth:`XploraAlarmService.async_set_all_enabled`: fresh per-watch fetch, all calls
        routed through the recovery gate, and the loop breaks on the first recoverable failure.
        """
        data = kwargs["kwargs"]
        entry_id, log, coordinator = self._resolve(data)
        action = "Turn all silents on" if enabled else "Turn all silents off"
        for wuid in self._resolve_targets(coordinator, data[ATTR_SERVICE_TARGET]):
            with self._api_call_guard(action, entry_id) as guard:
                silents = await coordinator._with_recovery(lambda: coordinator.controller.getSilentTime(wuid))
                if isinstance(silents, FetchError):
                    log.warning("%s: %s", silents.operation, silents.message)
                    continue
                for silent in silents:
                    sid = silent["id"]
                    if enabled:
                        await coordinator._with_recovery(lambda: coordinator.controller.setEnableSilentTime(sid))
                    else:
                        await coordinator._with_recovery(lambda: coordinator.controller.setDisableSilentTime(sid))
                    log.debug("Set silent %s enabled=%s", sid, enabled)
                await self._refresh_list(coordinator, wuid, ATTR_SILENT)
            if guard.failed:
                break
