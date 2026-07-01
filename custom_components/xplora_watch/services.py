"""Support for Xplora® Watch Version 2 send/read message, manually refresh and shutdown.

Services are targeted at Home Assistant **devices** (ADR 0003). Each account's copy of a watch
is its own HA device (named ``"<Ward> Watch (<account token>)"``, ADR 0002), so a single device
pick identifies both the account (config entry / coordinator) and the watch (``wuid``). A single
call can resolve to watches across several accounts (an ``area``/``floor``/``label`` target or a
multi-device pick), so every handler routes through one shared, **best-effort** fan-out executor
(ADR 0004): it acts on every ``(account, wuid)`` it can, drops Contact-gated watches before any
request (the Guardian gate, ADR 0001, is a pure client-side pre-filter), stops the rest of an
account's watches on a recoverable error but continues to the next account, and surfaces the
outcome — raising a clear error when nothing succeeded, or a single self-healing notification when
some watches were skipped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import target as target_helper

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

# Per-account gating action phrases, shared by every Guardian-only control service. They fill the
# `not_guardian` error's `{action}` placeholder and the per-watch "skipped: contact" warning.
_ACTION_ALARMS = "change the watch's alarms"
_ACTION_SILENTS = "change the watch's silent times"


def _target_schema(extra: dict[Any, Any] | None = None) -> vol.Schema:
    """Schema for a device-targeted service: HA ``device_id`` / ``area_id`` / ``entity_id`` plus fields.

    The watch is chosen in the UI via the ``device_id`` field (a ``device`` selector filtered to this
    integration -- see ``services.yaml``). ``device_id`` is ``Optional`` here (not required) because
    programmatic callers -- the bundled Lovelace card -- instead pass ``entity_id``, and an empty
    resolution is rejected at runtime by ``XploraService._accounts`` with a friendly
    ``ServiceValidationError`` rather than a schema error. ``area_id`` / ``entity_id`` are accepted
    from YAML automations and the card; both resolve to the watch's device below. ``floor_id`` /
    ``label_id`` are also accepted (they pass through ``vol.ALLOW_EXTRA`` and are expanded by Home
    Assistant's target helper, ADR 0004) without adding a UI selector.
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

    A single call may target devices belonging to several accounts (multi-select, area, floor or
    label); the resolver groups them so the fan-out executor runs each account's body once.
    """

    entry_id: str
    log: Log
    coordinator: XploraDataUpdateCoordinator
    wuids: list[str] = field(default_factory=list)


def _targeted_device_ids(hass: HomeAssistant, data: dict[str, Any]) -> list[str]:
    """Every HA device id a call's target resolves to, via Home Assistant's native target helper.

    Expands ``device_id`` / ``entity_id`` / ``area_id`` / ``floor_id`` / ``label_id`` (ADR 0004),
    including an entity assigned to an area whose *device* lives elsewhere -- that entity is caught
    through the helper's indirect expansion and mapped back to its device. Returns a de-duplicated,
    sorted (deterministic) list; ``_resolve_device`` then keeps only the Xplora watch devices.
    """
    selection = target_helper.TargetSelection(data)
    selected = target_helper.async_extract_referenced_entity_ids(hass, selection)
    device_ids: set[str] = set(selected.referenced_devices)
    entities = er.async_get(hass)
    # An entity target (or an entity assigned to a targeted area whose device is elsewhere) maps back
    # to its own device -- the case the old hand-rolled expansion silently dropped.
    for entity_id in selected.referenced | selected.indirectly_referenced:
        entry = entities.async_get(entity_id)
        if entry and entry.device_id:
            device_ids.add(entry.device_id)
    return sorted(device_ids)


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


def _error_reason(error: Exception) -> str:
    """A short, user-facing phrase for a recoverable controller error (fills toast/notification text)."""
    if isinstance(error, RateLimitError):
        return "rate-limited by the Xplora server (please retry later)"
    if isinstance(error, XploraConnectionError):
        return "could not reach the Xplora server"
    return "session token expired (it will refresh on the next update)"


def _log_api_error(action: str, log: Log, error: Exception) -> None:
    """Log a clean, per-type warning when a service call hits a recoverable controller error.

    Service controller calls route through the coordinator's centralized single-flight recovery
    gate (``_with_recovery``), so an expired token is recovered there (bounded refresh -> at-most-one
    re-login -> one retry) before it ever reaches this handler. An exception arriving here therefore
    means recovery was either not applicable (a 429 / connection drop -- those bypass the gate) or
    was attempted and exhausted. The message is tailored per type so the user sees what actually
    happened instead of one generic "session expired" for everything. ``log`` is the *account's*
    logger, so a per-watch failure warning is attributed to the correct account (ADR 0004).
    """
    if isinstance(error, RateLimitError):
        log.warning("%s skipped: Xplora API rate limit (HTTP 429); not retried to avoid a ban -- please retry later.", action)
    elif isinstance(error, XploraConnectionError):
        log.warning("%s failed: could not reach the Xplora server (%s) -- please retry.", action, error)
    else:
        log.warning("%s skipped: Xplora session token expired; it will refresh on the next update -- please retry.", action)


# Per-watch outcome kinds (ADR 0004). `skipped` is recorded before any request (Contact-gated); the
# other three are recorded by a controller call. Priority orders which "worst" outcome wins when a
# single watch makes several calls (a later error downgrades an earlier success).
_SKIPPED = "skipped"
_SUCCEEDED = "succeeded"
_REFUSED = "refused"
_ERRORED = "errored"
_PRIORITY = {_SUCCEEDED: 0, _REFUSED: 1, _ERRORED: 2}


@dataclass(slots=True)
class _WatchOutcome:
    """The final outcome of one resolved ``(account, wuid)`` pair, for surfacing."""

    kind: str
    account: str
    wuid: str | None
    reason: str


def _phrase(outcome: _WatchOutcome) -> str:
    """A one-line, user-facing reason a watch was not actioned (toast details / notification bullet)."""
    if outcome.kind == _SKIPPED:
        return f"{outcome.account}: this account is only a contact of the watch, not its guardian (skipped)"
    if outcome.kind == _REFUSED:
        return f"{outcome.account}: the watch could not be reached (it may be switched off or offline)"
    return f"{outcome.account}: {outcome.reason}"


class _Outcomes:
    """Per-``(account, wuid)`` outcome buckets accumulated across a fan-out (ADR 0004).

    The executor reads these to decide how to surface the call: raise a ``ServiceValidationError``
    when nothing succeeded, or fire a single notification when some watches were skipped.
    """

    def __init__(self) -> None:
        """Start empty; the ``_Account`` primitives fill this in as the fan-out runs."""
        self._records: dict[tuple[str, str | None], _WatchOutcome] = {}

    def skip(self, entry_id: str, wuid: str, account: str, action: str) -> None:
        """Record a Contact-gated watch (dropped before any request)."""
        self._records[(entry_id, wuid)] = _WatchOutcome(_SKIPPED, account, wuid, action)

    def record(self, entry_id: str, wuid: str | None, account: str, kind: str, reason: str) -> None:
        """Record a controller-call outcome, keeping the worst outcome seen for this watch.

        A gated (``skipped``) watch is never called, so it is never overwritten here.
        """
        key = (entry_id, wuid)
        existing = self._records.get(key)
        if existing is not None and existing.kind == _SKIPPED:
            return
        if existing is None or _PRIORITY[kind] > _PRIORITY[existing.kind]:
            self._records[key] = _WatchOutcome(kind, account, wuid, reason)

    @property
    def records(self) -> list[_WatchOutcome]:
        """All recorded outcomes."""
        return list(self._records.values())

    def succeeded(self) -> list[_WatchOutcome]:
        """The watches that were actioned successfully."""
        return [r for r in self._records.values() if r.kind == _SUCCEEDED]

    def gaps(self) -> list[_WatchOutcome]:
        """The watches that were NOT actioned (skipped / refused / errored)."""
        return [r for r in self._records.values() if r.kind != _SUCCEEDED]


class _Account:
    """Per-account primitives handed to a service body by the fan-out executor (ADR 0004).

    A handler writes only a small loop over ``targets(...)`` and ``call(...)``; the executor owns
    target resolution, the raise-vs-notify surfacing, and the break-this-account-on-error rule.
    """

    __slots__ = ("entry_id", "coordinator", "log", "wuids", "label", "broken", "_outcomes", "_broken_reason")

    def __init__(
        self,
        entry_id: str,
        coordinator: XploraDataUpdateCoordinator,
        log: Log,
        wuids: list[str],
        outcomes: _Outcomes,
        label: str,
    ) -> None:
        """Bind one account's coordinator/logger/watches plus the shared outcome accumulator."""
        self.entry_id = entry_id
        self.coordinator = coordinator
        self.log = log
        self.wuids = wuids
        self.label = label
        self.broken = False
        self._outcomes = outcomes
        self._broken_reason = ""

    def targets(self, *, guardian: bool, action: str) -> list[str]:
        """The watches to act on for this account, applying the Guardian pre-filter when asked.

        With ``guardian=True`` every Contact-only watch is dropped *before* any request (ADR 0001
        becomes a pure client-side pre-filter) and recorded as ``skipped``; the rest are returned.
        With ``guardian=False`` every resolved watch is returned. ``action`` is the short phrase
        shown to the user for what was blocked (e.g. ``"reboot the watch"``).
        """
        if not guardian:
            return list(self.wuids)
        allowed: list[str] = []
        for wuid in self.wuids:
            if self.coordinator.is_confirmed_contact(wuid):
                self.log.warning(
                    "Skipping '%s' for watch %s: this account is a contact of it, not its primary guardian.",
                    action,
                    wuid,
                )
                self._outcomes.skip(self.entry_id, wuid, self.label, action)
            else:
                allowed.append(wuid)
        return allowed

    async def call(self, action: str, wuid: str | None, factory: Callable[..., Awaitable[Any]], *, recover: bool = True) -> Any:
        """Run one controller call, record its outcome, and never send a doomed request.

        Once a recoverable error (rate-limit / expired-login auth / connection) has broken this
        account, every later ``call`` short-circuits without touching the server -- the rest of the
        account's watches are expected to keep failing (ADR 0004). A returned ``False`` is a *refused*
        (the server was reached but declined -- typically an offline watch); anything else is a
        *success*. Returns the call result, or ``None`` when the call errored / was short-circuited.

        ``recover`` wraps ``factory`` in the coordinator's single-flight token-recovery gate (the
        default, for raw ``controller.*`` calls); pass ``recover=False`` for a coordinator method that
        already recovers internally (``async_update_xplora_data`` / ``async_fetch_history_day``).
        """
        if self.broken:
            self._outcomes.record(self.entry_id, wuid, self.label, _ERRORED, self._broken_reason)
            return None
        try:
            result = await (self.coordinator._with_recovery(factory) if recover else factory())
        except (AuthError, RateLimitError, XploraConnectionError) as error:
            _log_api_error(action, self.log, error)
            reason = _error_reason(error)
            self._outcomes.record(self.entry_id, wuid, self.label, _ERRORED, reason)
            self.broken = True
            self._broken_reason = reason
            return None
        if result is False:
            self._outcomes.record(self.entry_id, wuid, self.label, _REFUSED, action)
        else:
            self._outcomes.record(self.entry_id, wuid, self.label, _SUCCEEDED, "")
        return result


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


class XploraService:
    """Common base for Xplora® service handlers, owning the shared best-effort fan-out (ADR 0004)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the entry."""
        self._hass = hass
        self._entry_id = entry_id

    def _accounts(self, data: dict[str, Any]) -> list[_AccountTargets]:
        """Resolve a call's device targets into per-account work units.

        Groups every resolved ``(account, wuid)`` by account and de-dupes watches, so a multi-device /
        area / floor / label selection fans out to each account once. Order is deterministic (the
        resolved device ids are sorted) rather than target order -- the fan-out is best-effort and
        order-independent. Devices that aren't Xplora watches (or whose account isn't loaded) are
        silently skipped. Raises ``ServiceValidationError`` (``no_xplora_device``) when the call
        resolves to no Xplora watch at all -- so a misfired call surfaces a clean message instead of
        silently doing nothing.
        """
        groups: dict[str, _AccountTargets] = {}
        order: list[str] = []
        for device_id in _targeted_device_ids(self._hass, data):
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

    async def _fan_out(self, data: dict[str, Any], service: str, body: Callable[[_Account], Awaitable[None]]) -> None:
        """Run ``body`` once per resolved account, best-effort, then surface the combined outcome.

        Every account is independent: a recoverable error breaks the rest of *that* account's
        watches (handled inside ``_Account.call``) but the fan-out still runs ``body`` for the next
        account. ``service`` is the Home Assistant service name -- it keys the partial-success
        notification so it replaces/self-heals across repeated runs.
        """
        outcomes = _Outcomes()
        for group in self._accounts(data):
            label = group.coordinator.controller.getUserName() or group.coordinator._entry.title or group.entry_id
            account = _Account(group.entry_id, group.coordinator, group.log, group.wuids, outcomes, label)
            await body(account)
        self._surface(service, outcomes)

    def _surface(self, service: str, outcomes: _Outcomes) -> None:
        """Turn the accumulated outcomes into user feedback (ADR 0004).

        Nothing succeeded -> raise a ``ServiceValidationError`` (error toast): a homogeneous failure
        uses a precise key (``not_guardian`` / ``watch_offline``), a mixed / all-errored one the
        generic ``nothing_actioned`` with an enumerated ``{details}``. At least one success with gaps
        -> a single ``persistent_notification`` (keyed per service, so it replaces on repeat) that
        names the service and lists what was skipped, then return cleanly. A fully-clean run dismisses
        any stale notification. The service is named in both the title and the body so an operator
        running several automations can tell which call produced the notice.
        """
        notification_id = f"{DOMAIN}_{service}"
        records = outcomes.records
        if not records:
            # Nothing was attempted (e.g. an input-validation short-circuit); no feedback needed.
            return
        succeeded = outcomes.succeeded()
        gaps = outcomes.gaps()
        if succeeded:
            if gaps:
                bullets = "\n".join(f"- {_phrase(g)}" for g in gaps)
                persistent_notification.async_create(
                    self._hass,
                    message=(f"`{DOMAIN}.{service}` actioned {len(succeeded)} watch(es); the following were not actioned:\n{bullets}"),
                    title=f"Xplora® Watch: {service.replace('_', ' ')} partly completed",
                    notification_id=notification_id,
                )
            else:
                # Fully clean run of this service -> clear any stale "partly completed" notice.
                persistent_notification.async_dismiss(self._hass, notification_id)
            return
        # Zero succeeded: raise so a fully-failed call never masquerades as success.
        kinds = {g.kind for g in gaps}
        if kinds == {_SKIPPED}:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_guardian",
                translation_placeholders={"action": gaps[0].reason},
            )
        if kinds == {_REFUSED}:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="watch_offline")
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="nothing_actioned",
            translation_placeholders={"details": "; ".join(_phrase(g) for g in gaps)},
        )

    async def _refresh_list(self, coordinator: XploraDataUpdateCoordinator, wuid: str, data_key: str, log: Log) -> None:
        """Re-fetch just the mutated alarm/silent list for one watch and push it to the entities.

        Uses ``async_set_updated_data`` (a single targeted fetch) rather than a full
        ``async_refresh`` poll, keeping the integration off the rate-limit radar as intended. ``log``
        is the account's logger, so a benign fetch-error warning is attributed to the right account.
        """
        if data_key == ATTR_ALARM:
            new_items = await coordinator._with_recovery(lambda: coordinator.controller.getWatchAlarm(wuid))
        else:
            new_items = await coordinator._with_recovery(lambda: coordinator.controller.getSilentTime(wuid))
        if isinstance(new_items, FetchError):
            log.warning("%s: %s", new_items.operation, new_items.message)
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

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            account.log.debug("%s: update all information: %s", coordinator.controller.getUserName(), ", ".join(account.wuids))
            # `async_update_xplora_data` recovers a stale token internally, so `recover=False`.
            await account.call("Update", None, lambda: coordinator.async_update_xplora_data(account.wuids), recover=False)

        await self._fan_out(data, ATTR_SERVICE_SEE, body)


