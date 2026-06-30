"""Entity for Xplora® Watch Version 2 tracking."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .config import ResolvedOptions, resolve, resolve_account_alias
from .const import ATTR_WATCH, ATTRIBUTION, CONF_REFRESH_ON_CARD_RENDER, DEVICE_NAME, DOMAIN, MANUFACTURER, TRACKER_UPDATE_STR
from .coordinator import XploraDataUpdateCoordinator
from .helper import account_token, watch_user_label
from .log import Log


class XploraBaseEntity(CoordinatorEntity[XploraDataUpdateCoordinator], RestoreEntity):
    """Common base for Xplora® entities."""

    # Use HA's entity-name composition: the device carries the watch name ("Kid One Watch") and
    # each entity only names its own role ("Battery"), so the UI shows "Kid One Watch Battery".
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_force_update = False
    _state = None

    def __init__(
        self,
        config_entry: ConfigEntry,
        description: EntityDescription | None,
        coordinator: XploraDataUpdateCoordinator,
        wuid: str,
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)
        if description is not None:
            self.entity_description = description
        self._config_entry = config_entry
        self._data = config_entry.data
        self._options = config_entry.options
        # Typed, resolved view of this entry's options (single source of option defaults).
        self._resolved_options = resolve(config_entry.options)
        # Per-entry child logger (see log.Log) for per-config-entry verbose control.
        self._log = Log(entry_id=config_entry.entry_id)

        self.watch_uid = wuid
        self._unsub_dispatchers: list[Callable[[], None]] = []

        self.watch_name = watch_user_label(coordinator.controller, self.watch_uid)
        # Per-account token (user-set alias -> account display name -> account id) appended to the
        # device name and entity slug so the same watch linked to several accounts stays
        # distinguishable. The alias resolves options -> data (an options-flow edit overrides the
        # value captured at setup); it is recomputed on every load, so the *device name* reflects an
        # alias edit immediately, while the *slug* is frozen at entity creation by HA's registry.
        self.account_token = account_token(
            resolve_account_alias(config_entry),
            coordinator.username,
            coordinator.user_id,
        )

        # Human-friendly device name (e.g. "Kid One Watch (Mom)"); the watch id is intentionally
        # omitted -- `identifiers` already makes the device unique. The token is shown verbatim.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._config_entry.unique_id}_{self.watch_uid}")},
            manufacturer=MANUFACTURER,
            model=coordinator.data[self.watch_uid].get("model", DEVICE_NAME),
            name=f"{self.watch_name} {ATTR_WATCH.title()} ({self.account_token})",
            sw_version=coordinator.os_version,
            configuration_url="https://github.com/distante/ha-xplora-watch/blob/main/README.md",
        )

    def branded_object_id(self, *parts: str) -> str:
        """Build a concise, integration-branded object id, e.g. `xplora_kid_one_watch_battery_mom`.

        Combine with the platform's ``ENTITY_ID_FORMAT`` and assign to ``self.entity_id``: a
        self-set entity_id is used verbatim by HA, whereas overriding ``suggested_object_id``
        would route through ``object_id_base`` and get the device name ("Kid One Watch") prefixed
        onto it. `parts` are the entity's role-specific tokens (sensor key, alarm time, …); the
        slugified account token is appended as the trailing segment so slugs stay collision-free
        across accounts that link the same watch.
        """
        return slugify(" ".join(["xplora", self.watch_name, ATTR_WATCH, *parts, self.account_token]))

    @property
    def resolved_options(self) -> ResolvedOptions:
        """Typed, resolved view of this entry's user options (single source of defaults)."""
        return self._resolved_options

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Attributes shared by every Xplora entity.

        `CONF_REFRESH_ON_CARD_RENDER` is surfaced so the custom Lovelace cards -- which may bind to
        *any* of the watch's entities -- can read the user's "refresh on render" preference without
        a separate websocket round-trip. Subclasses merge this via ``super().extra_state_attributes``.
        """
        return {CONF_REFRESH_ON_CARD_RENDER: self._resolved_options.refresh_on_card_render}

    def _states(self, status: str) -> bool:
        if status == "DISABLE":
            return False
        return True

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # Restore state if available
        if state := await self.async_get_last_state():
            self._state = state.state
        self._unsub_dispatchers.append(async_dispatcher_connect(self.hass, TRACKER_UPDATE_STR, self._async_receive_data))

    async def async_will_remove_from_hass(self) -> None:
        """Clean up after entity before removal."""
        await super().async_will_remove_from_hass()
        for unsub in self._unsub_dispatchers:
            unsub()
        self._log.debug("When entity is removed from hass")
        self._unsub_dispatchers = []

    @callback
    def _async_receive_data(self, device: str, location: tuple[float, float], location_name: str) -> None:
        """Update device data."""
        self._log.debug("Update device data.\n%s\n%s", device, self.watch_uid)
        if device != self.watch_uid:
            return
        self._location_name = location_name
        self._location = location
        self.async_write_ha_state()
