"""Tests for helper.get_location_distance_meter and helper.is_distance_in_radius."""

from __future__ import annotations

import pytest
from geopy import distance
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.xplora_watch.helper import (
    get_location_distance_meter,
    is_distance_in_radius,
)

HOME_LAT_LNG = (52.5200, 13.4050)
NEARBY_LAT_LNG = (52.5210, 13.4060)


async def test_get_location_distance_meter_matches_geopy(hass: HomeAssistant, mock_home_zone: None) -> None:
    """The returned int matches int(geopy.distance.distance(home, point).m) exactly."""
    expected = int(distance.distance(HOME_LAT_LNG, NEARBY_LAT_LNG).m)

    result = get_location_distance_meter(hass, NEARBY_LAT_LNG)

    assert result == expected
    assert isinstance(result, int)
    assert result > 0


async def test_get_location_distance_meter_missing_home_zone_raises(hass: HomeAssistant) -> None:
    """Without zone.home set, a HomeAssistantError with the exact message is raised."""
    with pytest.raises(HomeAssistantError, match="Zone 'zone.home' not found"):
        get_location_distance_meter(hass, NEARBY_LAT_LNG)


def test_is_distance_in_radius_inside_radius_returns_true() -> None:
    """A point clearly inside the radius returns True."""
    # ~10cm away, well inside any reasonable radius.
    close_point = (52.52001, 13.40501)
    assert is_distance_in_radius(HOME_LAT_LNG, close_point, radius=1000) is True


def test_is_distance_in_radius_outside_radius_returns_false() -> None:
    """A point clearly outside the radius returns False."""
    far_point = (53.5511, 9.9937)  # Hamburg, ~250km from Berlin.
    assert is_distance_in_radius(HOME_LAT_LNG, far_point, radius=1000) is False


def test_is_distance_in_radius_exact_boundary_returns_true() -> None:
    """radius == int(distance) is True because the comparison is `radius >= int(distance.m)`."""
    point = (52.5210, 13.4060)
    exact_radius = int(distance.distance(HOME_LAT_LNG, point).m)

    assert is_distance_in_radius(HOME_LAT_LNG, point, radius=exact_radius) is True