class XploraRefreshFunctionsService(XploraService):
    """Create a service that refreshes the alarm/silent/safezone data on demand."""

    async def async_refresh_functions(self, **kwargs: Any) -> None:
        """Force a functions (alarm/silent/safezone) refresh, bypassing the functions interval."""
        data = kwargs["kwargs"]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            account.log.debug("%s: refresh functions data: %s", coordinator.controller.getUserName(), ", ".join(account.wuids))
            await account.call("Refresh functions", None, lambda: coordinator.async_refresh_functions(account.wuids), recover=False)

        await self._fan_out(data, ATTR_SERVICE_REFRESH_FUNCTIONS, body)


class XploraFetchHistoryService(XploraService):
    """Fetch and cache one past day's location history on demand (default: yesterday).

    The watch's API only serves the last few days, so automating this daily lets Home Assistant
    keep a long-term archive: each run stores that day's complete track in the per-day history Store.
    """

    async def async_fetch_history(self, **kwargs: Any) -> None:
        """Fetch `date` (default yesterday) for the target watch(es), forcing a fresh pull + cache."""
        data = kwargs["kwargs"]
        date = data.get(ATTR_SERVICE_DATE)

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            day_key = (date or "").strip() or coordinator.history_yesterday_key()
            for watch_id in account.wuids:
                account.log.debug("fetch location history for %s on %s", watch_id, day_key)
                # `async_fetch_history_day` recovers a stale token internally, so `recover=False`.
                await account.call(
                    "Fetch history",
                    watch_id,
                    lambda wid=watch_id: coordinator.async_fetch_history_day(wid, day_key, force=True),
                    recover=False,
                )
            # Push the refreshed cache to the entities so the sensor's day list / count update.
            coordinator.async_update_listeners()

        await self._fan_out(data, ATTR_SERVICE_FETCH_HISTORY, body)


