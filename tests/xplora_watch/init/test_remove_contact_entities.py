"""Tests for _async_remove_contact_guardian_only_entities (upgrade cleanup) in __init__.py.

A watch the account is only a *Contact* of no longer gets the Guardian-only entities (battery,
distance, alarm/silent lists, location-history; charging, safe-zone; reboot/shutdown/refresh-
functions buttons; every device tracker). This sweep removes any such entity a previous version
already registered, keying off the per-watch role the coordinator derives from `deviceList`
(ref:XW-009). The integration-level Seam A coverage lives in
tests/xplora_watch/integration/test_contact_entities.py; these exercise the matching rules directly
(per-watch survival, fail-open, and the Ward-name false-match guard) with full control over roles.
"""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import _async_remove_contact_guardian_only_entities
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator

CONTACT_WUID = "watch-id-001"
GUARDIAN_WUID = "watch-id-002"


def _make_uid(coordinator: XploraDataUpdateCoordinator, ward: str, kind: str, wuid: str) -> str:
    """Build a unique id exactly as the entity platforms do: ``{ward}_watch_{kind}_{wuid}_{user_id}``."""
    return f"{ward}_watch_{kind}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()


def _seed(registry, entry, domain: str, unique_id: str):
    return registry.async_get_or_create(domain, "xplora_watch", unique_id, config_entry=entry)


async def test_removes_guardian_only_entities_and_keeps_contact_entities(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    coordinator.is_admin = {CONTACT_WUID: False}
    registry = er.async_get(hass)

    # Guardian-only across all four platforms, plus both kinds of device tracker.
    battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "battery", CONTACT_WUID))
    location_history = _seed(
        registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "location_history", CONTACT_WUID)
    )
    charging = _seed(registry, mock_config_entry_phone, "binary_sensor", _make_uid(coordinator, "Kid One", "charging", CONTACT_WUID))
    reboot = _seed(registry, mock_config_entry_phone, "button", _make_uid(coordinator, "Kid One", "reboot", CONTACT_WUID))
    refresh = _seed(registry, mock_config_entry_phone, "button", _make_uid(coordinator, "Kid One", "refresh_functions", CONTACT_WUID))
    tracker = _seed(registry, mock_config_entry_phone, "device_tracker", _make_uid(coordinator, "Kid One", "tracker", CONTACT_WUID))
    safezone_tracker = _seed(
        registry, mock_config_entry_phone, "device_tracker", _make_uid(coordinator, "Kid One", "safezone_zone1", CONTACT_WUID)
    )

    # Kept-for-a-Contact entities (online state, steps, chat, last-update, the Update button).
    state = _seed(registry, mock_config_entry_phone, "binary_sensor", _make_uid(coordinator, "Kid One", "state", CONTACT_WUID))
    steps = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "step_day", CONTACT_WUID))
    message = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "message", CONTACT_WUID))
    update = _seed(registry, mock_config_entry_phone, "button", _make_uid(coordinator, "Kid One", "update", CONTACT_WUID))

    _async_remove_contact_guardian_only_entities(hass, mock_config_entry_phone, coordinator)

    for removed in (battery, location_history, charging, reboot, refresh, tracker, safezone_tracker):
        assert registry.async_get(removed.entity_id) is None
    for kept in (state, steps, message, update):
        assert registry.async_get(kept.entity_id) is not None


async def test_keeps_guardian_watch_entities(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    coordinator.is_admin = {GUARDIAN_WUID: True}
    registry = er.async_get(hass)
    battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid Two", "battery", GUARDIAN_WUID))
    tracker = _seed(registry, mock_config_entry_phone, "device_tracker", _make_uid(coordinator, "Kid Two", "tracker", GUARDIAN_WUID))

    _async_remove_contact_guardian_only_entities(hass, mock_config_entry_phone, coordinator)

    assert registry.async_get(battery.entity_id) is not None
    assert registry.async_get(tracker.entity_id) is not None


async def test_fails_open_for_unknown_role(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    # Role never resolved (wuid absent from is_admin) -> treated as a Guardian, nothing stripped.
    coordinator.is_admin = {}
    registry = er.async_get(hass)
    battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "battery", CONTACT_WUID))

    _async_remove_contact_guardian_only_entities(hass, mock_config_entry_phone, coordinator)

    assert registry.async_get(battery.entity_id) is not None


async def test_ward_name_like_a_key_does_not_cause_a_false_match(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    # A Ward named "Battery" puts "battery" in the leading ward-name segment of every unique id for
    # the watch; matching the kind token after `_watch_` means the kept online (state) sensor is not
    # mistaken for the Guardian-only battery sensor, while the real battery sensor is still removed.
    coordinator.is_admin = {CONTACT_WUID: False}
    registry = er.async_get(hass)
    kept_state = _seed(registry, mock_config_entry_phone, "binary_sensor", _make_uid(coordinator, "Battery", "state", CONTACT_WUID))
    real_battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Battery", "battery", CONTACT_WUID))

    _async_remove_contact_guardian_only_entities(hass, mock_config_entry_phone, coordinator)

    assert registry.async_get(kept_state.entity_id) is not None
    assert registry.async_get(real_battery.entity_id) is None


async def test_mixed_entry_strips_only_the_contact_watch(
    hass, mock_config_entry_phone: MockConfigEntry, coordinator: XploraDataUpdateCoordinator
) -> None:
    # One account that is a Contact of one watch and the Guardian of another: only the Contact
    # watch's Guardian-only entity is removed.
    coordinator.is_admin = {CONTACT_WUID: False, GUARDIAN_WUID: True}
    registry = er.async_get(hass)
    contact_battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid One", "battery", CONTACT_WUID))
    guardian_battery = _seed(registry, mock_config_entry_phone, "sensor", _make_uid(coordinator, "Kid Two", "battery", GUARDIAN_WUID))

    _async_remove_contact_guardian_only_entities(hass, mock_config_entry_phone, coordinator)

    assert registry.async_get(contact_battery.entity_id) is None
    assert registry.async_get(guardian_battery.entity_id) is not None
