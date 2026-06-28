"""Tests for XploraDataUpdateCoordinator.init() against the mocked GraphQL transport.

This is the highest-risk file in the suite: it proves the aioresponses operationName
routing callback (see conftest.py's mock_graphql fixture) actually works end-to-end
against the real, unmodified vendored PyXploraApi client.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_USER_ID, DEFAULT_WUID, make_device_list_payload


async def test_init_logs_in_and_sets_username_and_user_id(coordinator: XploraDataUpdateCoordinator) -> None:
    assert coordinator.username == "Parent Name"
    assert coordinator.user_id == DEFAULT_USER_ID


async def test_is_admin_derived_from_devicelist_guardian_type(coordinator_with_data: XploraDataUpdateCoordinator) -> None:
    # is_admin is keyed per watch (wuid) and derived from each deviceList item's guardianType
    # ("FIRST" in the default fixture) -- no separate Contacts request.
    for wuid in coordinator_with_data.controller.getWatchUserIDs():
        assert coordinator_with_data.is_admin[wuid] is True


async def test_is_admin_false_when_guardian_type_not_first(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_geocoding_openstreetmap, graphql_operations
) -> None:
    from homeassistant.helpers import aiohttp_client

    # A non-primary guardian: the deviceList item reports guardianType != "FIRST".
    graphql_operations["deviceList"] = {"data": make_device_list_payload(guardian_type="SECOND")}

    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
    session = aiohttp_client.async_get_clientsession(hass)
    await coord.init(session=session)
    await coord.async_update_xplora_data()  # is_admin is derived during the deviceList fetch

    assert coord.is_admin[DEFAULT_WUID] is False


async def test_set_controller_uses_phone_entry_data(
    hass, mock_config_entry_phone: MockConfigEntry, mock_graphql, mock_geocoding_openstreetmap
) -> None:
    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_phone)
    await coord.set_controller(None)

    assert coord.controller._phoneNumber == "+491700000001"
    assert coord.controller._countrycode == "+49"


async def test_set_controller_uses_email_entry_data(
    hass, mock_config_entry_email: MockConfigEntry, mock_graphql, mock_geocoding_openstreetmap
) -> None:
    coord = XploraDataUpdateCoordinator(hass, mock_config_entry_email)
    await coord.set_controller(None)

    assert coord.controller._email == "parent@example.com"


async def test_controller_watch_ids_include_default_watch(coordinator: XploraDataUpdateCoordinator) -> None:
    assert DEFAULT_WUID in coordinator.controller.getWatchUserIDs()