class XploraDeleteMessageFromAppService(XploraService):
    """Create a service that can be remove message from Watch."""

    async def async_delete_message_from_app(self, **kwargs: Any) -> None:
        """Delete a message to one Watch."""
        data = kwargs["kwargs"]
        msg_id = str(data[ATTR_SERVICE_MSGID]).strip()
        if not msg_id:
            Log(entry_id=self._entry_id).warning("You must provide an ID!")
            return

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for watch_id in account.wuids:
                account.log.debug("remove message %s from %s", msg_id, watch_id)
                result = await account.call(
                    "Delete message",
                    watch_id,
                    lambda wid=watch_id: coordinator.controller.deleteMessageFromApp(wuid=wid, msgId=msg_id),
                )
                if result is False:
                    account.log.error("Message cannot deleted!")

        await self._fan_out(data, ATTR_SERVICE_DELETE_MSG, body)


class XploraMessageService(XploraService):
    """Create a service that can be send message to Watch."""

    async def async_send_message(self, **kwargs: Any) -> None:
        """Send message to Watch."""
        data = kwargs["kwargs"]
        msg = str(data[ATTR_SERVICE_MSG]).strip()
        if not msg:
            Log(entry_id=self._entry_id).warning("Message is empty!")
            return

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for watch_id in account.wuids:
                account.log.debug("Sending message '%s' to '%s'", msg, watch_id)
                result = await account.call(
                    "Send message",
                    watch_id,
                    lambda wid=watch_id: coordinator.controller.sendText(text=msg, wuid=wid),
                )
                if result is False:
                    account.log.error("Message cannot send!")

        await self._fan_out(data, ATTR_SERVICE_SEND_MSG, body)


