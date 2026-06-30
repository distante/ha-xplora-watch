"""Tests for the alarm / silent-time CRUD services (XploraAlarmService / XploraSilentService).

A MagicMock coordinator stands in for the real one: the services only touch
``coordinator.controller`` (CRUD + list fetchers), ``coordinator.data`` and
``coordinator.async_set_updated_data``. This isolates the conversion + refresh logic from the
network/GraphQL harness.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import (
    ATTR_SERVICE_ALARM_ID,
    ATTR_SERVICE_ENABLED,
    ATTR_SERVICE_END,
    ATTR_SERVICE_NAME,
    ATTR_SERVICE_SILENT_ID,
    ATTR_SERVICE_START,
    ATTR_SERVICE_TARGET,
    ATTR_SERVICE_USER,
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
ENTRY_ID = "entry-1"


async def _passthrough_recovery(coro_factory):
    """Stand-in for the real coordinator's `_with_recovery`: just run the factory.

    The single-flight token-recovery ladder is covered against a real controller in
    `coordinator/test_token_recovery_gate.py`; here the controller is a MagicMock, so the gate
    can only act as a transparent passthrough (an `AuthError` raised by the factory propagates to
    the service's terminal handler, exactly as it would after exhausted recovery).
    """
    return await coro_factory()


def _install(hass: HomeAssistant) -> MagicMock:
    """Install a MagicMock coordinator under hass.data and return it."""
    coord = MagicMock()
    coord.controller = MagicMock()
    coord.data = {WUID: {"alarm": [], "silent": []}}
    coord.async_set_updated_data = MagicMock()
    coord.controller.getWatchUserIDs.return_value = [WUID]
    coord._with_recovery = _passthrough_recovery
    # Default to a Guardian account (fail-open): the Contact-gate tests below override this. Without
    # it, a bare MagicMock would return a truthy stub from is_confirmed_contact and wrongly gate
    # every Guardian-path test.
    coord.is_confirmed_contact = MagicMock(return_value=False)
    hass.data.setdefault(DOMAIN, {})[ENTRY_ID] = coord
    return coord


def _kwargs(**extra) -> dict:
    return {"kwargs": {ATTR_SERVICE_TARGET: [WUID], ATTR_SERVICE_USER: [f"{ENTRY_ID} (parent)"], **extra}}


async def test_create_alarm_converts_and_refreshes(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.addAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}])

    await XploraAlarmService(hass, ENTRY_ID).async_create(
        **_kwargs(**{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon", "tue"], ATTR_SERVICE_NAME: "Wake"})
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

    await XploraAlarmService(hass, ENTRY_ID).async_update(**_kwargs(**{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_START: "09:30"}))

    # weekdays/name omitted -> passed as None so the mutation leaves them unchanged.
    coord.controller.modifyAlarmTime.assert_awaited_once_with("a1", 570, None, None)


async def test_delete_alarm(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.removeAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await XploraAlarmService(hass, ENTRY_ID).async_delete(**_kwargs(**{ATTR_SERVICE_ALARM_ID: "a1"}))

    coord.controller.removeAlarmTime.assert_awaited_once_with("a1")
    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableAlarmTime"), (False, "setDisableAlarmTime")])
async def test_set_alarm_enabled(hass: HomeAssistant, enabled: bool, method: str) -> None:
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await XploraAlarmService(hass, ENTRY_ID).async_set_enabled(**_kwargs(**{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_ENABLED: enabled}))

    getattr(coord.controller, method).assert_awaited_once_with("a1")


async def test_create_silent_converts_and_refreshes(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.addSilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=[{"id": "s1", "status": "ENABLE"}])

    await XploraSilentService(hass, ENTRY_ID).async_create(
        **_kwargs(**{ATTR_SERVICE_START: "22:00", ATTR_SERVICE_END: "07:00", ATTR_SERVICE_WEEKDAYS: ["sat", "sun"]})
    )

    # 22:00 -> 1320, 07:00 -> 420; ["sat","sun"] -> "1000001".
    coord.controller.addSilentTime.assert_awaited_once_with(WUID, 1320, 420, "1000001")
    assert coord.data[WUID]["silent"] == [{"id": "s1", "status": "ENABLE"}]


async def test_update_silent_partial(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.controller.modifySilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=[])

    await XploraSilentService(hass, ENTRY_ID).async_update(**_kwargs(**{ATTR_SERVICE_SILENT_ID: "s1", ATTR_SERVICE_END: "08:15"}))

    coord.controller.modifySilentTime.assert_awaited_once_with("s1", None, 495, None)


async def test_refresh_skips_on_fetch_error(hass: HomeAssistant) -> None:
    """If the post-mutation re-fetch fails, the coordinator data is left untouched."""
    coord = _install(hass)
    coord.controller.removeSilentTime = AsyncMock(return_value=True)
    coord.controller.getSilentTime = AsyncMock(return_value=FetchError("SlientTimes", "boom"))

    await XploraSilentService(hass, ENTRY_ID).async_delete(**_kwargs(**{ATTR_SERVICE_SILENT_ID: "s1"}))

    coord.async_set_updated_data.assert_not_called()
    assert coord.data[WUID]["silent"] == []


async def test_create_alarm_auth_error_is_clean(hass: HomeAssistant, caplog) -> None:
    coord = _install(hass)
    coord.controller.addAlarmTime = AsyncMock(side_effect=AuthError())

    with caplog.at_level(logging.WARNING):
        await XploraAlarmService(hass, ENTRY_ID).async_create(
            **_kwargs(**{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""})
        )

    assert "session token expired" in caplog.text
    coord.async_set_updated_data.assert_not_called()


async def test_create_alarm_stops_after_first_recoverable_failure(hass: HomeAssistant, caplog) -> None:
    """Ban defense: a per-watch loop must STOP after the first recoverable error instead of
    hammering the remaining watches (which would hit the same expired token / rate limit). With two
    targets and the first call raising RateLimitError, the controller is called exactly once.
    """
    coord = _install(hass)
    coord.controller.getWatchUserIDs.return_value = ["watch-1", "watch-2"]
    coord.controller.addAlarmTime = AsyncMock(side_effect=RateLimitError("429"))

    with caplog.at_level(logging.WARNING):
        await XploraAlarmService(hass, ENTRY_ID).async_create(
            **{
                "kwargs": {
                    ATTR_SERVICE_TARGET: ["watch-1", "watch-2"],
                    ATTR_SERVICE_USER: [f"{ENTRY_ID} (parent)"],
                    ATTR_SERVICE_START: "08:00",
                    ATTR_SERVICE_WEEKDAYS: ["mon"],
                    ATTR_SERVICE_NAME: "",
                }
            }
        )

    coord.controller.addAlarmTime.assert_awaited_once()  # broke after watch-1; watch-2 never attempted
    assert "rate limit" in caplog.text


def test_create_alarm_schema_requires_core_fields() -> None:
    with pytest.raises(vol.Invalid):
        BASE_CREATE_ALARM_SERVICE_SCHEMA({ATTR_SERVICE_TARGET: [WUID], ATTR_SERVICE_USER: [ENTRY_ID]})
    # A complete payload validates and applies the default empty name.
    out = BASE_CREATE_ALARM_SERVICE_SCHEMA(
        {ATTR_SERVICE_TARGET: [WUID], ATTR_SERVICE_USER: [ENTRY_ID], ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"]}
    )
    assert out[ATTR_SERVICE_NAME] == ""


# --------------------------------------------------------------------------- bulk turn-all services


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableAlarmTime"), (False, "setDisableAlarmTime")])
async def test_turn_all_alarms_toggles_every_entry(hass: HomeAssistant, enabled: bool, method: str) -> None:
    """Every alarm the watch reports is toggled, and the list is re-fetched once to refresh entities."""
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    # getWatchAlarm serves BOTH the enumerate-ids fetch and the post-toggle `_refresh_list` fetch.
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}, {"id": "a2", "status": "DISABLE"}])

    await XploraAlarmService(hass, ENTRY_ID).async_set_all_enabled(enabled, **_kwargs())

    toggler = getattr(coord.controller, method)
    assert toggler.await_count == 2
    toggler.assert_any_await("a1")
    toggler.assert_any_await("a2")
    # Enumerate + refresh = two list fetches; entities updated with the refreshed list.
    assert coord.controller.getWatchAlarm.await_count == 2
    coord.async_set_updated_data.assert_called_once()


@pytest.mark.parametrize(("enabled", "method"), [(True, "setEnableSilentTime"), (False, "setDisableSilentTime")])
async def test_turn_all_silents_toggles_every_entry(hass: HomeAssistant, enabled: bool, method: str) -> None:
    coord = _install(hass)
    setattr(coord.controller, method, AsyncMock(return_value=True))
    coord.controller.getSilentTime = AsyncMock(return_value=[{"id": "s1", "status": "ENABLE"}, {"id": "s2", "status": "ENABLE"}])

    await XploraSilentService(hass, ENTRY_ID).async_set_all_enabled(enabled, **_kwargs())

    toggler = getattr(coord.controller, method)
    assert toggler.await_count == 2
    toggler.assert_any_await("s1")
    toggler.assert_any_await("s2")
    coord.async_set_updated_data.assert_called_once()


async def test_turn_all_alarms_empty_list_is_idempotent(hass: HomeAssistant) -> None:
    """No alarms -> no toggle calls, but the refresh still runs (harmless no-op)."""
    coord = _install(hass)
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    await XploraAlarmService(hass, ENTRY_ID).async_set_all_enabled(True, **_kwargs())

    coord.controller.setEnableAlarmTime.assert_not_awaited()
    coord.async_set_updated_data.assert_called_once()
    assert coord.data[WUID]["alarm"] == []


async def test_turn_all_alarms_skips_watch_on_fetch_error(hass: HomeAssistant, caplog) -> None:
    """A failed enumerate fetch skips that watch entirely -- no toggles, no entity update."""
    coord = _install(hass)
    coord.controller.setEnableAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=FetchError("Alarms", "boom"))

    with caplog.at_level(logging.WARNING):
        await XploraAlarmService(hass, ENTRY_ID).async_set_all_enabled(True, **_kwargs())

    coord.controller.setEnableAlarmTime.assert_not_awaited()
    coord.async_set_updated_data.assert_not_called()
    assert "boom" in caplog.text


async def test_turn_all_alarms_stops_after_first_recoverable_failure(hass: HomeAssistant, caplog) -> None:
    """Ban defense: a recoverable error on the first toggle aborts the rest (no refresh either)."""
    coord = _install(hass)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}, {"id": "a2", "status": "ENABLE"}])
    coord.controller.setEnableAlarmTime = AsyncMock(side_effect=RateLimitError("429"))

    with caplog.at_level(logging.WARNING):
        await XploraAlarmService(hass, ENTRY_ID).async_set_all_enabled(True, **_kwargs())

    coord.controller.setEnableAlarmTime.assert_awaited_once()  # broke after a1; a2 never attempted
    coord.async_set_updated_data.assert_not_called()
    assert "rate limit" in caplog.text


def test_turn_all_schemas_require_target_and_user() -> None:
    for schema in (BASE_TURN_ALL_ALARMS_SERVICE_SCHEMA, BASE_TURN_ALL_SILENTS_SERVICE_SCHEMA):
        with pytest.raises(vol.Invalid):
            schema({ATTR_SERVICE_TARGET: [WUID]})  # missing user
        # target + user alone is a complete payload (the handler enumerates entries itself).
        assert schema({ATTR_SERVICE_TARGET: [WUID], ATTR_SERVICE_USER: [ENTRY_ID]})


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
        await XploraAlarmService(hass, ENTRY_ID).async_create(
            **_kwargs(**{ATTR_SERVICE_START: "08:00", ATTR_SERVICE_WEEKDAYS: ["mon"], ATTR_SERVICE_NAME: ""})
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
        await XploraAlarmService(hass, ENTRY_ID).async_update(**_kwargs(**{ATTR_SERVICE_ALARM_ID: "a1", ATTR_SERVICE_START: "09:30"}))

    coord.controller.modifyAlarmTime.assert_not_awaited()


async def test_set_silent_enabled_contact_is_blocked(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.setEnableSilentTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError):
        await XploraSilentService(hass, ENTRY_ID).async_set_enabled(**_kwargs(**{ATTR_SERVICE_SILENT_ID: "s1", ATTR_SERVICE_ENABLED: True}))

    coord.controller.setEnableSilentTime.assert_not_awaited()


async def test_turn_all_alarms_contact_is_blocked(hass: HomeAssistant) -> None:
    coord = _install(hass)
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[{"id": "a1", "status": "ENABLE"}])

    with pytest.raises(ServiceValidationError):
        await XploraAlarmService(hass, ENTRY_ID).async_set_all_enabled(True, **_kwargs())

    coord.controller.getWatchAlarm.assert_not_awaited()  # not even the enumerate fetch fires


async def test_create_alarm_mixed_all_skips_contacts_and_proceeds_for_guardian(hass: HomeAssistant, caplog) -> None:
    # With the special `all` target on a mixed account, the Guardian watch proceeds and the Contact
    # watch is skipped with a warning -- no error is raised.
    coord = _install(hass)
    coord.controller.getWatchUserIDs.return_value = ["watch-guardian", "watch-contact"]
    coord.is_confirmed_contact = MagicMock(side_effect=lambda wuid: wuid == "watch-contact")
    coord.data = {"watch-guardian": {"alarm": [], "silent": []}, "watch-contact": {"alarm": [], "silent": []}}
    coord.controller.addAlarmTime = AsyncMock(return_value=True)
    coord.controller.getWatchAlarm = AsyncMock(return_value=[])

    with caplog.at_level(logging.WARNING):
        await XploraAlarmService(hass, ENTRY_ID).async_create(
            **{
                "kwargs": {
                    ATTR_SERVICE_TARGET: ["all"],
                    ATTR_SERVICE_USER: [f"{ENTRY_ID} (parent)"],
                    ATTR_SERVICE_START: "08:00",
                    ATTR_SERVICE_WEEKDAYS: ["mon"],
                    ATTR_SERVICE_NAME: "",
                }
            }
        )

    coord.controller.addAlarmTime.assert_awaited_once()  # only the Guardian watch
    assert coord.controller.addAlarmTime.await_args.args[0] == "watch-guardian"
    assert "watch-contact" in caplog.text
    assert "guardian" in caplog.text.lower()


async def test_create_alarm_all_contacts_raises_and_sends_nothing(hass: HomeAssistant) -> None:
    # The account guards NONE of the targeted watches -> the error is raised and no mutation is sent.
    coord = _install(hass)
    coord.controller.getWatchUserIDs.return_value = ["contact-1", "contact-2"]
    coord.is_confirmed_contact = MagicMock(return_value=True)
    coord.controller.addAlarmTime = AsyncMock(return_value=True)

    with pytest.raises(ServiceValidationError):
        await XploraAlarmService(hass, ENTRY_ID).async_create(
            **{
                "kwargs": {
                    ATTR_SERVICE_TARGET: ["all"],
                    ATTR_SERVICE_USER: [f"{ENTRY_ID} (parent)"],
                    ATTR_SERVICE_START: "08:00",
                    ATTR_SERVICE_WEEKDAYS: ["mon"],
                    ATTR_SERVICE_NAME: "",
                }
            }
        )

    coord.controller.addAlarmTime.assert_not_awaited()
