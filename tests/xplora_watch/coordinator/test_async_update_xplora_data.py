"""Tests for XploraDataUpdateCoordinator.async_update_xplora_data()."""

from __future__ import annotations

from homeassistant.const import CONF_COUNTRY_CODE, CONF_LANGUAGE, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import (
    CONF_PHONENUMBER,
    CONF_TIMEZONE,
    CONF_USERLANG,
    DOMAIN,
)
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID


async def test_new_data_short_circuit_no_prior_data(coordinator: XploraDataUpdateCoordinator) -> None:
    """new_data with no prior coordinator.data sets data directly and returns it."""
    assert not coordinator.data
    result = await coordinator.async_update_xplora_data(new_data={"some-wuid": {"foo": "bar"}})

    assert result == {"some-wuid": {"foo": "bar"}}
    assert coordinator.data == {"some-wuid": {"foo": "bar"}}


async def test_new_data_short_circuit_merges_into_prior_data(coordinator: XploraDataUpdateCoordinator) -> None:
    """new_data with existing coordinator.data merges (updates) rather than replacing."""
    coordinator.data = {"existing-wuid": {"baz": 1}}

    result = await coordinator.async_update_xplora_data(new_data={"some-wuid": {"foo": "bar"}})

    assert result == {"existing-wuid": {"baz": 1}, "some-wuid": {"foo": "bar"}}
    assert coordinator.data == {"existing-wuid": {"baz": 1}, "some-wuid": {"foo": "bar"}}


async def test_new_data_short_circuit_overwrites_same_wuid_key(coordinator: XploraDataUpdateCoordinator) -> None:
    """new_data for a wuid already present in coordinator.data overwrites that wuid entirely."""
    coordinator.data = {DEFAULT_WUID: {"old": "value"}}

    result = await coordinator.async_update_xplora_data(new_data={DEFAULT_WUID: {"new": "value"}})

    assert result == {DEFAULT_WUID: {"new": "value"}}


async def test_targets_explicit_list_is_used(coordinator: XploraDataUpdateCoordinator) -> None:
    """An explicit targets list is passed straight to controller.setDevices()."""
    result = await coordinator.async_update_xplora_data(targets=[DEFAULT_WUID])

    assert DEFAULT_WUID in result
    assert result is coordinator.data


async def test_targets_none_falls_back_to_conf_watches_option(coordinator: XploraDataUpdateCoordinator) -> None:
    """targets=None falls back to the CONF_WATCHES option (set to [DEFAULT_WUID] by the fixture)."""
    result = await coordinator.async_update_xplora_data()

    assert DEFAULT_WUID in result
    assert result is coordinator.data


async def test_targets_none_and_no_conf_watches_falls_back_to_setDevices(
    hass: HomeAssistant, mock_graphql, mock_geocoding_openstreetmap
) -> None:
    """When neither targets nor the CONF_WATCHES option is set, controller.setDevices() (no args) discovers watches."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Xplora®",
        unique_id="+491700000099",
        data={
            CONF_COUNTRY_CODE: "+49",
            CONF_PHONENUMBER: "+491700000099",
            CONF_PASSWORD: "secret",
            CONF_USERLANG: "en-GB",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_LANGUAGE: "en",
        },
        options={},
    )
    entry.add_to_hass(hass)

    from homeassistant.helpers import aiohttp_client

    coord = XploraDataUpdateCoordinator(hass, entry)
    await coord.init(session=aiohttp_client.async_get_clientsession(hass))

    result = await coord.async_update_xplora_data()

    assert DEFAULT_WUID in result
    assert result is coord.data


async def test_returns_full_accumulated_data_not_just_refreshed_wuid(
    coordinator: XploraDataUpdateCoordinator,
) -> None:
    """The return value is the whole accumulated coordinator.data dict, merged with any prior content."""
    coordinator.data = {"stale-wuid": {"stale": True}}

    result = await coordinator.async_update_xplora_data(targets=[DEFAULT_WUID])

    assert "stale-wuid" in result
    assert DEFAULT_WUID in result
    assert result is coordinator.data