class XploraMessageSensorUpdateService(XploraService):
    """Create a service that can be used to read messages from Watch."""

    async def async_read_message(self, **kwargs: Any) -> None:
        """Read the messages from account."""
        data = kwargs["kwargs"]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            old_state: dict[str, Any] = coordinator.data
            resolved = resolve(coordinator._entry.options)
            limit: int = resolved.message
            show_remove_msg = resolved.remove_message
            for watch in account.wuids:
                res_chats = await account.call(
                    "Read messages",
                    watch,
                    lambda w=watch: coordinator.message_data(w, limit, show_remove_msg),
                )
                if account.broken:
                    break
                if not res_chats:
                    continue
                # A recoverable error during the (rate-limited) media fetch stops the rest of this
                # account's reads; the chats already gathered are still persisted below.
                try:
                    for chat in res_chats.get("list") or []:
                        chat_type = chat.get("type")
                        msg_id = chat.get("msgId")
                        if chat_type == "VOICE":
                            await self._fetch_chat_voice(coordinator, watch, msg_id)
                        elif chat_type == "SHORT_VIDEO":
                            await self._fetch_chat_short_video(coordinator, watch, msg_id)
                        elif chat_type == "IMAGE":
                            await self._fetch_chat_image(coordinator, watch, msg_id)
                except (AuthError, RateLimitError, XploraConnectionError) as error:
                    _log_api_error("Read messages", account.log, error)
                    account.broken = True
                new_data_msg: dict[str, Any] = old_state.get(watch, {}) if isinstance(old_state, dict) else {}
                if new_data_msg:
                    new_data_msg.update({SENSOR_MESSAGE: res_chats})
                    old_state.update({watch: new_data_msg})
                if account.broken:
                    break
            await coordinator.async_update_xplora_data(new_data=old_state)

        await self._fan_out(data, ATTR_SERVICE_READ_MSG, body)

    async def _fetch_chat_voice(self, coordinator: XploraDataUpdateCoordinator, watch_id: str, msg_id: str) -> None:
        # `coordinator` is passed in (not read from instance state) so concurrent read_message calls
        # for different accounts never cross wires (ADR 0004).
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "mp3", "voice"):
            return
        voice = await coordinator._with_recovery(lambda: coordinator.controller.get_chat_voice(watch_id, msg_id))
        if voice:
            await encoded_base64_string_to_mp3_file(self._hass, voice, msg_id)

    async def _fetch_chat_short_video(self, coordinator: XploraDataUpdateCoordinator, watch_id: str, msg_id: str) -> None:
        # Skip the remote fetch only once BOTH the video and its thumbnail are cached.
        if chat_media_cached(self._hass, msg_id, "mp4", "video") and chat_media_cached(self._hass, msg_id, "jpeg", "video/thumb"):
            return
        video = await coordinator._with_recovery(lambda: coordinator.controller.get_short_video(watch_id, msg_id))
        if video:
            await encoded_base64_string_to_file(self._hass, video, msg_id, "mp4", "video")
        thumb = await coordinator._with_recovery(lambda: coordinator.controller.get_short_video_cover(watch_id, msg_id))
        if thumb:
            await encoded_base64_string_to_file(self._hass, thumb, msg_id, "jpeg", "video/thumb")

    async def _fetch_chat_image(self, coordinator: XploraDataUpdateCoordinator, watch_id: str, msg_id: str) -> None:
        # Already downloaded -> skip the remote (rate-limited) fetch and serve the cached file.
        if chat_media_cached(self._hass, msg_id, "jpeg", "image"):
            return
        image = await coordinator._with_recovery(lambda: coordinator.controller.get_chat_image(watch_id, msg_id))
        if image:
            await encoded_base64_string_to_file(self._hass, image, msg_id, "jpeg", "image")


