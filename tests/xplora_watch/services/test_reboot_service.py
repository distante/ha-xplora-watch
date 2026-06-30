"""Tests for XploraRebootService.async_reboot (ISSUE-14: parity with the app's reboot service)."""

from __future__ import annotations

import logging

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.services import XploraRebootService
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


def _register_coordinator(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> str:
    entry_id = coordinator._entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry_id] = coordinator
    return entry_id


async def test_reboot_admin_watch_succeeds(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    reboot_service = XploraRebootService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await reboot_service.async_reboot([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "Reboot failed" not in caplog.text
    assert "Reboot result: True" in caplog.text


async def test_reboot_contact_watch_is_blocked(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    caplog,
) -> None:
    # A Contact (role resolved and not the Guardian: guardianType != "FIRST") may NOT reboot the
    # watch. The integration restricts this control action to the watch's Guardian as a client policy
    # (ref:XW-009): the service raises ServiceValidationError and the reboot mutation is never sent.
    # (This reverses the old ref:XW-007 "non-primary guardian still succeeds" behavior; the chat half
    # of ref:XW-007 still holds.)
    coordinator.is_admin = {DEFAULT_WUID: False}
    entry_id = _register_coordinator(hass, coordinator)
    reboot_service = XploraRebootService(hass, entry_id)

    with caplog.at_level(logging.DEBUG), pytest.raises(ServiceValidationError) as err:
        await reboot_service.async_reboot([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    # Localized client-policy error, not a server rejection: a translation key + an {action} that
    # names what was blocked.
    assert err.value.translation_key == "not_guardian"
    assert err.value.translation_placeholders == {"action": "reboot the watch"}
    assert "Reboot result" not in caplog.text  # gated before the mutation, so it never fired


async def test_reboot_auth_error_logs_clean_after_bounded_recovery(
    hass: HomeAssistant,
    coordinator: XploraDataUpdateCoordinator,
    graphql_operations,
    caplog,
) -> None:
    # Token expires (E000004) on the reboot mutation itself (no admin/Contacts pre-check anymore).
    # The call routes through the coordinator's centralized single-flight recovery gate, so the
    # bounded ladder runs once (refresh -> at-most-one re-login -> one retry). The mutation keeps
    # returning E000004, so recovery is ultimately exhausted: the service must log a clean warning
    # and return, NOT propagate a raw traceback. (The old "services never re-login" stance was
    # superseded by centralization -- the single-flight gate makes the re-login storm-safe.)
    graphql_operations["reboot"] = {"errors": [{"code": "E000004"}], "data": {"reboot": None}}
    entry_id = _register_coordinator(hass, coordinator)
    reboot_service = XploraRebootService(hass, entry_id)

    with caplog.at_level(logging.DEBUG):
        await reboot_service.async_reboot([DEFAULT_WUID], kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "session token expired" in caplog.text
    assert "Reboot failed" not in caplog.text
    assert "Reboot result" not in caplog.text  # AuthError raised before the result is logged


async def test_non_list_targets_logs_warning_and_skips(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    entry_id = _register_coordinator(hass, coordinator)
    reboot_service = XploraRebootService(hass, entry_id)

    with caplog.at_level(logging.WARNING):
        await reboot_service.async_reboot(None, kwargs={"user": [f"{entry_id} (testuser)"]})

    assert "No watch ID or type" in caplog.text
