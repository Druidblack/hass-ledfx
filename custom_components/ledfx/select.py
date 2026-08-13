"""Select component."""

from __future__ import annotations

import logging
from typing import Final

from homeassistant.components.select import (
    ENTITY_ID_FORMAT,
    SelectEntity,
    SelectEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_DEVICE,
    ATTR_FIELD_EFFECTS,
    ATTR_FIELD_OPTIONS,
    ATTR_FIELD_TYPE,
    ATTR_LIGHT_ACTIVE_PRESET,
    ATTR_LIGHT_BRIGHTNESS,
    ATTR_LIGHT_CUSTOM_PRESETS,
    ATTR_LIGHT_DEFAULT_PRESETS,
    ATTR_LIGHT_EFFECT,
    ATTR_LIGHT_EFFECT_CONFIG,
    ATTR_LIGHT_EFFECTS,
    ATTR_LIGHT_STATE,
    ATTR_PRESET_DEFAULT,
    ATTR_SELECT_AUDIO_INPUT,
    ATTR_SELECT_AUDIO_INPUT_NAME,
    ATTR_SELECT_AUDIO_INPUT_OPTIONS,
    ATTR_SELECT_DEVICE_EFFECT,
    ATTR_SELECT_DEVICE_PRESET,
    ATTR_STATE,
    SELECT_ICONS,
    SIGNAL_NEW_SELECT,
)
from .entity import LedFxEntity
from .enum import ActionType, Version
from .exceptions import LedFxError
from .helper import generate_entity_id
from .updater import LedFxEntityDescription, LedFxUpdater, async_get_updater

PARALLEL_UPDATES = 0

OPTIONS_MAP: Final = {
    ATTR_SELECT_AUDIO_INPUT: ATTR_SELECT_AUDIO_INPUT_OPTIONS,
}

