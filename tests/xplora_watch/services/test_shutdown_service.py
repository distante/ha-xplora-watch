"""Tests for the ``shutdown`` service, driven through device targeting (ADR 0003).

Services are called the way Home Assistant calls them -- ``hass.services.async_call`` with a
``device_id`` -- against a real device-registry device, so these assert the full path: device ->
(account, wuid) resolution, the Guardian gate, and the backend result handling.
"""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.xplora_watch.const import ATTR_SERVICE_SHUTDOWN, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target


async def test_shutdown_admin_watch_succeeds(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.DEBUG):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SHUTDOWN, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    assert "Shutdown failed" not in caplog.text
    assert "Shutdown result: True" in caplog.text


async def test_shutdown_contact_watch_is_blocked(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    caplog,
) -> None:
    # A Contact (role resolved and not the Guardian: guardianType != "FIRST") may NOT shut the watch
    # down. The integration restricts this control action to the watch's Guardian as a client policy
    # (ref:XW-009): the service raises ServiceValidationError and the shutdown mutation is never sent.
    # (Reverses the old ref:XW-007 "non-primary guardian still succeeds" behavior; the chat half of
    # ref:XW-007 still holds.)
    coordinator.is_admin = {DEFAULT_WUID: False}
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.DEBUG), pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SHUTDOWN, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    assert "Shutdown result" not in caplog.text  # gated before the mutation, so it never fired


async def test_shutdown_rejected_by_backend_logs_warning_and_raises_watch_offline(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # The backend refuses the command (False) -- e.g. the watch is off/offline. The service says so
    # via a per-watch warning, and -- since that refused watch was the only target, so nothing
    # succeeded -- surfaces the homogeneous `watch_offline` error toast (ADR 0004), never a silent
    # success.
    graphql_operations["ShutDown"] = {"data": {"shutDown": False}}
    devices = await setup_service_target(hass, coordinator)

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SHUTDOWN, {"device_id": [devices[DEFAULT_WUID]]}, blocking=True)

    assert err.value.translation_key == "watch_offline"
    assert "Shutdown was not accepted" in caplog.text


async def test_shutdown_without_xplora_device_raises(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    # Targeting a non-Xplora device (or no device) resolves to no watch: the service rejects the call
    # with a clean ServiceValidationError instead of silently doing nothing.
    await setup_service_target(hass, coordinator)
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers={("some_other_domain", "not-a-watch")},
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SHUTDOWN, {"device_id": [foreign.id]}, blocking=True)

    assert "Shutdown result" not in caplog.text
