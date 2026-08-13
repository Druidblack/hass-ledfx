"""LedFx custom integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    PLATFORMS,
    UPDATE_LISTENER,
    UPDATER,
)
from .helper import build_auth, get_config_value
from .updater import LedFxUpdater

_LOGGER = logging.getLogger(__name__)


def _remove_legacy_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove obsolete entities from older integration versions."""

    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.platform != DOMAIN:
            continue

        if entity_entry.domain == "button":
            _LOGGER.debug("Removing legacy LedFx scene button %s", entity_entry.entity_id)
            registry.async_remove(entity_entry.entity_id)
            continue

        if entity_entry.domain == "media_player":
            _LOGGER.debug("Removing legacy LedFx media_player %s", entity_entry.entity_id)
            registry.async_remove(entity_entry.entity_id)
            continue

        if (
            entity_entry.domain == "sensor"
            and entity_entry.unique_id.endswith("-last_successful_update")
        ):
            _LOGGER.debug(
                "Removing obsolete LedFx Last successful update sensor %s",
                entity_entry.entity_id,
            )
            registry.async_remove(entity_entry.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LedFx from a config entry."""

    updater = LedFxUpdater(
        hass,
        get_config_value(entry, CONF_IP_ADDRESS),
        get_config_value(entry, CONF_PORT),
        build_auth(
            get_config_value(entry, CONF_USERNAME),
            get_config_value(entry, CONF_PASSWORD),
        ),
        get_config_value(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        get_config_value(entry, CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {UPDATER: updater}

    # The first refresh must happen while the config entry is in
    # SETUP_IN_PROGRESS.  Deferring it with call_later() causes reloads to fail
    # on recent Home Assistant versions because the entry is already LOADED.
    try:
        await updater.async_config_entry_first_refresh()
        _remove_legacy_entities(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Do not leave stale runtime data behind if setup fails.  Home Assistant
        # will retry ConfigEntryNotReady raised by async_config_entry_first_refresh.
        await updater.async_stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
        raise

    hass.data[DOMAIN][entry.entry_id][UPDATE_LISTENER] = entry.add_update_listener(
        async_update_options
    )

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""

    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        return

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a LedFx config entry."""

    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        return True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    entry_data = hass.data[DOMAIN].pop(entry.entry_id)

    updater: LedFxUpdater = entry_data[UPDATER]
    await updater.async_stop()

    update_listener: CALLBACK_TYPE | None = entry_data.get(UPDATE_LISTENER)
    if update_listener is not None:
        update_listener()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN, None)

    return True