class XploraShutdownService(XploraService):
    """Create a service that shuts down Xplora."""

    async def async_shutdown(self, **kwargs: Any) -> None:
        """Turn off watch."""
        data = kwargs["kwargs"]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for watch in account.targets(guardian=True, action="shut down the watch"):
                accepted = await account.call("Shutdown", watch, lambda w=watch: coordinator.controller.shutdown(w))
                if accepted is None:
                    continue  # errored / short-circuited; already logged
                account.log.debug("Shutdown result: %s", accepted)
                if accepted is False:
                    # False == the backend refused (typically the watch is off/offline).
                    account.log.warning("Shutdown was not accepted for watch %s (it may be off or offline)", watch[25:])

        await self._fan_out(data, ATTR_SERVICE_SHUTDOWN, body)


class XploraRebootService(XploraService):
    """Create a service that reboots a watch (parity with the app's `reboot(uid)` mutation)."""

    async def async_reboot(self, **kwargs: Any) -> None:
        """Reboot watch."""
        data = kwargs["kwargs"]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for watch in account.targets(guardian=True, action="reboot the watch"):
                accepted = await account.call("Reboot", watch, lambda w=watch: coordinator.controller.reboot(w))
                if accepted is None:
                    continue  # errored / short-circuited; already logged
                account.log.debug("Reboot result: %s", accepted)
                if accepted is False:
                    # False == the backend refused (typically the watch is off/offline).
                    account.log.warning("Reboot was not accepted for watch %s (it may be off or offline)", watch[25:])

        await self._fan_out(data, ATTR_SERVICE_REBOOT, body)


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

        async def _logout(account: _Account) -> bool:
            try:
                acknowledged = await account.coordinator.controller.logout()
                account.log.debug("Logout result (server acknowledged: %s)", acknowledged)
            except (RateLimitError, XploraConnectionError, AuthError) as error:
                # Best-effort: the local token was already cleared inside `logout()`, so the next
                # poll re-logs in regardless. Surface a clean warning instead of a traceback, and
                # still count it a success -- the account IS logged out locally.
                account.log.warning("Logout could not reach the Xplora server (%s); local session cleared anyway.", type(error).__name__)
            return True

        async def body(account: _Account) -> None:
            # Account-level: one call per account, no per-watch loop (ADR 0004). `_logout` swallows a
            # transient server error, so `call` never marks it errored (recover=False -- no gate).
            await account.call("Logout", None, lambda: _logout(account), recover=False)

        await self._fan_out(data, ATTR_SERVICE_LOGOUT, body)


