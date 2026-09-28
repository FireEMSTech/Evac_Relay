"""Binary sensors: alarm active and acknowledged."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EvacConfigEntry
from .entity import EvacEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    async_add_entities([ActiveSensor(manager), AcknowledgedSensor(manager)])


class ActiveSensor(EvacEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, manager) -> None:
        super().__init__(manager, "alarm_active")

    @property
    def is_on(self) -> bool:
        return self.manager.state.active


class AcknowledgedSensor(EvacEntity, BinarySensorEntity):
    def __init__(self, manager) -> None:
        super().__init__(manager, "acknowledged")

    @property
    def is_on(self) -> bool:
        return self.manager.state.active and self.manager.state.acknowledged
