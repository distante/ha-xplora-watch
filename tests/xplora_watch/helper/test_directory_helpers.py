"""Tests for helper.create_www_directory."""

from __future__ import annotations

import os

from homeassistant.core import HomeAssistant

from custom_components.xplora_watch.const import DOMAIN
from custom_components.xplora_watch.helper import create_www_directory


async def test_create_www_directory_creates_all_expected_paths(hass: HomeAssistant) -> None:
    """All 6 expected www subdirectories are created."""
    await create_www_directory(hass)

    expected_paths = [
        hass.config.path("www"),
        hass.config.path("www/image"),
        hass.config.path("www/video"),
        hass.config.path("www/video/thumb"),
        hass.config.path("www/voice"),
        hass.config.path(f"www/{DOMAIN}"),
    ]
    for path in expected_paths:
        assert os.path.exists(path), f"expected {path} to exist"


async def test_create_www_directory_is_idempotent(hass: HomeAssistant) -> None:
    """Calling create_www_directory twice does not raise."""
    await create_www_directory(hass)
    await create_www_directory(hass)

    assert os.path.exists(hass.config.path(f"www/{DOMAIN}"))
