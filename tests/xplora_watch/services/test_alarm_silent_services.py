"""Tests for the alarm / silent-time CRUD services (XploraAlarmService / XploraSilentService).

A MagicMock coordinator stands in for the real one: the services only touch
``coordinator.controller`` (CRUD + list fetchers), ``coordinator.data`` and
``coordinator.async_set_updated_data``. This isolates the conversion + refresh logic from the
network/GraphQL harness.

Targeting is device-based (ADR 0003): a real ``MockConfigEntry`` + device-registry device per watch
let the handlers resolve ``device_id`` -> ``(account, wuid)`` against the MagicMock coordinator. The
device <-> account resolution itself is covered in ``test_device_targeting.py``.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    ATTR_SERVICE_ALARM_ID,
    ATTR_SERVICE_ENABLED,
    ATTR_SERVICE_END,
    ATTR_SERVICE_NAME,
    ATTR_SERVICE_SILENT_ID,
    ATTR_SERVICE_START,
    ATTR_SERVICE_WEEKDAYS,
    DOMAIN,
)
from custom_components.xplora_watch.pyxplora_api.exception_classes import AuthError, RateLimitError
from custom_components.xplora_watch.pyxplora_api.pyxplora_api_async import FetchError
from custom_components.xplora_watch.services import (
    BASE_CREATE_ALARM_SERVICE_SCHEMA,
    BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA,
    BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA,
    XploraAlarmService,
    XploraSilentService,
)

WUID = "watch-1"
UNIQUE_ID = "uid-1"


async def _passthrough_recovery(coro_factory):
    """Stand-in for the real coordinator's `_with_recovery`: just run the factory.

    The single-flight token-recovery ladder is covered against a real controller in
    `coordinator/test_token_recovery_gate.py`; here the controller is a MagicMock, so the gate
    can only act as a transparent passthrough (an `AuthError` raised by the factory propagates to
    the service's terminal handler, exactly as it would after exhausted recovery).
    """
    return await coro_factory()


def _install(hass: HomeAssistant, wuids: tuple[str, ...] = (WUID,)) -> MagicMock:
    """Install a MagicMock coordinator + a real config entry with one device per watch.

    Returns the coordinator; ``coord._device_ids`` maps each wuid to its HA device id (use
    ``_kwargs`` to build the ``device_id`` target payload).
    """
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UNIQUE_ID, data={}, options={})
    entry.add_to_hass(hass)

    coord = MagicMock()
    coord.controller = MagicMock()
    coord._entry = entry
    coord.data = {w: {"alarm": [], "silent": []} for w in wuids}
    coord.async_set_updated_data = MagicMock()
    coord.controller.getWatchUserIDs.return_value = list(wuids)
    coord._with_recovery = _passthrough_recovery
    # Default to a Guardian account (fail-open): the Contact-gate tests below override this. Without
    # it, a bare MagicMock would return a truthy stub from is_confirmed_contact and wrongly gate
    # every Guardian-path test.
    coord.is_confirmed_contact = MagicMock(return_value=False)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    registry = dr.async_get(hass)
    coord._device_ids = {
        w: registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{UNIQUE_ID}_{w}")}).id for w in wuids
    }
    return coord


def _service(hass: HomeAssistant, coord: MagicMock, cls):
    return cls(hass, coord._entry.entry_id)


def _kwargs(coord: MagicMock, wuids: tuple[str, ...] = (WUID,), **extra) -> dict:
    return {"kwargs": {"device_id": [coord._device_ids[w] for w in wuids], **extra}}


async def test_create_alarm_converts_and_refreshes(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.addAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}])

    await _service(hass, coord, XploraAlarmService).async_create(
        **_kwargs(coord, **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon", "tue"], ATTR_SERVICE_NAME: "Wake"})
    )

    # 08:00 -> 480 min; ["mon","tue"] -> "0110000" (Sun-first index).
    coord.controller.addAlarmTime.assert_awaited_once_with(WUID, 480, "0110000", "Wake")
    # The mutated list was re-fetched and pushed to the entities.
    coord.controller.getWatchAlarm.assert_awaited_once_with(WUID)
    coord.async_set_updated_data.assert_called_once()
    assert coord.data[WUID]["alarm"] == [{"id": "a1", "status": "ENABLE"}]


async def test_update_alarm_only_forwards_supplied_fields(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.modifyAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await _service(hass, coord, XploraAlarmService).async_update(
        **_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_START: "09:30"})
    )

    # weekdays/name omitted -> passed as None so the mutation leaves them unchanged.
    coord.controller.modifyAlarmTime.assert_awaited_once_with("a1", 570, None, None)


async def test_delete_alarm(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.removeAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await _service(hass, coord, XploraAlarmService).async_delete(**_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1"}))

    coord.controller.removeAlarmTime.assert_awaited_once_with("a1")
    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableAlarmTime"), (False, "setDisableAlarmTime")])
async def test_set_alarm_enabled(hass: HomeAssistant, enabled: bool, method: str) -> None:
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await _service(hass, coord, XploraAlarmService).async_set_enabled(
        **_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_ENABLED: enabled})
    )

    getattr(coord.controller, method).assert_awaited_once_with("a1")


async def test_create_silent_converts_and_refreshes(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.addSilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=[{"id": "s1", "status": "ENABLE"}])

    await _service(hass, coord, XploraSilentService).async_create(
        **_kwargs(coord, **{ATTR_SERVICE_START: "22:00", ATTR_SERVICE_END: "07:00", ATTR_SERVICE_WEEKDAYS: ["sat", "sun"]})
    )

    # 22:00 -> 1320, 07:00 -> 420; ["sat","sun"] -> "1000001".
    coord.controller.addSilentTime.assert_awaited_once_with(WUID, 1320, 420, "1000001")
    assert coord.data[WUID]["silent"] == [{"id": "s1", "status": "ENABLE"}]


async def test_update_silent_partial(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.modifySilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=[])

    await _service(hass, coord, XploraSilentService).async_update(
        **_kwargs(coord, **{ATTR_SERVICE_SILENT_ID: "s1", ATTR_SERVICE_END: "08:15"})
    )

    coord.controller.modifySilentTime.assert_awaited_once_with("s1", None, 495, None)


async def test_refresh_skips_on_fetch_error(hass: HomeAssistant) -> None:
    """If the post-mutation re-fetch fails, the coordinator data is left untouched."""
    coord = _install(hass)
    coord.controller.removeSilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=FetchError("SlientTimes", "boom"))

    await _service(hass, coord, XploraSilentService).async_delete(**_kwargs(coord, **{ATTR_SERVICE_SILENT_ID: "s1"}))

    coord.async_set_updated_data.assert_not_called()
    assert coord.data[WUID]["silent"] == []


async def test_create_alarm_auth_error_is_clean(hass: HomeAssistant, caplog) -> None:
    # Recovery exhausted -> a clean per-type warning (no raw traceback); nothing succeeded, so the
    # call surfaces an error toast rather than returning silently (best-effort fan-out, ADR 0004).
    coord = _install(hass)
    coord.controller.addAlarmTime = AsyncMock(side_effect=AuthError())

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_create(
            **_kwargs(coord, **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""})
        )

    assert err.value.translation_key == "nothing_actioned"
    assert "session token expired" in caplog.text
    coord.async_set_updated_data.assert_not_called()


async def test_create_alarm_stops_after_first_recoverable_failure(hass: HomeAssistant, caplog) -> None:
    """Ban defense (break-account, ADR 0004): a recoverable error stops the REST of that account's
    watches instead of hammering them (they'd hit the same expired token / rate limit). With two
    targets on one account and the first call raising RateLimitError, the controller is called
    exactly once; nothing succeeded, so the call also surfaces an error toast.
    """
    coord = _install(hass, wuids=("watch-1", "watch-2"))
    coord.controller.addAlarmTime = AsyncMock(side_effect=RateLimitError("429"))

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_create(
            **_kwargs(
                coord, wuids=("watch-1", "watch-2"), **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""}
            )
        )

    assert err.value.translation_key == "nothing_actioned"
    coord.controller.addAlarmTime.assert_awaited_once()  # broke after watch-1; watch-2 never attempted
    assert "rate limit" in caplog.text


def test_create_alarm_schema_requires_core_fields() -> None:
    with pytest.raises(vol.Invalid):
        BASE_CREATE_ALARM_SERVICE_SCHEMA({"device_id": [WUID]})  # missing start/weekdays
    # A complete payload validates and applies the default empty name.
    out = BASE_CREATE_ALARM_SERVICE_SCHEMA({"device_id": [WUID], ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"]})
    assert out[ATTR_SERVICE_NAME] == ""


# --------------------------------------------------------------------------- bulk turn-all services


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableAlarmTime"), (False, "setDisableAlarmTime")])
async def test_turn_all_alarms_toggles_every_entry(hass: HomeAssistant, enabled: bool, method: str) -> None:
    """Every alarm the watch reports is toggled, and the list is re-fetched once to refresh entities."""
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    # getWatchAlarm serves BOTH the enumerate-ids fetch and the post-toggle `_refresh_list` fetch.
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}, {"id": "a2", "status": "DISABLE"}])

    await _service(hass, coord, XploraAlarmService).async_set_all_enabled(enabled, **_kwargs(coord))

    toggler = getattr(coord.controller, method)
    assert toggler.await_count == 2
    toggler.assert_any_await("a1")
    toggler.assert_any_await("a2")
    # Enumerate + refresh = two list fetches; entities updated with the refreshed list.
    assert coord.controller.getWatchAlarm.await_count == 2
    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize("enabled", [True, False])
async def test_turn_all_alarms_noop_is_confirmed_via_refresh(hass: HomeAssistant, enabled: bool) -> None:
    """A no-op alarm toggle must NOT surface as ``watch_offline`` (ADR 0012).

    Setting an alarm to the state it already has makes the server reject the ``modifyAlarm`` with a
    generic error, so the toggle call returns falsy (a provisional refusal). Because that error is
    indistinguishable from a real failure, we confirm against the post-write re-fetch instead: when
    the list already shows every alarm in the requested state, the intent holds and the call succeeds
    silently rather than raising.
    """
    coord = _install(hass)
    method = "setEnableAlarmTime" if enabled else "setDisableAlarmTime"
    setattr(coord.controller, method, AsyncMock(return_value=False))  # server no-op rejection -> falsy
    target = "ENABLE" if enabled else "DISABLE"
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": target}, {"id": "a2", "status": target}])

    # Must not raise: every alarm is already in the requested state per the refresh.
    await _service(hass, coord, XploraAlarmService).async_set_all_enabled(enabled, **_kwargs(coord))

    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize("enabled", [True, False])
async def test_turn_all_alarms_genuine_failure_still_surfaces(hass: HomeAssistant, enabled: bool) -> None:
    """Fail-loud is preserved (ADR 0012): a falsy toggle whose alarm did NOT reach the requested
    state after the re-fetch still raises ``watch_offline`` -- the confirmation only rescues true
    no-ops, never a genuine failure."""
    coord = _install(hass)
    method = "setEnableAlarmTime" if enabled else "setDisableAlarmTime"
    setattr(coord.controller, method, AsyncMock(return_value=False))
    off_target = "DISABLE" if enabled else "ENABLE"  # the alarm is NOT in the requested state
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": off_target}])

    with pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(enabled, **_kwargs(coord))

    assert err.value.translation_key == "watch_offline"


@pytest.mark.parametrize("enabled", [True, False])
async def test_set_alarm_enabled_noop_is_confirmed_via_refresh(hass: HomeAssistant, enabled: bool) -> None:
    """The single-alarm toggle applies the same confirmation (ADR 0012): a no-op that the server
    rejects is not surfaced as ``watch_offline`` when the re-fetch shows the alarm already in the
    requested state."""
    coord = _install(hass)
    method = "setEnableAlarmTime" if enabled else "setDisableAlarmTime"
    setattr(coord.controller, method, AsyncMock(return_value=False))  # no-op rejection -> falsy
    target = "ENABLE" if enabled else "DISABLE"
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": target}])

    # Must not raise: the alarm is already in the requested state.
    await _service(hass, coord, XploraAlarmService).async_set_enabled(
        **_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_ENABLED: enabled})
    )


async def test_turn_all_alarms_mixed_real_toggle_and_noop_succeeds(hass: HomeAssistant) -> None:
    """The headline case (ADR 0012): "turn all on" where one alarm really flips and another is
    already on. The no-op's provisional refusal must not sink the whole call -- the post-write
    re-fetch shows every alarm on target, so the watch is confirmed a success. This is the mixed
    real-success + no-op on one watch that the per-watch worst-wins collapse would otherwise report
    as a refusal.
    """
    coord = _install(hass)
    # a1 is off -> a real enable the server accepts (True); a2 is already on -> a no-op the server
    # rejects (False).
    coord.controller.setEnableAlarmTime = AsyncMock(side_effect=lambda aid: aid == "a1")
    # Enumerate sees the pre-toggle mix; the post-toggle refresh sees both enabled.
    coord.controller.getWatchAlarm = AsyncMock(
        side_effect=[
            [{"id": "a1", "status": "DISABLE"}, {"id": "a2", "status": "ENABLE"}],
            [{"id": "a1", "status": "ENABLE"}, {"id": "a2", "status": "ENABLE"}],
        ]
    )

    # Must not raise: every alarm is on target after the toggle.
    await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    coord.async_set_updated_data.assert_called_once()


async def test_turn_all_alarms_refresh_failure_cannot_confirm(hass: HomeAssistant, caplog) -> None:
    """A failed confirming re-fetch must NOT rescue a no-op's provisional refusal (ADR 0012): with no
    authoritative end state to check, the call fails loudly rather than guessing success."""
    coord = _install(hass)
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=False)  # no-op rejection -> falsy
    # Enumerate succeeds; the post-toggle refresh fails, so the end state can't be confirmed.
    coord.controller.getWatchAlarm = AsyncMock(side_effect=[[{"id": "a1", "status": "ENABLE"}], FetchError("Alarms", "boom")])

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    assert err.value.translation_key == "watch_offline"


@pytest.mark.parametrize("enabled", [True, False])
async def test_set_alarm_enabled_genuine_failure_still_surfaces(hass: HomeAssistant, enabled: bool) -> None:
    """Single-handler fail-loud guard (ADR 0012): a falsy toggle whose alarm did NOT reach the
    requested state after the re-fetch still raises ``watch_offline`` -- the confirmation rescues only
    a true no-op, never a genuine failure (this pins the single-alarm path, mirroring the bulk guard)."""
    coord = _install(hass)
    method = "setEnableAlarmTime" if enabled else "setDisableAlarmTime"
    setattr(coord.controller, method, AsyncMock(return_value=False))
    off_target = "DISABLE" if enabled else "ENABLE"  # the alarm is NOT in the requested state
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": off_target}])

    with pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_set_enabled(
            **_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_ENABLED: enabled})
        )

    assert err.value.translation_key == "watch_offline"


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableSilentTime"), (False, "setDisableSilentTime")])
async def test_turn_all_silents_toggles_every_entry(hass: HomeAssistant, enabled: bool, method: str) -> None:
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    coord.controller.getSilentTime = AsyncMock(return_value=[{"id": "s1", "status": "ENABLE"}, {"id": "s2", "status": "ENABLE"}])

    await _service(hass, coord, XploraSilentService).async_set_all_enabled(enabled, **_kwargs(coord))

    toggler = getattr(coord.controller, method)
    assert toggler.await_count == 2
    toggler.assert_any_await("s1")
    toggler.assert_any_await("s2")
    coord.async_set_updated_data.assert_called_once()


async def test_turn_all_silents_fetch_error_surfaces_loudly(hass: HomeAssistant, caplog) -> None:
    """Symmetry with the alarm case: a failed silent-time enumerate must fail loudly, not report a
    silent success. Both bulk handlers share the ``_Account.call`` FetchError handling, so this pins
    the silent path against a regression there."""
    coord = _install(hass)
    coord.controller.setEnableSilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=FetchError("SlientTimes", "boom"))

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraSilentService).async_set_all_enabled(True, **_kwargs(coord))

    assert err.value.translation_key == "nothing_actioned"
    coord.controller.setEnableSilentTime.assert_not_awaited()
    coord.async_set_updated_data.assert_not_called()
    assert "boom" in caplog.text


async def test_turn_all_alarms_empty_list_is_idempotent(hass: HomeAssistant) -> None:
    """No alarms -> no toggle calls, but the refresh still runs (harmless no-op)."""
    coord = _install(hass)
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    coord.controller.setEnableAlarmTime.assert_not_awaited()
    coord.async_set_updated_data.assert_called_once()
    assert coord.data[WUID]["alarm"] == []


async def test_turn_all_alarms_fetch_error_surfaces_loudly(hass: HomeAssistant, caplog) -> None:
    """A failed enumerate fetch skips that watch's mutations AND fails loudly.

    The list fetcher returns a ``FetchError`` sentinel (rather than raising) when it exhausts its
    retries; that is a failure, not a truthy success, so on the only targeted watch the call must
    surface an error toast -- never report silent success when the alarm list could not be read
    (fail loud on fetch errors).
    """
    coord = _install(hass)
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=FetchError("Alarms", "boom"))

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    assert err.value.translation_key == "nothing_actioned"
    coord.controller.setEnableAlarmTime.assert_not_awaited()
    coord.async_set_updated_data.assert_not_called()
    assert "boom" in caplog.text


async def test_turn_all_alarms_fetch_error_on_one_watch_is_partial_not_silent(hass: HomeAssistant, caplog) -> None:
    """Two watches on one account: a watch whose enumerate fetch-errors is a *gap* in the partial-
    success notification, not a silent success (fail loud on fetch errors). The other watch succeeds,
    so the call does not raise -- but it must NOTIFY about the failed watch rather than dismissing the
    run as clean, which is exactly what a phantom fetch-success would have produced.
    """
    coord = _install(hass, wuids=("watch-1", "watch-2"))
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=True)
    # watch-1's enumerate fails; watch-2's enumerate + its post-toggle refresh both succeed.
    coord.controller.getWatchAlarm = AsyncMock(
        side_effect=[FetchError("Alarms", "boom"), [{"id": "a1", "status": "DISABLE"}], [{"id": "a1", "status": "ENABLE"}]]
    )

    with caplog.at_level(logging.WARNING), patch("custom_components.xplora_watch.services.persistent_notification") as pn:
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord, wuids=("watch-1", "watch-2")))

    coord.controller.setEnableAlarmTime.assert_awaited_once_with("a1")  # only watch-2 toggled
    pn.async_create.assert_called_once()  # notified about the failed watch-1 ...
    pn.async_dismiss.assert_not_called()  # ... not dismissed as a clean success
    assert "boom" in caplog.text


async def test_turn_all_alarms_stops_after_first_recoverable_failure(hass: HomeAssistant, caplog) -> None:
    """Ban defense: a recoverable error on the first toggle aborts the rest (no refresh either); the
    watch was not actioned, so the call surfaces an error toast (best-effort fan-out, ADR 0004)."""
    coord = _install(hass)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}, {"id": "a2", "status": "ENABLE"}])
    coord.controller.setEnableAlarmTime = AsyncMock(side_effect=RateLimitError("429"))

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    assert err.value.translation_key == "nothing_actioned"
    coord.controller.setEnableAlarmTime.assert_awaited_once()  # broke after a1; a2 never attempted
    coord.async_set_updated_data.assert_not_called()
    assert "rate limit" in caplog.text


def test_turn_all_schemas_accept_a_device_target() -> None:
    for schema in (BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA, BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA):
        # No service-specific fields are required (the handler enumerates entries itself); a device
        # target validates, and the empty resolution is rejected at runtime, not by the schema.
        assert schema({"device_id": [WUID]}) == {"device_id": [WUID]}
        assert schema({}) == {}


# ----------------------------------------------------------------- Contact gate (Guardian-only CRUD)
#
# Alarm/silent CRUD is a Guardian-only control action. A confirmed Contact (role resolved and not the
# Guardian) is refused with a ServiceValidationError as a client policy (ref:XW-009); the mutation
# must never be sent. The gate fails open, so the Guardian-path tests above need no extra wiring
# (``_install`` defaults ``is_confirmed_contact`` to False).


async def test_create_alarm_contact_is_blocked(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.addAlarmTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError) as err:
        await _service(hass, coord, XploraAlarmService).async_create(
            **_kwargs(coord, **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""})
        )

    # Localized client-policy error naming the blocked action.
    assert err.value.translation_key == "not_guardian"
    assert err.value.translation_placeholders == {"action": "change the watch's alarms"}
    coord.controller.addAlarmTime.assert_not_awaited()  # gated before the mutation


async def test_update_alarm_contact_is_blocked_before_mutation(hass: HomeAssistant) -> None:
    # async_update fires the mutation FIRST and only then iterates targets to refresh, so the gate
    # must run up front -- assert the modify call never happens for a Contact.
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.modifyAlarmTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError):
        await _service(hass, coord, XploraAlarmService).async_update(
            **_kwargs(coord, **{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_START: "09:30"})
        )

    coord.controller.modifyAlarmTime.assert_not_awaited()


async def test_set_silent_enabled_contact_is_blocked(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.setEnableSilentTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError):
        await _service(hass, coord, XploraSilentService).async_set_enabled(
            **_kwargs(coord, **{ATTR_SERVICE_SILENT_ID: "s1", ATTR_SERVICE_ENABLED: True})
        )

    coord.controller.setEnableSilentTime.assert_not_awaited()


async def test_turn_all_alarms_contact_is_blocked(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}])

    with pytest.raises(ServiceValidationError):
        await _service(hass, coord, XploraAlarmService).async_set_all_enabled(True, **_kwargs(coord))

    coord.controller.getWatchAlarm.assert_not_awaited()  # not even the enumerate fetch fires


async def test_create_alarm_mixed_selection_skips_contacts_and_proceeds_for_guardian(hass: HomeAssistant, caplog) -> None:
    # Selecting a Guardian watch AND a Contact watch on one account: the Guardian watch proceeds and
    # the Contact watch is skipped with a warning -- no error is raised.
    coord = _install(hass, wuids=("watch-guardian", "watch-contact"))
    coord.is_confirmed_contact = MagicMock(side_effect=lambda wuid: wuid == "watch-contact")
    coord.controller.addAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    with caplog.at_level(logging.WARNING):
        await _service(hass, coord, XploraAlarmService).async_create(
            **_kwargs(
                coord,
                wuids=("watch-guardian", "watch-contact"),
                **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""},
            )
        )

    coord.controller.addAlarmTime.assert_awaited_once()  # only the Guardian watch
    assert coord.controller.addAlarmTime.await_args.args[0] == "watch-guardian"
    assert "watch-contact" in caplog.text
    assert "guardian" in caplog.text.lower()


async def test_create_alarm_all_contacts_raises_and_sends_nothing(hass: HomeAssistant) -> None:
    # The account guards NONE of the targeted watches -> the error is raised and no mutation is sent.
    coord = _install(hass, wuids=("contact-1", "contact-2"))
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.addAlarmTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError):
        await _service(hass, coord, XploraAlarmService).async_create(
            **_kwargs(
                coord,
                wuids=("contact-1", "contact-2"),
                **{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""},
            )
        )

    coord.controller.addAlarmTime.assert_not_awaited()