SELECTS: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key=ATTR_SELECT_AUDIO_INPUT,
        name=ATTR_SELECT_AUDIO_INPUT_NAME,
        icon="mdi:audio-input-stereo-minijack",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=True,
    ),
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LedFx select entry.

    :param hass: HomeAssistant: Home Assistant object
    :param config_entry: ConfigEntry: ConfigEntry object
    :param async_add_entities: AddEntitiesCallback: AddEntitiesCallback callback object
    """

    updater: LedFxUpdater = async_get_updater(hass, config_entry.entry_id)

    @callback
    def add_select(entity: LedFxEntityDescription) -> None:
        """Add select.

        :param entity: LedFxEntityDescription: Sensor object
        """

        async_add_entities(
            [
                LedFxSelect(
                    f"{config_entry.entry_id}-{entity.device_code}-{entity.description.key}"
                    if entity.type
                    in (
                        ActionType.DEVICE,
                        ActionType.DEVICE_PRESET,
                        ActionType.DEVICE_EFFECT,
                    )
                    else f"{config_entry.entry_id}-{entity.description.key}",
                    entity,
                    updater,
                )
            ]
        )

    for select in SELECTS:
        add_select(
            LedFxEntityDescription(description=select, device_info=updater.device_info)
        )

    for select in updater.selects.values():
        add_select(select)

    updater.new_select_callback = async_dispatcher_connect(
        hass, SIGNAL_NEW_SELECT, add_select
    )


class LedFxSelect(LedFxEntity, SelectEntity):
    """LedFx select entry."""

    _options_key: str
    _type: ActionType

    def __init__(
        self,
        unique_id: str,
        entity: LedFxEntityDescription,
        updater: LedFxUpdater,
    ) -> None:
        """Initialize select.

        :param unique_id: str: Unique ID
        :param entity: LedFxEntityDescription object
        :param updater: LedFxUpdater: Luci updater object
        """

        LedFxEntity.__init__(
            self, unique_id, entity.description, updater, ENTITY_ID_FORMAT
        )

        self._type = entity.type
        self._attr_device_info = entity.device_info
        self._attr_available: bool = True

        if entity.type == ActionType.DEVICE:
            self._attr_device_code = entity.device_code

            self.entity_id = generate_entity_id(
                ENTITY_ID_FORMAT,
                updater.ip,
                f"{entity.device_code}_{entity.description.key}",
            )

            self._attr_current_option = updater.data.get(
                f"{entity.device_code}_{ATTR_LIGHT_EFFECT_CONFIG}", {}
            ).get(entity.description.key)

            self._attr_options = (
                entity.extra.get(ATTR_FIELD_OPTIONS, []) if entity.extra else []
            )

            if entity.extra:
                self._attr_field_type = entity.extra.get(ATTR_FIELD_TYPE)

            self._attr_extra_state_attributes = {
                ATTR_DEVICE: self._attr_device_code,
                ATTR_FIELD_EFFECTS: entity.extra.get(ATTR_FIELD_EFFECTS, [])
                if entity.extra
                else [],
            }

            self._attr_available = bool(
                updater.data.get(ATTR_STATE, False)
                and len(self._attr_options) > 0
                and updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_STATE}")
                and updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}")
                in self._attr_extra_state_attributes[ATTR_FIELD_EFFECTS]
            )

            if entity.description.key in SELECT_ICONS:
                self._attr_icon = SELECT_ICONS[entity.description.key]

            return

        if entity.type in (ActionType.DEVICE_PRESET, ActionType.DEVICE_EFFECT):
            self._attr_device_code = entity.device_code

            self.entity_id = generate_entity_id(
                ENTITY_ID_FORMAT,
                updater.ip,
                f"{entity.device_code}_{entity.description.key}",
            )

            self._attr_extra_state_attributes = {ATTR_DEVICE: self._attr_device_code}

            if entity.type == ActionType.DEVICE_EFFECT:
                self._attr_current_option = self._effect_current_label()
                self._attr_options = self._effect_select_options()
                self._attr_available = self._effect_select_available()
            else:
                self._attr_current_option = self._preset_current_option()
                self._attr_options = self._preset_options()
                self._attr_available = self._preset_available()

            return

        self._attr_current_option = updater.data.get(entity.description.key, None)

        self._options_key = (
            OPTIONS_MAP[entity.description.key]
            if entity.description.key in OPTIONS_MAP
            else f"{entity.description.key}_options"
        )

        options: dict | list = updater.data.get(self._options_key, [])
        self._attr_options = (
            list(options.values()) if isinstance(options, dict) else options
        )

        self._attr_available = bool(
            updater.data.get(ATTR_STATE, False) and len(self._attr_options) > 0
        )

    def _handle_coordinator_update(self) -> None:
        """Update state."""

        is_available: bool = self._attr_available
        current_option: str = self._attr_current_option
        options: dict | list = self._attr_options

        if self._type == ActionType.DEVICE_EFFECT:
            options = self._effect_select_options()
            is_available = self._effect_select_available()
            current_option = self._effect_current_label()
        elif self._type == ActionType.DEVICE_PRESET:
            options = self._preset_options()
            is_available = self._preset_available()
            current_option = self._preset_current_option()
            if current_option not in options:
                current_option = None
        elif self._type == ActionType.DEVICE:
            current_option = self._updater.data.get(
                f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT_CONFIG}", {}
            ).get(self.entity_description.key)

            is_available = bool(
                self._updater.data.get(ATTR_STATE, False)
                and len(options) > 0
                and self._updater.data.get(
                    f"{self._attr_device_code}_{ATTR_LIGHT_STATE}"
                )
                and self._updater.data.get(
                    f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"
                )
                in self._attr_extra_state_attributes[ATTR_FIELD_EFFECTS]
            )
        else:
            current_option = self._updater.data.get(self.entity_description.key, False)
            options = self._updater.data.get(self._options_key, [])
            options = list(options.values()) if isinstance(options, dict) else options

            is_available = bool(
                self._updater.data.get(ATTR_STATE, False) and len(options) > 0
            )

        if (
            self._attr_current_option == current_option
            and self._attr_options == options
            and self._attr_available == is_available
        ):
            return

        self._attr_available = is_available
        self._attr_current_option = current_option
        self._attr_options = options

        self.async_write_ha_state()

    def _effect_select_options(self) -> list:
        """Effects available on the instance, as "Category: Name" labels.

        :return list: Effect options
        """

        return self._updater.effect_options or list(
            self._updater.data.get(ATTR_LIGHT_EFFECTS, [])
        )

    def _effect_current_label(self) -> str | None:
        """Label of the device's current effect (id mapped to display label).

        :return str | None: Current option
        """

        effect: str | None = self._updater.data.get(
            f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"
        )

        return self._updater.effect_id_to_label.get(effect, effect)

    def _effect_select_available(self) -> bool:
        """Effect select availability.

        :return bool: Is available
        """

        return bool(
            self._updater.data.get(ATTR_STATE, False)
            and len(self._effect_select_options()) > 0
        )

    async def _effect_change(self, option: str) -> bool:
        """Set the device's base effect (loads that effect's default config).

        Accepts either a "Category: Name" label (from the effect select) or a
        raw effect id (from the preset "Default" reset).

        :param option: str: Effect option
        :return bool: Result
        """

        effect: str = self._updater.effect_label_to_id.get(option, option)

        try:
            response: dict = dict(
                await self._updater.client.device_on(
                    self._attr_device_code,  # type: ignore
                    effect,
                    self._updater.version == Version.V2,
                )
            )
        except LedFxError as _e:
            _LOGGER.debug("Effect update error: %r", _e)

            return False

        effect_config: dict = {
            key: value
            for key, value in response.get("effect", {}).get("config", {}).items()
            if not isinstance(value, (dict, list))
        }

        self._updater.data[f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"] = effect
        self._updater.data[f"{self._attr_device_code}_{ATTR_LIGHT_STATE}"] = True
        self._updater.data[f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT_CONFIG}"] = {
            code: value
            for code, value in effect_config.items()
            if code != ATTR_LIGHT_BRIGHTNESS
        }
        self._updater.data[
            f"{self._attr_device_code}_{ATTR_LIGHT_ACTIVE_PRESET}"
        ] = None

        # Refresh siblings (light, preset select) so the new effect/presets show
        # immediately instead of on the next poll.
        self._updater.async_update_listeners()

        return True

    def _preset_current_option(self) -> str | None:
        """Preset currently active on the LedFx virtual."""

        return self._updater.data.get(
            f"{self._attr_device_code}_{ATTR_LIGHT_ACTIVE_PRESET}"
        )

    def _preset_options(self) -> list:
        """Presets available for the device's current effect.

        The synthetic "Default" option (re-applies the effect's default config)
        is always offered first when an effect is set, so the selector is usable
        even for effects that ship no presets.

        :return list: Preset options
        """

        effect: str | None = self._updater.data.get(
            f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"
        )

        if not effect:
            return []

        default_presets: dict = self._updater.data.get(ATTR_LIGHT_DEFAULT_PRESETS, {})
        custom_presets: dict = self._updater.data.get(ATTR_LIGHT_CUSTOM_PRESETS, {})

        return [ATTR_PRESET_DEFAULT] + sorted(
            set(default_presets.get(effect, []) + custom_presets.get(effect, []))
        )

    def _preset_available(self) -> bool:
        """Preset select availability.

        :return bool: Is available
        """

        return bool(
            self._updater.data.get(ATTR_STATE, False)
            and self._updater.data.get(f"{self._attr_device_code}_{ATTR_LIGHT_STATE}")
            and len(self._preset_options()) > 0
        )

    @staticmethod
    def _preset_label(preset_id: str, preset_data: object) -> str:
        """Return the user-visible name for a LedFx preset."""

        if isinstance(preset_data, dict):
            name = preset_data.get("name")
            if name is not None and str(name).strip():
                return str(name).strip()

        return str(preset_id)

    @classmethod
    def _find_preset_in_response(
        cls, response: dict, option: str
    ) -> tuple[str | None, str | None, str | None]:
        """Resolve a HA option using LedFx's authoritative virtual preset list.

        Returns ``(effect_id, category, preset_id)``. User presets are searched
        before built-ins, matching the existing integration behaviour.  Both
        the visible name and the internal ID are accepted, and duplicate labels
        disambiguated as ``Name [id]`` are supported.
        """

        effect = response.get("effect")
        if not effect:
            return None, None, None

        for category in ("user_presets", "ledfx_presets"):
            presets = response.get(category, {})
            if not isinstance(presets, dict):
                continue

            for raw_id, preset_data in presets.items():
                preset_id = str(raw_id)
                label = cls._preset_label(preset_id, preset_data)
                if option in (label, preset_id, f"{label} [{preset_id}]"):
                    return str(effect), category, preset_id

        return str(effect), None, None

    async def _preset_change(self, option: str) -> bool:
        """Apply a preset (or reset to default) for the device's current effect.

        For LedFx 2.x, resolve the option from the per-virtual preset endpoint
        immediately before applying it. This endpoint is authoritative and
        preserves the relationship between a Unicode display name and LedFx's
        internal ``preset_id``.

        :param option: str: Preset option
        :return bool: Result
        """

        effect: str | None = self._updater.data.get(
            f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"
        )

        if not effect:
            return False

        if option == ATTR_PRESET_DEFAULT:
            changed = await self._effect_change(effect)
            if changed:
                self._updater.data[
                    f"{self._attr_device_code}_{ATTR_LIGHT_ACTIVE_PRESET}"
                ] = ATTR_PRESET_DEFAULT
                self._updater.async_update_listeners()
            return changed

        category: str | None = None
        preset_id: str | None = None

        try:
            if self._updater.version == Version.V2:
                preset_response = await self._updater.client.virtual_presets(
                    self._attr_device_code  # type: ignore[arg-type]
                )
                api_effect, category, preset_id = self._find_preset_in_response(
                    preset_response, option
                )
                if api_effect:
                    effect = api_effect

            # Compatibility fallback for older LedFx or unusual API responses.
            if category is None or preset_id is None:
                category, preset_id = self._updater.resolve_preset(effect, option)

            if category is None or preset_id is None:
                raise HomeAssistantError(
                    f"LedFx preset {option!r} could not be resolved for effect {effect!r}"
                )

            response = await self._updater.client.preset(
                self._attr_device_code,  # type: ignore[arg-type]
                category,
                effect,
                preset_id,
                self._updater.version == Version.V2,
            )

        except LedFxError as err:
            # Do not silently restore the previous HA selection. Surface the
            # actual LedFx response in the service error so failures are useful.
            _LOGGER.warning(
                "Unable to apply LedFx preset %r to %s: %s",
                option,
                self._attr_device_code,
                err,
            )
            raise HomeAssistantError(str(err)) from err

        # PUT /presets returns the new active effect. Reflect it immediately in
        # coordinator data so light/effect parameter entities update together.
        effect_data = response.get("effect", {}) if isinstance(response, dict) else {}
        if isinstance(effect_data, dict):
            active_effect = effect_data.get("type", effect)
            config = effect_data.get("config", {})
            self._updater.data[
                f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT}"
            ] = active_effect
            self._updater.data[
                f"{self._attr_device_code}_{ATTR_LIGHT_STATE}"
            ] = True
            if isinstance(config, dict):
                self._updater.data[
                    f"{self._attr_device_code}_{ATTR_LIGHT_EFFECT_CONFIG}"
                ] = {
                    key: value
                    for key, value in config.items()
                    if key != ATTR_LIGHT_BRIGHTNESS
                }

        self._updater.data[
            f"{self._attr_device_code}_{ATTR_LIGHT_ACTIVE_PRESET}"
        ] = option
        self._updater.async_update_listeners()
        return True

    async def _audio_input_change(self, option: str) -> bool:
        """Audio input

        :param option: str: Option value
        :return bool: Result
        """

        options: dict = self._updater.data.get(self._options_key, {})
        if option_ids := [_id for _id, name in options.items() if name == option]:
            try:
                await self._updater.client.set_audio_device(
                    int(option_ids[0]), self._updater.version == Version.V2
                )

                return True
            except LedFxError as _e:
                _LOGGER.debug("Audio input update error: %r", _e)

        return False

    async def _device_change(self, option: str) -> bool:
        """Device input

        :param option: str: Option value
        :return bool: Result
        """

        await self.async_update_effect(self.entity_description.key, option)

        return True

    async def async_select_option(self, option: str) -> None:
        """Select option

        :param option: str: Option
        """

        code: str = (
            ActionType.DEVICE
            if self._type == ActionType.DEVICE
            else self.entity_description.key
        )

        if action := getattr(self, f"_{code}_change"):
            if await action(option):
                if self._type == ActionType.DEFAULT:
                    self._updater.data[self.entity_description.key] = option

                self._attr_current_option = option

            self.async_write_ha_state()
