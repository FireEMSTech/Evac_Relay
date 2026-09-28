"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, SIGNAL_UPDATE
from .manager import EvacManager


class EvacEntity(Entity):
    """Push-updated entity bound to one manager."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: EvacManager, key: str) -> None:
        self.manager = manager
        self._attr_translation_key = key
        self._attr_unique_id = f"{manager.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry_id)},
            name=f"Evac Relay {manager.name}",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(self.manager.entry_id), self.async_write_ha_state)
        )
