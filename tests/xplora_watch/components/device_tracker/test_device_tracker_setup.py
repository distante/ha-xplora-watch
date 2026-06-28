"""Tests for device_tracker.py's async_setup_entry (entity fan-out)."""

from __future__ import annotations

import os
import re
from collections.abc import Generator

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.coordinator import XploraDataUpdateCoordinator
from custom_components.xplora_watch.device_tracker import (
    XploraDeviceTracker,
    XploraSafezoneTracker,
    async_setup_entry,
)
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_WUID
from tests.xplora_watch.fixtures.rest_payloads import ENTITY_PICTURE_BODY


@pytest.fixture(autouse=True)
def _clean_cached_watch_icon(hass: HomeAssistant) -> Generator[None]:
    """pytest-homeassistant-custom-component reuses a fixed (non-tmp) config dir across tests, so a
    cached icon written by one test would otherwise leak into the next; remove it before and after.
    """
    cached_file = hass.config.path(f"www/image/{DEFAULT_WUID}.jpeg")
    if os.path.exists(cached_file):
        os.remove(cached_file)
    yield
    if os.path.exists(cached_file):
        os.remove(cached_file)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, coordinator: XploraDataUpdateCoordinator) -> list:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    captured: list = []

    def capture_entities(new_entities, update_before_add: bool = False) -> None:
        captured.extend(new_entities)

    await async_setup_entry(hass, entry, capture_entities)
    return captured


async def test_default_setup_creates_safezone_and_main_tracker(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_entity_picture,
) -> None:
    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    safezone_entities = [e for e in entities if isinstance(e, XploraSafezoneTracker)]
    tracker_entities = [e for e in entities if isinstance(e, XploraDeviceTracker)]
    assert len(safezone_entities) == 1
    assert len(tracker_entities) == 1


async def test_main_tracker_caches_entity_picture_locally(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_entity_picture,
) -> None:
    """The remote entity_picture URL is downloaded once into www/image and served locally."""
    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    tracker_entities = [e for e in entities if isinstance(e, XploraDeviceTracker)]
    assert tracker_entities[0]._attr_entity_picture == f"/local/image/{DEFAULT_WUID}.jpeg"

    cached_file = hass.config.path(f"www/image/{DEFAULT_WUID}.jpeg")
    assert os.path.exists(cached_file)
    with open(cached_file, "rb") as f:
        assert f.read() == ENTITY_PICTURE_BODY


async def test_main_tracker_skips_download_when_already_cached(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_graphql,
) -> None:
    """If the icon is already cached, the remote URL is not hit again and the cached file is kept."""
    cached_file = hass.config.path(f"www/image/{DEFAULT_WUID}.jpeg")
    os.makedirs(os.path.dirname(cached_file), exist_ok=True)
    with open(cached_file, "wb") as f:
        f.write(b"placeholder content")

    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    tracker_entities = [e for e in entities if isinstance(e, XploraDeviceTracker)]
    assert tracker_entities[0]._attr_entity_picture == f"/local/image/{DEFAULT_WUID}.jpeg"
    with open(cached_file, "rb") as f:
        assert f.read() == b"placeholder content"


async def test_main_tracker_falls_back_to_mdi_icon_when_entity_picture_unreachable(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_graphql,
) -> None:
    """A non-200 response for the entity_picture URL leaves entity_picture unset, so the frontend
    falls back to _attr_icon (mdi:watch) instead of a remote placeholder image. The
    XploraDeviceTracker entity is still created either way.
    """
    mock_graphql.get(re.compile(r"https://api\.myxplora\.com/file.*"), status=404, repeat=True)

    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    tracker_entities = [e for e in entities if isinstance(e, XploraDeviceTracker)]
    assert len(tracker_entities) == 1
    assert tracker_entities[0]._attr_entity_picture is None
    assert tracker_entities[0]._attr_icon == "mdi:watch"
    assert not os.path.exists(hass.config.path(f"www/image/{DEFAULT_WUID}.jpeg"))


async def test_setup_skips_watch_not_in_conf_watches(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_entity_picture,
) -> None:
    hass.config_entries.async_update_entry(mock_config_entry_phone, options={**mock_config_entry_phone.options, "watches": []})

    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    assert entities == []


async def test_setup_no_options_skips_watch(
    hass: HomeAssistant,
    coordinator_with_data: XploraDataUpdateCoordinator,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Xplora®", data={}, options={})
    entry.add_to_hass(hass)

    entities = await _setup(hass, entry, coordinator_with_data)

    assert entities == []


async def test_safezone_tracker_unique_id_contains_watch_and_vendor_id(
    hass: HomeAssistant,
    mock_config_entry_phone: MockConfigEntry,
    coordinator_with_data: XploraDataUpdateCoordinator,
    mock_entity_picture,
) -> None:
    entities = await _setup(hass, mock_config_entry_phone, coordinator_with_data)

    safezone_entities = [e for e in entities if isinstance(e, XploraSafezoneTracker)]
    assert "vendor_safezone_1" in safezone_entities[0]._attr_unique_id
    assert DEFAULT_WUID.replace("-", "_") in safezone_entities[0]._attr_unique_id
