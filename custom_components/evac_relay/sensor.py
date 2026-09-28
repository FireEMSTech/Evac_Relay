"""Sensors: alert level, last message, NWS poll health."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EvacConfigEntry
from .classifier import Level
from .entity import EvacEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    manager = entry.runtime_data
    async_add_entities([LevelSensor(manager), LastMessageSensor(manager), NwsPollSensor(manager)])


class LevelSensor(EvacEntity, SensorEntity):
    """none / warning / shelter / order. The response blueprint triggers on this."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [lvl.key for lvl in Level]
    # Alert text and the scanner URL (which can carry credentials) stay out of history.
    _unrecorded_attributes = frozenset({"message", "scanner_url"})

    def __init__(self, manager) -> None:
        super().__init__(manager, "level")

    @property
    def native_value(self) -> str:
        return self.manager.state.level if self.manager.state.active else Level.NONE.key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.manager.state
        return {
            "active": s.active,
            "acknowledged": s.acknowledged,
            "test": s.test,
            "source": s.alarm_source,
            "activated_at": s.activated_at,
            "zones": self.manager.zones,
            "scanner_url": self.manager.scanner_url,
            "message": s.alarm_message,
        }


class LastMessageSensor(EvacEntity, SensorEntity):
    """Most recent message that contained evacuation language, for any zone."""

    _unrecorded_attributes = frozenset({"full_text"})

    def __init__(self, manager) -> None:
        super().__init__(manager, "last_message")

    @property
    def native_value(self) -> str | None:
        msg = self.manager.state.last_message
        return msg[:250] if msg else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.manager.state
        return {
            "full_text": s.last_message,
            "source": s.last_source,
            "level": s.last_level_seen,
            "received_at": s.last_received_at,
        }


class NwsPollSensor(EvacEntity, SensorEntity):
    """Last successful NWS poll; lets a watchdog automation detect a dead source."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager) -> None:
        super().__init__(manager, "nws_last_poll")

    @property
    def native_value(self) -> datetime | None:
        return self.manager.nws_last_poll
