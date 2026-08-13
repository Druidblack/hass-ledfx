"""Text component."""

from __future__ import annotations

import logging

from homeassistant.components.text import ENTITY_ID_FORMAT, TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_DEVICE,
    ATTR_FIELD_EFFECTS,
    ATTR_FIELD_TYPE,
    ATTR_LIGHT_EFFECT,
    ATTR_LIGHT_EFFECT_CONFIG,
    ATTR_LIGHT_STATE,
    ATTR_STATE,
    SIGNAL_NEW_TEXT,
)
from .entity import LedFxEntity
from .enum import ActionType
from .helper import generate_entity_id
from .updater import LedFxEntityDescription, LedFxUpdater, async_get_updater

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LedFx text entry.

    :param hass: HomeAssistant: Home Assistant object
    :param config_entry: ConfigEntry: ConfigEntry object
    :param async_add_entities: AddEntitiesCallback: AddEntitiesCallback callback object
    """

    updater: LedFxUpdater = async_get_updater(hass, config_entry.entry_id)

    @callback
    def add_text(entity: LedFxEntityDescription) -> None:
        """Add text.

        :param entity: LedFxEntityDescription: Text object
        """

        async_add_entities(
            [
                LedFxText(
                    f"{config_entry.entry_id}-{entity.device_code}-{entity.description.key}",
                    entity,
                    updater,
                )
            ]
        )

    for text in updater.texts.values():
        add_text(text)

    updater.new_text_callback = async_dispatcher_connect(
        hass, SIGNAL_NEW_TEXT, add_text
    )


class LedFxText(LedFxEntity, TextEntity):
    """LedFx text entry."""

    _type: ActionType

    def __init__(
        self,
        unique_id: str,
        entity: LedFxEntityDescription,
        updater: LedFxUpdater,
    ) -> None:
        """Initialize text.

        :param unique_id: str: Unique ID
        :param entity: LedFxEntityDescription object
        :param updater: LedFxUpdater: updater object
        """

        LedFxEntity.__init__(
            self, unique_id, entity.description, updater, ENTITY_ID_FORMAT
        )

        self._type = entity.type
        self._attr_device_info = entity.device_info
        self._attr_available: bool = True

        self._attr_device_code = entity.device_code

        self.entity_id = generate_entity_id(
            ENTITY_ID_FORMAT,
            updater.ip,
            f"{entity.device_code}_{entity.description.key}",
        )

        self._attr_native_value = updater.data.get(
            f"{entity.device_code}_{ATTR_LIGHT_EFFECT_CONFIG}", {}
        ).get(entity.description.key)

        self._attr_extra_state_attributes = {
            ATTR_DEVICE: self._attr_device_code,
            ATTR_FIELD_EFFECTS: entity.extra.get(ATTR_FIELD_EFFECTS, [])
            if entity.extra
            else [],
        }

        if entity.extra:
            self._attr_field_type = entity.extra.get(ATTR_FIELD_TYPE)

        self._attr_available = bool(
            updater.data.get(ATTR_STATE, False)
            and updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_STATE}")
            and updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}")
            in self._attr_extra_state_attributes[ATTR_FIELD_EFFECTS]
        )

    def _handle_coordinator_update(self) -> None:
        """Update state."""

        value: str = self._updater.data.get(
            f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT_CONFIG}", {}
        ).get(self.entity_description.key)

        is_available: bool = bool(
            self._updater.data.get(ATTR_STATE, False)
            and self._updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_STATE}")
            and self._updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}")
            in self._attr_extra_state_attributes[ATTR_FIELD_EFFECTS]
        )

        if self._attr_native_value == value and self._attr_available == is_available:
            return

        self._attr_available = is_available
        self._attr_native_value = value

        self.async_write_ha_state()

    async def _device_set_value(self, value: str) -> None:
        """Device input

        :param value: str: Value
        """

        await self.async_update_effect(self.entity_description.key, value)

    async def async_set_value(self, value: str) -> None:
        """Set value

        :param value: str: Value
        """

        if action := getattr(self, f"_{ActionType.DEVICE}_set_value"):
            await action(value)

            self._attr_native_value = value

            self.async_write_ha_state()
