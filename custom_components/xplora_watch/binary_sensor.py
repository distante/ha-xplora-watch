"""Reads watch status from Xplora® Watch Version 2."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    ENTITY_ID_FORMAT,
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ID,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
    STATE_ON,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_SERVICE_USER,
    ATTR_TRACKER_LAT,
    ATTR_TRACKER_LNG,
    ATTR_WATCH,
    BINARY_SENSOR_CHARGING,
    BINARY_SENSOR_SAFEZONE,
    BINARY_SENSOR_STATE,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_WATCHES,
    DOMAIN,
    GUARDIAN_ONLY_KEYS,
    HOME,
)
from .coordinator import XploraDataUpdateCoordinator
from .entity import XploraBaseEntity
from .helper import is_distance_in_radius

_LOGGER = logging.getLogger(__name__)

# Charging and online state are enabled by default (core watch status); safezone is registered
# but disabled-by-default (it depends on home/safezone configuration to be meaningful) so it can
# be enabled per entity in the UI instead of via an options-flow type selection.
BINARY_SENSOR_TYPES: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key=BINARY_SENSOR_CHARGING,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key=BINARY_SENSOR_SAFEZONE,
        device_class=BinarySensorDeviceClass.SAFETY,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key=BINARY_SENSOR_STATE,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Xplora® Watch Version 2 binary sensors from config entry."""
    coordinator: XploraDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[XploraBinarySensor] = []
    for description in BINARY_SENSOR_TYPES:
        for watch in coordinator.controller.watchs:
            options = config_entry.options
            if not options or not isinstance(watch, dict):
                _LOGGER.debug("%s %s - no config options", watch, config_entry.entry_id)
                continue

            ward = watch.get("ward", None)
            if not isinstance(ward, dict):
                continue

            wuid = ward.get(ATTR_ID)
            if wuid is None:
                continue

            conf_watches = options.get(CONF_WATCHES, None)

            # Only `CONF_WATCHES` gates creation now; visibility is per-entity via
            # `entity_registry_enabled_default`, not an options-flow type selection.
            if conf_watches is None or wuid not in conf_watches:
                continue

            # The charging and safe-zone sensors depend on battery/location data a *Contact* never
            # receives, so skip them for a watch the account is only a Contact of (ref:XW-009).
            # Fails open: an unknown/unresolved role is treated as a Guardian. Online state is kept.
            if coordinator.is_confirmed_contact(wuid) and description.key in GUARDIAN_ONLY_KEYS:
                continue

            entities.append(XploraBinarySensor(config_entry, coordinator, ward, wuid, description))

    async_add_entities(entities)


class XploraBinarySensor(XploraBaseEntity, BinarySensorEntity):
    """Create Binary Sensor."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        coordinator: XploraDataUpdateCoordinator,
        ward: dict[str, Any],
        wuid: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize Binary Sensor."""
        super().__init__(config_entry, description, coordinator, wuid)
        if self.watch_uid not in self.coordinator.data:
            return

        # has_entity_name: name only the role; the device supplies the "Kid One Watch" prefix.
        self._attr_name: str = description.key.replace("_", " ").title()
        self.entity_id = ENTITY_ID_FORMAT.format(self.branded_object_id(description.key))

        # unique_id is kept unchanged to preserve existing entities' history/customizations.
        self._attr_unique_id = (
            f"{ward.get(CONF_NAME)}_{ATTR_WATCH}_{description.key}_{wuid}_{coordinator.user_id}".replace(" ", "_").replace("-", "_").lower()
        )
        _LOGGER.debug("Updating binary_sensor: %s | Typ: %s | Watch_ID ...%s", self._attr_name, description.key, wuid[25:])

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.entity_description.key == BINARY_SENSOR_CHARGING:
            is_charging: bool | None = self.coordinator.data[self.watch_uid].get("isCharging", None)
            return is_charging
        if self.entity_description.key == BINARY_SENSOR_STATE:
            is_online: bool | None = self.coordinator.data[self.watch_uid].get("isOnline", None)
            return is_online
        if self.entity_description.key == BINARY_SENSOR_SAFEZONE:
            if self.resolved_options.home_is_safezone == STATE_ON:
                latitude = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LAT, None)
                longitude = self.coordinator.data[self.watch_uid].get(ATTR_TRACKER_LNG, None)
                home_state = self.hass.states.get(HOME)
                if home_state and home_state.attributes:
                    home_latitude = home_state.attributes[CONF_LATITUDE]
                    home_longitude = home_state.attributes[CONF_LONGITUDE]
                    home_raduis = home_state.attributes[CONF_RADIUS]
                    if is_distance_in_radius(
                        (
                            self._options.get(CONF_HOME_LATITUDE, home_latitude),
                            self._options.get(CONF_HOME_LONGITUDE, home_longitude),
                        ),
                        (latitude, longitude),
                        self._options.get(CONF_HOME_RADIUS, home_raduis),
                    ):
                        return False
                else:
                    return False
            is_safezone: bool | None = self.coordinator.data[self.watch_uid].get("isSafezone", None)
            return is_safezone
        return False

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, if any."""
        if self.entity_description.key == BINARY_SENSOR_CHARGING and not self.coordinator.data[self.watch_uid].get("isCharging", None):
            return "mdi:battery-unknown"
        if hasattr(self, "_attr_icon"):
            return self._attr_icon
        if hasattr(self, "entity_description"):
            return self.entity_description.icon
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes that should be added to BINARY_SENSOR_STATE."""
        data = super().extra_state_attributes or {}
        return dict(data, **{ATTR_SERVICE_USER: self.coordinator.controller.getUserName()})
