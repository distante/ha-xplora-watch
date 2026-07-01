"""Best-effort multi-account service fan-out (ADR 0004).

A single service call can resolve to watches across several accounts (an area/floor/label target or
a multi-device pick). These drive the shared fan-out executor end-to-end from the ONLY public seam --
``hass.services.async_call`` -- against real device/entity registries and real coordinators, and
observe behaviour three ways (ADR 0004's testing decision):

- which watches were actioned -> the controller mock's call args;
- zero-success -> ``ServiceValidationError`` + its ``translation_key``;
- partial success -> the ``persistent_notification`` surface (created / dismissed; stable id).

The executor and the per-account primitives are deliberately NOT tested directly -- they are
implementation detail behind this seam.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import ATTR_SERVICE_REBOOT, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.pyxplora_api.exception_classes import RateLimitError
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target

WATCH_B = "watch-id-b"
WATCH_C = "watch-id-c"


async def _reboot(hass: HomeAssistant, device_ids: list[str]) -> None:
    await hass.services.async_call(DOMAIN, ATTR_SERVICE_REBOOT, {"device_id": device_ids}, blocking=True)


async def test_mixed_guardian_and_outcome_across_accounts_actions_what_it_can(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    make_account,
) -> None:
    """The headline case: Guardian on account A + Contact on account B + a rate-limited account C.

    The watch we control (A) is actioned; the Contact watch (B) is dropped BEFORE any request (the
    server never sees a doomed call); the rate-limited account (C) is attempted and fails -- and does
    NOT cancel the work on A. Because at least one watch succeeded, the call does not raise; a single
    notification lists what was skipped.
    """
    coordinator.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]
    account_b = await make_account("dad@example.com", wuids=(WATCH_B,), contacts=(WATCH_B,))
    account_b.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]
    account_c = await make_account("aunt@example.com", wuids=(WATCH_C,))
    account_c.controller.reboot = AsyncMock(side_effect=RateLimitError("429"))  # type: ignore[method-assign]

    devices_a = await setup_service_target(hass, coordinator)
    devices_b = await setup_service_target(hass, account_b, wuids=(WATCH_B,))
    devices_c = await setup_service_target(hass, account_c, wuids=(WATCH_C,))

    with patch("custom_components.xplora_watch.services.persistent_notification") as pn:
        # No raise: account A succeeded, so the call is a partial success, not a failure.
        await _reboot(hass, [devices_a[DEFAULT_WUID], devices_b[WATCH_B], devices_c[WATCH_C]])

    coordinator.controller.reboot.assert_awaited_once_with(DEFAULT_WUID)  # the guarded watch ran
    account_b.controller.reboot.assert_not_awaited()  # Contact-gated: never sent to the server
    account_c.controller.reboot.assert_awaited_once_with(WATCH_C)  # attempted, then errored

    pn.async_create.assert_called_once()
    kwargs = pn.async_create.call_args.kwargs
    assert kwargs["notification_id"] == f"{DOMAIN}_{ATTR_SERVICE_REBOOT}"
    # The service is named in title + body so, with several automations, the operator knows which
    # call produced this notification.
    assert ATTR_SERVICE_REBOOT in kwargs["title"]
    assert ATTR_SERVICE_REBOOT in kwargs["message"]
    assert "contact" in kwargs["message"].lower()
    assert "rate-limited" in kwargs["message"].lower()


async def test_zero_success_across_accounts_raises_nothing_actioned_with_details(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    make_account,
) -> None:
    """A call that resolves ONLY to a Contact watch + a rate-limited account achieves nothing -> a
    single error enumerating every reason (mixed zero-success uses the generic ``nothing_actioned``)."""
    coordinator.is_admin = {DEFAULT_WUID: False}  # account A is only a Contact of its watch
    account_c = await make_account("aunt@example.com", wuids=(WATCH_C,))
    account_c.controller.reboot = AsyncMock(side_effect=RateLimitError("429"))  # type: ignore[method-assign]

    devices_a = await setup_service_target(hass, coordinator)
    devices_c = await setup_service_target(hass, account_c, wuids=(WATCH_C,))

    with pytest.raises(ServiceValidationError) as err:
        await _reboot(hass, [devices_a[DEFAULT_WUID], devices_c[WATCH_C]])

    assert err.value.translation_key == "nothing_actioned"
    details = err.value.translation_placeholders["details"].lower()
    assert "contact" in details
    assert "rate-limited" in details


async def test_all_contacts_across_accounts_raises_not_guardian(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    make_account,
) -> None:
    """Every targeted watch is Contact-only -> the homogeneous ``not_guardian`` key, and NO request."""
    coordinator.is_admin = {DEFAULT_WUID: False}
    coordinator.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]
    account_b = await make_account("dad@example.com", wuids=(WATCH_B,), contacts=(WATCH_B,))
    account_b.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]

    devices_a = await setup_service_target(hass, coordinator)
    devices_b = await setup_service_target(hass, account_b, wuids=(WATCH_B,))

    with pytest.raises(ServiceValidationError) as err:
        await _reboot(hass, [devices_a[DEFAULT_WUID], devices_b[WATCH_B]])

    assert err.value.translation_key == "not_guardian"
    assert err.value.translation_placeholders == {"action": "reboot the watch"}
    coordinator.controller.reboot.assert_not_awaited()
    account_b.controller.reboot.assert_not_awaited()


async def test_all_offline_raises_watch_offline(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
) -> None:
    """Every reachable watch refuses (offline) -> the homogeneous ``watch_offline`` key, not a silent success."""
    coordinator.controller.reboot = AsyncMock(return_value=False)  # type: ignore[method-assign]
    devices = await setup_service_target(hass, coordinator)

    with pytest.raises(ServiceValidationError) as err:
        await _reboot(hass, [devices[DEFAULT_WUID]])

    assert err.value.translation_key == "watch_offline"


async def test_transient_error_on_one_account_does_not_block_the_next(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    make_account,
) -> None:
    """A rate-limit on one account must not cancel work on another whose session is healthy."""
    coordinator.controller.reboot = AsyncMock(side_effect=RateLimitError("429"))  # type: ignore[method-assign]
    account_c = await make_account("aunt@example.com", wuids=(WATCH_C,))
    account_c.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]

    devices_a = await setup_service_target(hass, coordinator)
    devices_c = await setup_service_target(hass, account_c, wuids=(WATCH_C,))

    with patch("custom_components.xplora_watch.services.persistent_notification"):
        await _reboot(hass, [devices_a[DEFAULT_WUID], devices_c[WATCH_C]])

    # The healthy account still ran even though the other account was throttled.
    account_c.controller.reboot.assert_awaited_once_with(WATCH_C)


async def test_partial_success_notification_self_heals_on_a_later_clean_run(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    make_account,
) -> None:
    """A partial run fires ONE notification (stable id); a later fully-clean run dismisses it."""
    coordinator.controller.reboot = AsyncMock(return_value=True)  # type: ignore[method-assign]
    account_c = await make_account("aunt@example.com", wuids=(WATCH_C,))
    account_c.controller.reboot = AsyncMock(return_value=False)  # offline

    devices_a = await setup_service_target(hass, coordinator)
    devices_c = await setup_service_target(hass, account_c, wuids=(WATCH_C,))

    with patch("custom_components.xplora_watch.services.persistent_notification") as pn:
        # Partial: A succeeds, C offline -> a single notification created under the stable id.
        await _reboot(hass, [devices_a[DEFAULT_WUID], devices_c[WATCH_C]])
        pn.async_create.assert_called_once()
        assert pn.async_create.call_args.kwargs["notification_id"] == f"{DOMAIN}_{ATTR_SERVICE_REBOOT}"

        # Clean: only A, everything succeeds -> the stale notification is dismissed (self-heal).
        await _reboot(hass, [devices_a[DEFAULT_WUID]])
        pn.async_dismiss.assert_called_once_with(hass, f"{DOMAIN}_{ATTR_SERVICE_REBOOT}")


def test_module_no_longer_exposes_the_old_per_call_guard() -> None:
    """The old copy-paste-prone ``_api_call_guard`` is gone -- policy now lives in the executor."""
    from custom_components.xplora_watch import services as svc

    assert not hasattr(svc.XploraService, "_api_call_guard")
    assert not hasattr(svc, "_ApiCallGuard")