class XploraAlarmService(XploraService):
    """Create / update / delete / enable-disable alarms on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new alarm on each target watch."""
        data = kwargs["kwargs"]
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])
        name = data.get(ATTR_SERVICE_NAME, "")

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for wuid in account.targets(guardian=True, action=_ACTION_ALARMS):
                ok = await account.call(
                    "Create alarm", wuid, lambda w=wuid: coordinator.controller.addAlarmTime(w, occur_min, week_repeat, name)
                )
                if ok is None:
                    continue  # errored / short-circuited
                account.log.debug("Create alarm on %s: %s", wuid, ok)
                await self._refresh_list(coordinator, wuid, ATTR_ALARM, account.log)

        await self._fan_out(data, ATTR_SERVICE_CREATE_ALARM, body)

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing alarm (time, repeat days and/or name)."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        occur_min = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None
        name = data.get(ATTR_SERVICE_NAME)

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            # Gate up front: by-id mutation fires once, then the per-watch refresh loop runs.
            targets = account.targets(guardian=True, action=_ACTION_ALARMS)
            if not targets:
                return
            ok = await account.call(
                "Update alarm", targets[0], lambda: coordinator.controller.modifyAlarmTime(alarm_id, occur_min, week_repeat, name)
            )
            if ok is None:
                return
            account.log.debug("Update alarm %s: %s", alarm_id, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_ALARM, account.log)

        await self._fan_out(data, ATTR_SERVICE_UPDATE_ALARM, body)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete an alarm."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            targets = account.targets(guardian=True, action=_ACTION_ALARMS)
            if not targets:
                return
            ok = await account.call("Delete alarm", targets[0], lambda: coordinator.controller.removeAlarmTime(alarm_id))
            if ok is None:
                return
            account.log.debug("Delete alarm %s: %s", alarm_id, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_ALARM, account.log)

        await self._fan_out(data, ATTR_SERVICE_DELETE_ALARM, body)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable an alarm."""
        data = kwargs["kwargs"]
        alarm_id = data[ATTR_SERVICE_ALARM_ID]
        enabled = data[ATTR_SERVICE_ENABLED]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            targets = account.targets(guardian=True, action=_ACTION_ALARMS)
            if not targets:
                return
            if enabled:
                ok = await account.call("Set alarm enabled", targets[0], lambda: coordinator.controller.setEnableAlarmTime(alarm_id))
            else:
                ok = await account.call("Set alarm enabled", targets[0], lambda: coordinator.controller.setDisableAlarmTime(alarm_id))
            if ok is None:
                return
            account.log.debug("Set alarm %s enabled=%s: %s", alarm_id, enabled, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_ALARM, account.log)

        await self._fan_out(data, ATTR_SERVICE_SET_ALARM_ENABLED, body)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every alarm on each target watch in one call.

        The current list is fetched FRESH per watch (not read from ``coordinator.data``) so the
        toggle is correct even when functions polling is off and the cached data is stale. A
        recoverable failure breaks the rest of the account (ADR 0004): the toggle loop stops and the
        refresh is skipped so we don't keep hammering a throttled account.
        """
        data = kwargs["kwargs"]
        action = "Turn all alarms on" if enabled else "Turn all alarms off"

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for wuid in account.targets(guardian=True, action=_ACTION_ALARMS):
                alarms = await account.call(action, wuid, lambda w=wuid: coordinator.controller.getWatchAlarm(w))
                if isinstance(alarms, FetchError):
                    account.log.warning("%s: %s", alarms.operation, alarms.message)
                    continue
                if not isinstance(alarms, list):
                    continue  # errored / short-circuited
                for alarm in alarms:
                    aid = alarm["id"]
                    if enabled:
                        result = await account.call(action, wuid, lambda a=aid: coordinator.controller.setEnableAlarmTime(a))
                    else:
                        result = await account.call(action, wuid, lambda a=aid: coordinator.controller.setDisableAlarmTime(a))
                    if result is None:
                        break  # errored; stop toggling this (now-broken) account's watch
                    account.log.debug("Set alarm %s enabled=%s", aid, enabled)
                if account.broken:
                    continue  # skip the refresh; the next watch's call short-circuits anyway
                await self._refresh_list(coordinator, wuid, ATTR_ALARM, account.log)

        await self._fan_out(data, ATTR_SERVICE_TURN_ALL_ALARMS_ON if enabled else ATTR_SERVICE_TURN_ALL_ALARMS_OFF, body)


