"""Device-targeting resolution shared by every service (ADR 0003).

A service call carries HA ``device_id`` / ``area_id`` targets; the handler resolves each to its
``(account, wuid)``. These assert the cross-cutting behavior once -- multi-device fan-out and area
expansion -- so the per-service test files can stay focused on their own logic.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch import services as svc
from custom_components.xplora_watch.const import ATTR_SERVICE_SEE, ATTR_SERVICE_SEND_MSG, DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID

from ..conftest import setup_service_target

SECOND_WUID = "watch-id-002"


async def test_multiple_devices_fan_out_to_each_watch(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """Selecting two watch devices on one account sends the action to BOTH watches."""
    # Pretend the account links two watches so both device identifiers resolve.
    coordinator.controller.getWatchUserIDs = lambda: [DEFAULT_WUID, SECOND_WUID]  # type: ignore[method-assign]
    coordinator.controller.sendText = AsyncMock(return_value=True)  # type: ignore[method-assign]
    devices = await setup_service_target(hass, coordinator, wuids=(DEFAULT_WUID, SECOND_WUID))

    await hass.services.async_call(
        DOMAIN,
        ATTR_SERVICE_SEND_MSG,
        {"device_id": [devices[DEFAULT_WUID], devices[SECOND_WUID]], "message": "hi"},
        blocking=True,
    )

    acted_on = {call.kwargs["wuid"] for call in coordinator.controller.sendText.await_args_list}
    assert acted_on == {DEFAULT_WUID, SECOND_WUID}


async def test_area_target_expands_to_xplora_devices_only(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator, caplog) -> None:
    """An ``area_id`` target resolves to the Xplora watch device(s) in that area; a non-Xplora
    device sharing the area is ignored (it has no DOMAIN identifier to resolve)."""
    devices = await setup_service_target(hass, coordinator)
    area = ar.async_get(hass).async_create("Kids Room")
    registry = dr.async_get(hass)
    registry.async_update_device(devices[DEFAULT_WUID], area_id=area.id)
    # A foreign device in the same area must NOT be dragged in.
    foreign = registry.async_get_or_create(
        config_entry_id=coordinator._entry.entry_id,
        identifiers={("some_other_domain", "not-a-watch")},
    )
    registry.async_update_device(foreign.id, area_id=area.id)

    with caplog.at_level(logging.DEBUG):
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SEE, {"area_id": [area.id]}, blocking=True)

    # The `see` handler logs once per resolved account; reaching it proves the area expanded to the
    # watch device (and only it -- the foreign device resolves to nothing).
    assert "update all information" in caplog.text
    assert DEFAULT_WUID in caplog.text


async def test_entity_id_target_resolves_via_its_device(hass: HomeAssistant, coordinator: XploraDataUpdateCoordinator) -> None:
    """The bundled Lovelace card targets a watch by one of its entities. An ``entity_id`` target is
    resolved to that entity's device, and from there to the (account, watch) -- so the card's service
    calls reach the right watch without it knowing the device id."""
    devices = await setup_service_target(hass, coordinator)
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{coordinator._entry.unique_id}_{DEFAULT_WUID}_battery",
        config_entry=coordinator._entry,
        device_id=devices[DEFAULT_WUID],
    )

    with patch.object(coordinator, "async_update_xplora_data", new=AsyncMock()) as mock_update:
        await hass.services.async_call(DOMAIN, ATTR_SERVICE_SEE, {"entity_id": [entity.entity_id]}, blocking=True)

    mock_update.assert_awaited_once_with([DEFAULT_WUID])


async def test_resolve_device_recovers_wuid_against_known_wuids_not_a_naive_split(hass: HomeAssistant) -> None:
    """ADR 0003: the wuid is recovered by MATCHING the device identifier against the coordinator's
    known wuids, never by splitting the identifier string -- because the account ``unique_id``
    (an email / phone number) may itself contain the separator. Here both the account id and the
    wuid carry underscores, so any ``rsplit("_")`` would recover the wrong wuid; the matcher must
    still return the exact one.
    """
    entry = MockConfigEntry(domain=DOMAIN, unique_id="acc_with_underscores", data={}, options={})
    entry.add_to_hass(hass)
    wuid = "watch_001_with_underscores"

    coord = MagicMock()
    coord._entry = entry
    coord.controller.getWatchUserIDs.return_value = [wuid]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.unique_id}_{wuid}")},
    )

    resolved = svc._resolve_device(hass, device.id)

    assert resolved is not None
    resolved_entry_id, resolved_coord, resolved_wuid = resolved
    assert resolved_entry_id == entry.entry_id
    assert resolved_coord is coord
    # The exact wuid -- a naive `"acc_with_underscores_watch_001_with_underscores".rsplit("_", 1)`
    # would have yielded "underscores", not this.
    assert resolved_wuid == wuid
