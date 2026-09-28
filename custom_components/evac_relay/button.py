"""Dashboard buttons for acknowledge, clear, and test."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import EvacConfigEntry
from .classifier import Level
from .entity import EvacEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EvacConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    m = entry.runtime_data
    async_add_entities(
        [
            EvacButton(m, "acknowledge", m.async_acknowledge),
            EvacButton(m, "clear", m.async_clear),
            EvacButton(m, "test", lambda: m.async_test(Level.WARNING), EntityCategory.DIAGNOSTIC),
        ]
    )


class EvacButton(EvacEntity, ButtonEntity):
    def __init__(self, manager, key, action, category: EntityCategory | None = None) -> None:
        super().__init__(manager, key)
        self._action = action
        self._attr_entity_category = category

    async def async_press(self) -> None:
        self._action()
