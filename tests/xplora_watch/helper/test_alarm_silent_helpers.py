"""Tests for the alarm/silent conversion helpers and the frontend-card registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DATA_FRONTEND_REGISTERED, DOMAIN, FRONTEND_SCRIPT_URL
from custom_components.xplora_watch.helper import (
    _register_lovelace_resource,
    async_register_frontend_card,
    time_str_to_minutes,
    week_repeat_to_localized_days,
    week_repeat_to_weekdays,
    weekdays_to_week_repeat,
)


@pytest.mark.parametrize(
    ("value", "minutes"),
    [("00:00", 0), ("08:00", 480), ("22:30", 1350), ("23:59", 1439), ("07:05:00", 425)],
)
def test_time_str_to_minutes(value: str, minutes: int) -> None:
    assert time_str_to_minutes(value) == minutes


@pytest.mark.parametrize("bad", ["", "8", "25:00", "12:60", "noon"])
def test_time_str_to_minutes_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        time_str_to_minutes(bad)


def test_weekdays_to_week_repeat_roundtrip() -> None:
    # index 0 = Sunday .. 6 = Saturday
    assert weekdays_to_week_repeat(["mon", "tue", "wed", "thu", "fri"]) == "0111110"
    assert weekdays_to_week_repeat(["sun", "sat"]) == "1000001"
    assert weekdays_to_week_repeat([]) == "0000000"
    assert week_repeat_to_weekdays("0111110") == ["mon", "tue", "wed", "thu", "fri"]
    assert week_repeat_to_weekdays("1000001") == ["sun", "sat"]


def test_weekdays_to_week_repeat_is_case_insensitive() -> None:
    assert weekdays_to_week_repeat(["MON", " Fri "]) == "0100010"


def test_week_repeat_to_localized_days() -> None:
    # DAYS["en"] = [Sun, Mon, Tue, Wed, Thu, Fri, Sat]
    assert week_repeat_to_localized_days("0111110", "en") == "Mon, Tue, Wed, Thu, Fri"
    assert week_repeat_to_localized_days("1000001", "de") == "So, Sa"
    # Unknown language falls back to the default (en).
    assert week_repeat_to_localized_days("1000000", "xx") == "Sun"


async def test_async_register_frontend_card_registers_once(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})
    fake_http = MagicMock()
    fake_http.async_register_static_paths = AsyncMock()

    with (
        patch.object(hass, "http", fake_http, create=True),
        patch("homeassistant.components.frontend.add_extra_js_url") as mock_add_js,
    ):
        await async_register_frontend_card(hass)
        await async_register_frontend_card(hass)  # idempotent

    fake_http.async_register_static_paths.assert_awaited_once()
    # The module URL carries a `?v=<version>` cache-bust; the static path itself stays plain.
    mock_add_js.assert_called_once()
    called_hass, called_url = mock_add_js.call_args.args
    assert called_hass is hass
    assert called_url.startswith(f"{FRONTEND_SCRIPT_URL}?v=")
    assert hass.data[DOMAIN][DATA_FRONTEND_REGISTERED] is True


async def test_async_register_frontend_card_skips_without_http(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {})
    with patch.object(hass, "http", None, create=True):
        await async_register_frontend_card(hass)  # must not raise
    assert DATA_FRONTEND_REGISTERED not in hass.data[DOMAIN]


async def test_register_lovelace_resource_creates_item_when_absent(hass: HomeAssistant) -> None:
    """A storage-mode resource collection gets a new module resource for the card bundle."""
    url = f"{FRONTEND_SCRIPT_URL}?v=1.2.3"
    resources = MagicMock()
    resources.loaded = True
    resources.async_items = MagicMock(return_value=[])
    resources.async_create_item = AsyncMock()
    resources.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=resources)

    await _register_lovelace_resource(hass, url)

    resources.async_create_item.assert_awaited_once_with({"res_type": "module", "url": url})
    resources.async_update_item.assert_not_awaited()


async def test_register_lovelace_resource_updates_stale_version(hass: HomeAssistant) -> None:
    """An existing resource for the same base URL is version-updated rather than duplicated."""
    url = f"{FRONTEND_SCRIPT_URL}?v=2.0.0"
    resources = MagicMock()
    resources.loaded = True
    resources.async_items = MagicMock(return_value=[{"id": "abc", "url": f"{FRONTEND_SCRIPT_URL}?v=1.0.0"}])
    resources.async_create_item = AsyncMock()
    resources.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=resources)

    await _register_lovelace_resource(hass, url)

    resources.async_update_item.assert_awaited_once_with("abc", {"url": url})
    resources.async_create_item.assert_not_awaited()


async def test_register_lovelace_resource_noop_when_already_current(hass: HomeAssistant) -> None:
    """No write when the resource is already present with the current version."""
    url = f"{FRONTEND_SCRIPT_URL}?v=3.0.0"
    resources = MagicMock()
    resources.loaded = True
    resources.async_items = MagicMock(return_value=[{"id": "abc", "url": url}])
    resources.async_create_item = AsyncMock()
    resources.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=resources)

    await _register_lovelace_resource(hass, url)

    resources.async_create_item.assert_not_awaited()
    resources.async_update_item.assert_not_awaited()


async def test_register_lovelace_resource_noop_in_yaml_mode(hass: HomeAssistant) -> None:
    """YAML-mode collections are read-only (no async_create_item) -> no-op, no raise."""
    resources = MagicMock(spec=[])  # exposes no methods, mirroring a read-only YAML collection
    hass.data["lovelace"] = MagicMock(resources=resources)

    await _register_lovelace_resource(hass, f"{FRONTEND_SCRIPT_URL}?v=1")  # must not raise
