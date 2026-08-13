"""Sensor component."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_DIAG_ACTIVE_VIRTUALS,
    ATTR_DIAG_CONFIGURATION_VERSION,
    ATTR_DIAG_GITHUB_SHA,
    ATTR_DIAG_ONLINE_DEVICES,
    ATTR_DIAG_PHYSICAL_DEVICES,
    ATTR_DIAG_SCAN_INTERVAL,
    ATTR_DIAG_STREAMING_VIRTUALS,
    ATTR_DIAG_VIRTUALS,
    ATTR_STATE,
    SENSOR_ICONS,
    SIGNAL_NEW_SENSOR,
)
from .entity import LedFxEntity
from .updater import LedFxEntityDescription, LedFxUpdater, async_get_updater

PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)

DIAGNOSTIC_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=ATTR_DIAG_CONFIGURATION_VERSION,
        name="Configuration version",
        icon="mdi:file-cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_GITHUB_SHA,
        name="GitHub SHA",
        icon="mdi:source-commit",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_PHYSICAL_DEVICES,
        name="Physical devices",
        icon="mdi:devices",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_ONLINE_DEVICES,
        name="Online devices",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_VIRTUALS,
        name="Virtuals",
        icon="mdi:layers-triple-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_ACTIVE_VIRTUALS,
        name="Active virtuals",
        icon="mdi:play-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=True,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_STREAMING_VIRTUALS,
        name="Streaming virtuals",
        icon="mdi:cast-connected",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key=ATTR_DIAG_SCAN_INTERVAL,
        name="Scan interval",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer-sync-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LedFx sensor entry.

    :param hass: HomeAssistant: Home Assistant object
    :param config_entry: ConfigEntry: ConfigEntry object
    :param async_add_entities: AddEntitiesCallback: AddEntitiesCallback callback object
    """

    updater: LedFxUpdater = async_get_updater(hass, config_entry.entry_id)

    async_add_entities(
        [
            LedFxSensor(
                f"{config_entry.entry_id}-{description.key}",
                LedFxEntityDescription(
                    description=description,
                    device_info=updater.device_info,
                ),
                updater,
            )
            for description in DIAGNOSTIC_SENSORS
        ]
    )

    @callback
    def add_sensor(entity: LedFxEntityDescription) -> None:
        """Add sensor.

        :param entity: LedFxEntityDescription: Sensor object
        """

        async_add_entities(
            [
                LedFxSensor(
                    f"{config_entry.entry_id}-{entity.description.key}",
                    entity,
                    updater,
                )
            ]
        )

    for sensor in updater.sensors.values():
        add_sensor(sensor)

    updater.new_sensor_callback = async_dispatcher_connect(
        hass, SIGNAL_NEW_SENSOR, add_sensor
    )


class LedFxSensor(LedFxEntity, SensorEntity):
    """LedFx sensor entry."""

    def __init__(
        self,
        unique_id: str,
        entity: LedFxEntityDescription,
        updater: LedFxUpdater,
    ) -> None:
        """Initialize sensor.

        :param unique_id: str: Unique ID
        :param entity: LedFxEntityDescription object
        :param updater: LedFxUpdater: LedFx updater object
        """

        LedFxEntity.__init__(
            self, unique_id, entity.description, updater, ENTITY_ID_FORMAT
        )

        self._attr_native_value = self._updater.data.get(entity.description.key, None)
        self._attr_device_info = entity.device_info

        if entity.description.key in SENSOR_ICONS:
            self._attr_icon = SENSOR_ICONS[entity.description.key]

    def _handle_coordinator_update(self) -> None:
        """Update state."""

        is_available: bool = self._updater.data.get(ATTR_STATE, False)

        state: Any = self._updater.data.get(self.entity_description.key, None)

        if (
            self._attr_native_value == state
            and self._attr_available == is_available  # type: ignore
        ):
            return

        self._attr_available = is_available
        self._attr_native_value = state

        self.async_write_ha_state()