class XploraSilentService(XploraService):
    """Create / update / delete / enable-disable silent-time windows on a watch."""

    async def async_create(self, **kwargs: Any) -> None:
        """Create a new silent-time window on each target watch."""
        data = kwargs["kwargs"]
        start = time_str_to_minutes(data[ATTR_SERVICE_START])
        end = time_str_to_minutes(data[ATTR_SERVICE_END])
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS])

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for wuid in account.targets(guardian=True, action=_ACTION_SILENTS):
                ok = await account.call(
                    "Create silent time", wuid, lambda w=wuid: coordinator.controller.addSilentTime(w, start, end, week_repeat)
                )
                if ok is None:
                    continue
                account.log.debug("Create silent on %s: %s", wuid, ok)
                await self._refresh_list(coordinator, wuid, ATTR_SILENT, account.log)

        await self._fan_out(data, ATTR_SERVICE_CREATE_SILENT, body)

    async def async_update(self, **kwargs: Any) -> None:
        """Modify an existing silent-time window (start, end and/or repeat days)."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        start = time_str_to_minutes(data[ATTR_SERVICE_START]) if ATTR_SERVICE_START in data else None
        end = time_str_to_minutes(data[ATTR_SERVICE_END]) if ATTR_SERVICE_END in data else None
        week_repeat = weekdays_to_week_repeat(data[ATTR_SERVICE_WEEKDAYS]) if ATTR_SERVICE_WEEKDAYS in data else None

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            targets = account.targets(guardian=True, action=_ACTION_SILENTS)
            if not targets:
                return
            ok = await account.call(
                "Update silent time", targets[0], lambda: coordinator.controller.modifySilentTime(silent_id, start, end, week_repeat)
            )
            if ok is None:
                return
            account.log.debug("Update silent %s: %s", silent_id, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_SILENT, account.log)

        await self._fan_out(data, ATTR_SERVICE_UPDATE_SILENT, body)

    async def async_delete(self, **kwargs: Any) -> None:
        """Delete a silent-time window."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            targets = account.targets(guardian=True, action=_ACTION_SILENTS)
            if not targets:
                return
            ok = await account.call("Delete silent time", targets[0], lambda: coordinator.controller.removeSilentTime(silent_id))
            if ok is None:
                return
            account.log.debug("Delete silent %s: %s", silent_id, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_SILENT, account.log)

        await self._fan_out(data, ATTR_SERVICE_DELETE_SILENT, body)

    async def async_set_enabled(self, **kwargs: Any) -> None:
        """Enable or disable a silent-time window."""
        data = kwargs["kwargs"]
        silent_id = data[ATTR_SERVICE_SILENT_ID]
        enabled = data[ATTR_SERVICE_ENABLED]

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            targets = account.targets(guardian=True, action=_ACTION_SILENTS)
            if not targets:
                return
            if enabled:
                ok = await account.call("Set silent enabled", targets[0], lambda: coordinator.controller.setEnableSilentTime(silent_id))
            else:
                ok = await account.call("Set silent enabled", targets[0], lambda: coordinator.controller.setDisableSilentTime(silent_id))
            if ok is None:
                return
            account.log.debug("Set silent %s enabled=%s: %s", silent_id, enabled, ok)
            for wuid in targets:
                await self._refresh_list(coordinator, wuid, ATTR_SILENT, account.log)

        await self._fan_out(data, ATTR_SERVICE_SET_SILENT_ENABLED, body)

    async def async_set_all_enabled(self, enabled: bool, **kwargs: Any) -> None:
        """Enable or disable every silent-time window on each target watch in one call.

        Mirror of :meth:`XploraAlarmService.async_set_all_enabled`: fresh per-watch fetch, all calls
        routed through the fan-out executor, and the account breaks on the first recoverable failure.
        """
        data = kwargs["kwargs"]
        action = "Turn all silents on" if enabled else "Turn all silents off"

        async def body(account: _Account) -> None:
            coordinator = account.coordinator
            for wuid in account.targets(guardian=True, action=_ACTION_SILENTS):
                silents = await account.call(action, wuid, lambda w=wuid: coordinator.controller.getSilentTime(w))
                if isinstance(silents, FetchError):
                    account.log.warning("%s: %s", silents.operation, silents.message)
                    continue
                if not isinstance(silents, list):
                    continue  # errored / short-circuited
                for silent in silents:
                    sid = silent["id"]
                    if enabled:
                        result = await account.call(action, wuid, lambda s=sid: coordinator.controller.setEnableSilentTime(s))
                    else:
                        result = await account.call(action, wuid, lambda s=sid: coordinator.controller.setDisableSilentTime(s))
                    if result is None:
                        break
                    account.log.debug("Set silent %s enabled=%s", sid, enabled)
                if account.broken:
                    continue
                await self._refresh_list(coordinator, wuid, ATTR_SILENT, account.log)

        await self._fan_out(data, ATTR_SERVICE_TURN_ALL_SILENTS_ON if enabled else ATTR_SERVICE_TURN_ALL_SILENTS_OFF, body)
