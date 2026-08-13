"""LedFx data updater."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from typing import Any, Final

from homeassistant.components.light import LightEntityDescription
from homeassistant.components.number import NumberEntityDescription
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntityDescription
from homeassistant.components.text import TextEntityDescription
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import event
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import (
    EntityCategory,
    EntityDescription,
)
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import utcnow
from httpx import USE_CLIENT_DEFAULT, codes

from .client import LedFxClient
from .const import (
    ATTR_DEVICE_SW_VERSION,
    ATTR_DIAG_ACTIVE_VIRTUALS,
    ATTR_DIAG_CONFIGURATION_VERSION,
    ATTR_DIAG_DEVELOPER_MODE,
    ATTR_DIAG_GITHUB_SHA,
    ATTR_DIAG_ONLINE_DEVICES,
    ATTR_DIAG_PHYSICAL_DEVICES,
    ATTR_DIAG_RELEASE_BUILD,
    ATTR_DIAG_SCAN_INTERVAL,
    ATTR_DIAG_SENDSPIN_AVAILABLE,
    ATTR_DIAG_STREAMING_VIRTUALS,
    ATTR_DIAG_VIRTUALS,
    ATTR_FIELD,
    ATTR_FIELD_EFFECTS,
    ATTR_FIELD_OPTIONS,
    ATTR_FIELD_TYPE,
    ATTR_LIGHT_ACTIVE_PRESET,
    ATTR_LIGHT_BRIGHTNESS,
    ATTR_LIGHT_COLOR,
    ATTR_LIGHT_CONFIG,
    ATTR_LIGHT_CUSTOM_PRESETS,
    ATTR_LIGHT_DEFAULT_PRESETS,
    ATTR_LIGHT_EFFECT,
    ATTR_LIGHT_EFFECT_CONFIG,
    ATTR_LIGHT_EFFECTS,
    ATTR_LIGHT_STATE,
    ATTR_SELECT_AUDIO_INPUT,
    ATTR_SELECT_AUDIO_INPUT_OPTIONS,
    ATTR_SELECT_DEVICE_EFFECT,
    ATTR_SELECT_DEVICE_EFFECT_NAME,
    ATTR_SELECT_DEVICE_PRESET,
    ATTR_SELECT_DEVICE_PRESET_NAME,
    ATTR_STATE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    NAME,
    SERVICE_MANUFACTURER,
    SERVICE_MODEL,
    SIGNAL_NEW_DEVICE,
    SIGNAL_NEW_NUMBER,
    SIGNAL_NEW_SELECT,
    SIGNAL_NEW_SENSOR,
    SIGNAL_NEW_SWITCH,
    SIGNAL_NEW_TEXT,
    UPDATER,
)
from .enum import ActionType, Version
from .exceptions import LedFxConnectionError, LedFxRequestError

PREPARE_METHODS_V1: Final = (
    "config",
    "info",
    "colors",
    "schema",
    "devices",
    "audio_devices",
)

_LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-branches,too-many-lines,too-many-arguments
class LedFxUpdater(DataUpdateCoordinator):
    """LedFx data updater for interaction with LedFX API."""

    version: Version = Version.V1

    client: LedFxClient
    code: codes = codes.BAD_GATEWAY
    ip: str
    port: str

    new_device_callback: CALLBACK_TYPE | None = None
    new_number_callback: CALLBACK_TYPE | None = None
    new_select_callback: CALLBACK_TYPE | None = None
    new_text_callback: CALLBACK_TYPE | None = None
    new_sensor_callback: CALLBACK_TYPE | None = None
    new_switch_callback: CALLBACK_TYPE | None = None

    _scan_interval: int
    _is_only_check: bool = False

    def __init__(
        self,
        hass: HomeAssistant,
        ip: str,
        port: str,
        auth: Any = USE_CLIENT_DEFAULT,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        timeout: int = DEFAULT_TIMEOUT,
        is_only_check: bool = False,
    ) -> None:
        """Initialize updater.

        :rtype: object
        :param hass: HomeAssistant: Home Assistant object
        :param ip: str: ip address
        :param port: str: port
        :param auth: Any: Basic auth
        :param scan_interval: int: Update interval
        :param timeout: int: Query execution timeout
        :param is_only_check: bool: Only config flow
        """

        self.client = LedFxClient(
            get_async_client(hass, False),
            ip,
            port,
            auth,
            timeout,
        )

        self.ip = ip  # pylint: disable=invalid-name
        self.port = port

        self._scan_interval = scan_interval
        self._is_only_check = is_only_check

        if hass is not None:
            super().__init__(
                hass,
                _LOGGER,
                name=f"{NAME} updater",
                update_interval=self._update_interval,
                update_method=self.update,
            )

        self.data: dict[str, Any] = {ATTR_DIAG_SCAN_INTERVAL: self._scan_interval}

        self.devices: dict[str, LedFxEntityDescription] = {}
        self.numbers: dict[str, LedFxEntityDescription] = {}
        self.selects: dict[str, LedFxEntityDescription] = {}
        self.texts: dict[str, LedFxEntityDescription] = {}
        self.sensors: dict[str, LedFxEntityDescription] = {}
        self.switches: dict[str, LedFxEntityDescription] = {}

        self.effect_properties: dict = {}
        self.colors: dict = {}
        self.gradients: dict = {}

        # Effect select presentation: options are "Category: Name" labels ordered
        # by category then name; the maps translate to/from the raw effect id
        # that LedFx expects on the wire.
        self.effect_options: list[str] = []
        self.effect_label_to_id: dict[str, str] = {}
        self.effect_id_to_label: dict[str, str] = {}

        # LedFx stores a preset under an internal preset ID and keeps the
        # user-visible label separately in the preset's ``name`` field.  This
        # distinction is essential for non-Latin names: LedFx 2.1.9's
        # generate_id() can turn a Cyrillic-only name into the ID ``default``.
        # Home Assistant must display the Unicode name while sending the real
        # preset ID back to the API.
        self.default_preset_ids: dict[str, dict[str, str]] = {}
        self.custom_preset_ids: dict[str, dict[str, str]] = {}

        self._is_first_update: bool = True

    async def async_stop(self) -> None:
        """Stop updater"""

        callbacks: list = [
            self.new_device_callback,
            self.new_number_callback,
            self.new_select_callback,
            self.new_sensor_callback,
            self.new_switch_callback,
            self.new_text_callback,
        ]

        for _callback in callbacks:
            if _callback is not None:
                _callback()  # pylint: disable=not-callable

    @cached_property
    def _update_interval(self) -> timedelta:
        """Update interval

        :return timedelta: update_interval
        """

        return timedelta(seconds=self._scan_interval)

    async def update(self) -> dict:
        """Update LedFx information.

        The first failed update is raised as ``UpdateFailed`` so
        ``async_config_entry_first_refresh`` can put the config entry into
        Home Assistant's retry flow.  Later failures keep the integration
        loaded and publish ``state = False`` as the original integration did.

        :return dict: dict with LedFx data.
        """

        self.code = codes.OK

        try:
            for method in PREPARE_METHODS_V1:
                if not self._is_only_check or method == "config":
                    await self._async_prepare(method, self.data)
        except LedFxConnectionError as err:
            self.code = codes.NOT_FOUND
            self.data[ATTR_STATE] = False

            if self._is_first_update and not self._is_only_check:
                raise UpdateFailed(
                    f"Unable to connect to LedFx at {self.address}"
                ) from err

            return self.data
        except LedFxRequestError as err:
            self.code = codes.FORBIDDEN
            self.data[ATTR_STATE] = False

            if self._is_first_update and not self._is_only_check:
                raise UpdateFailed(f"LedFx API request failed: {err}") from err

            return self.data

        self._is_first_update = False
        self.data[ATTR_STATE] = True
        self.data.setdefault("paused", False)

        return self.data

    @cached_property
    def address(self) -> str:
        """Full address

        :return str
        """

        return f"{self.ip}:{self.port}"

    @property
    def device_info(self) -> DeviceInfo:
        """Device info.

        :return DeviceInfo: Service DeviceInfo.
        """

        return DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self.address)},
            name=NAME,
            manufacturer=SERVICE_MANUFACTURER,
            model=SERVICE_MODEL,
            sw_version=self.data.get(ATTR_DEVICE_SW_VERSION, None),
            configuration_url=f"http://{self.address}/",
        )

    def schedule_refresh(self, offset: timedelta) -> None:
        """Schedule refresh.

        :param offset: timedelta
        """

        if self._unsub_refresh:  # type: ignore
            self._unsub_refresh()  # type: ignore
            self._unsub_refresh = None

        self._unsub_refresh = event.async_track_point_in_utc_time(
            self.hass,
            self._job,
            utcnow().replace(microsecond=0) + offset,
        )

    async def _async_prepare(self, method: str, data: dict) -> None:
        """Prepare data.

        :param method: str
        :param data: dict
        """

        action = getattr(self, f"_async_prepare_{method}")

        if action is not None:
            await action(data)

    async def _async_prepare_info(self, data: dict) -> None:
        """Prepare info.

        :param data: dict
        """

        # /api/info is the authoritative source for the LedFx application
        # version on both API generations.  Do not use
        # /api/config["configuration_version"] here: that value describes
        # the configuration schema (for example 2.3.6), not the running
        # LedFx software release.
        response: dict = await self.client.info()

        if "version" in response:
            data[ATTR_DEVICE_SW_VERSION] = str(response["version"])

        if "github_sha" in response:
            data[ATTR_DIAG_GITHUB_SHA] = str(response["github_sha"])

        if "is_release" in response:
            release_value = response["is_release"]
            data[ATTR_DIAG_RELEASE_BUILD] = (
                release_value
                if isinstance(release_value, bool)
                else str(release_value).strip().lower() == "true"
            )

        if "developer_mode" in response:
            data[ATTR_DIAG_DEVELOPER_MODE] = bool(response["developer_mode"])

        features = response.get("features", {})
        if isinstance(features, dict) and "sendspin" in features:
            data[ATTR_DIAG_SENDSPIN_AVAILABLE] = bool(features["sendspin"])

    async def _async_prepare_colors(self, data: dict) -> None:
        """Prepare colors.

        :param data: dict
        """

        if self.version != Version.V2:
            return

        response: dict = await self.client.colors()

        colors: dict = {}
        gradients: dict = {}
        if "colors" in response:
            if "builtin" in response["colors"]:
                colors |= response["colors"]["builtin"]
            if "user" in response["colors"]:
                colors |= response["colors"]["user"]

        if "gradients" in response:
            if "builtin" in response["gradients"]:
                gradients |= response["gradients"]["builtin"]
            if "user" in response["gradients"]:
                gradients |= response["gradients"]["user"]

        self.colors = colors
        self.gradients = gradients

    async def _async_prepare_schema(self, data: dict) -> None:
        """Prepare schema.

        :param data: dict
        """

        response: dict = await self.client.schema()

        if "effects" in response and response["effects"]:
            data[ATTR_LIGHT_EFFECTS] = sorted(list(response["effects"].keys()))

            ordered: list[tuple[str, str, str]] = sorted(
                (
                    (
                        fields.get("category", "Other"),
                        fields.get("name", effect.title()),
                        effect,
                    )
                    for effect, fields in response["effects"].items()
                ),
                key=lambda item: (item[0].lower(), item[1].lower()),
            )

            self.effect_options = [f"{category}: {name}" for category, name, _ in ordered]
            self.effect_label_to_id = {
                f"{category}: {name}": effect for category, name, effect in ordered
            }
            self.effect_id_to_label = {
                effect: label
                for label, effect in self.effect_label_to_id.items()
            }

            for effect, fields in response["effects"].items():
                for code, parameter in fields["schema"]["properties"].items():
                    if code == "brightness" or (
                        self.version == Version.V2 and code == "background_color"
                    ):
                        continue

                    if code in self.effect_properties:
                        if (
                            effect
                            not in self.effect_properties[code][ATTR_FIELD_EFFECTS]
                        ):
                            self.effect_properties[code][ATTR_FIELD_EFFECTS].append(
                                effect
                            )

                        continue

                    field, field_type, options = self._build_entity(code, parameter)

                    if field:
                        self.effect_properties[code] = {
                            ATTR_FIELD: field,
                            ATTR_FIELD_TYPE: field_type,
                            ATTR_FIELD_OPTIONS: options,
                            ATTR_FIELD_EFFECTS: [effect],
                        }

        if (
            "audio" in response
            and "schema" in response["audio"]
            and "properties" in response["audio"]["schema"]
            and "audio_device" in response["audio"]["schema"]["properties"]
            and "enum" in response["audio"]["schema"]["properties"]["audio_device"]
        ):
            data[ATTR_SELECT_AUDIO_INPUT_OPTIONS] = dict(
                response["audio"]["schema"]["properties"]["audio_device"]["enum"]
            )

            if (
                isinstance(data[ATTR_SELECT_AUDIO_INPUT], int)
                and str(data[ATTR_SELECT_AUDIO_INPUT])
                in data[ATTR_SELECT_AUDIO_INPUT_OPTIONS]
            ):
                data[ATTR_SELECT_AUDIO_INPUT] = data[ATTR_SELECT_AUDIO_INPUT_OPTIONS][
                    str(data[ATTR_SELECT_AUDIO_INPUT])
                ]

    def _build_entity(
        self, code: str, entity_data: dict
    ) -> tuple[EntityDescription | None, str | None, list | None]:
        """Build entity

        :param code: str: Code
        :param entity_data: dict: Entity data
        :return tuple[EntityDescription | None, str | None, list | None]
        """

        if entity_data.get("type") == "boolean":
            return (
                SwitchEntityDescription(
                    key=code,
                    name=entity_data.get("title", code.title()),
                    device_class=SwitchDeviceClass.SWITCH,
                    entity_category=EntityCategory.CONFIG,
                    entity_registry_enabled_default=False,
                ),
                "switch",
                None,
            )

        if entity_data.get("type") in ("integer", "number"):
            # LedFx schemas carry no explicit step. Use 1 for integers and a
            # fine step for floats; the previous behaviour derived the step from
            # `minimum`, producing coarse/fractional steps across many effects.
            is_int: bool = entity_data.get("type") == "integer"
            return (
                NumberEntityDescription(
                    key=code,
                    name=entity_data.get("title", code.title()),
                    native_max_value=float(entity_data.get("maximum", 0.0)),
                    native_min_value=float(entity_data.get("minimum", 0.0)),
                    native_step=1.0 if is_int else 0.01,
                    entity_category=EntityCategory.CONFIG,
                    entity_registry_enabled_default=False,
                ),
                "number",
                None,
            )

        if entity_data.get("type") in ("string", "color"):
            enum: list = entity_data.get("enum", [])
            field_type: str = "select"

            if entity_data.get("type") == "color":
                enum = list(
                    self.gradients.keys()
                    if entity_data.get("gradient", False)
                    else self.colors.keys()
                )
                field_type = "color"

            # Free-text / path / dynamic string params (e.g. texter2d.text,
            # gifplayer.image_location, blender.foreground) carry no enum, so a
            # select is meaningless. Expose them as editable text entities.
            if entity_data.get("type") == "string" and not enum:
                return (
                    TextEntityDescription(
                        key=code,
                        name=entity_data.get("title", code.title()),
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=False,
                    ),
                    "text",
                    None,
                )

            return (
                SelectEntityDescription(
                    key=code,
                    name=entity_data.get("title", code.title()),
                    entity_category=EntityCategory.CONFIG,
                    entity_registry_enabled_default=False,
                ),
                field_type,
                enum,
            )

        return None, None, None

    async def _async_prepare_config(self, data: dict) -> None:
        """Prepare config.

        :param data: dict
        """

        response: dict = await self.client.config()

        if "config" in response:
            await self._async_prepare_config_v1(data, response)

            return

        if "configuration_version" in response:
            self.version = Version.V2

            # configuration_version is the LedFx config schema version, not
            # the application/software version.  The latter is populated by
            # _async_prepare_info() from /api/info.
            await self._async_prepare_config_v2(data, response)

    async def _async_prepare_config_v1(self, data: dict, response: dict) -> None:
        """Prepare config V1.

        :param data: dict
        :param response: dict
        """

        if "audio" in response["config"]:
            for code, value in response["config"]["audio"].items():
                if code == "device_name":
                    data[ATTR_SELECT_AUDIO_INPUT] = response["config"]["audio"][
                        "device_name"
                    ]
                elif code != "device_index":
                    data[code] = value

                    if code in self.sensors:
                        continue

                    self.sensors[code] = LedFxEntityDescription(
                        description=SensorEntityDescription(
                            key=code,
                            name=code.replace("_", " ").title(),
                            entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False,
                        ),
                        device_info=self.device_info,
                    )

                    if self.new_sensor_callback:
                        async_dispatcher_send(
                            self.hass, SIGNAL_NEW_SENSOR, self.sensors[code]
                        )

        default_names, self.default_preset_ids = self._normalise_presets(
            response["config"].get("default_presets", {})
        )
        custom_names, self.custom_preset_ids = self._normalise_presets(
            response["config"].get("custom_presets", {})
        )
        data[ATTR_LIGHT_DEFAULT_PRESETS] = default_names
        data[ATTR_LIGHT_CUSTOM_PRESETS] = custom_names

    async def _async_prepare_config_v2(self, data: dict, response: dict) -> None:
        """Prepare config V2.

        :param data: dict
        :param response: dict
        """

        if "configuration_version" in response:
            data[ATTR_DIAG_CONFIGURATION_VERSION] = str(
                response["configuration_version"]
            )

        if "audio" in response:
            for code, value in response["audio"].items():
                if code == "audio_device":
                    data[ATTR_SELECT_AUDIO_INPUT] = int(
                        response["audio"]["audio_device"]
                    )
                elif code != "device_index":
                    data[code] = value

                    if code in self.sensors:
                        continue

                    self.sensors[code] = LedFxEntityDescription(
                        description=SensorEntityDescription(
                            key=code,
                            name=code.replace("_", " ").title(),
                            entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False,
                        ),
                        device_info=self.device_info,
                    )

                    if self.new_sensor_callback:
                        async_dispatcher_send(
                            self.hass, SIGNAL_NEW_SENSOR, self.sensors[code]
                        )

        default_names, self.default_preset_ids = self._normalise_presets(
            response.get("ledfx_presets", {})
        )
        custom_names, self.custom_preset_ids = self._normalise_presets(
            response.get("user_presets", {})
        )
        data[ATTR_LIGHT_DEFAULT_PRESETS] = default_names
        data[ATTR_LIGHT_CUSTOM_PRESETS] = custom_names

    @staticmethod
    def _normalise_presets(
        raw_presets: dict,
    ) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
        """Return display names plus a display-name -> preset-id map.

        LedFx 2.x stores presets as ``{preset_id: {name, config, ...}}``.
        Older integrations exposed the dictionary key directly, which loses
        names written in Cyrillic and other non-Latin scripts.
        """

        display_by_effect: dict[str, list[str]] = {}
        id_by_effect: dict[str, dict[str, str]] = {}

        if not isinstance(raw_presets, dict):
            return display_by_effect, id_by_effect

        for effect, presets in raw_presets.items():
            if not isinstance(presets, dict):
                continue

            labels: list[str] = []
            label_to_id: dict[str, str] = {}

            for raw_id, preset_data in presets.items():
                preset_id = str(raw_id)
                display_name = preset_id

                if isinstance(preset_data, dict):
                    candidate = preset_data.get("name")
                    if candidate is not None and str(candidate).strip():
                        display_name = str(candidate).strip()

                # Home Assistant select options must be unique.  A duplicate
                # display name is rare, but preserve access to both presets by
                # disambiguating the later one with its internal ID.
                label = display_name
                if label in label_to_id and label_to_id[label] != preset_id:
                    label = f"{display_name} [{preset_id}]"

                label_to_id[label] = preset_id
                labels.append(label)

            effect_id = str(effect)
            display_by_effect[effect_id] = sorted(
                dict.fromkeys(labels), key=str.casefold
            )
            id_by_effect[effect_id] = label_to_id

        return display_by_effect, id_by_effect

    def resolve_preset(
        self, effect: str, display_name: str
    ) -> tuple[str | None, str | None]:
        """Resolve a displayed preset name to API category and preset ID.

        User presets take precedence over built-ins, matching the behaviour of
        the previous integration when identical names exist in both groups.
        """

        if preset_id := self.custom_preset_ids.get(effect, {}).get(display_name):
            return "user_presets", preset_id

        if preset_id := self.default_preset_ids.get(effect, {}).get(display_name):
            return "ledfx_presets", preset_id

        return None, None

    def _preset_label_for_id(
        self, effect: str, category: str, preset_id: str
    ) -> str:
        """Return the Home Assistant option label for a LedFx preset ID."""

        mapping = (
            self.custom_preset_ids
            if category == "user_presets"
            else self.default_preset_ids
        )

        for label, mapped_id in mapping.get(effect, {}).items():
            if mapped_id == preset_id:
                return label

        return preset_id

    def _active_preset_from_response(self, response: dict) -> str | None:
        """Return the active preset label from a virtual presets response.

        LedFx 2.1.9 annotates presets with an ``active`` flag by comparing each
        preset config to the virtual's current active effect config. User
        presets are preferred if identical configs exist in both categories.
        """

        effect = response.get("effect")
        if not effect:
            return None

        effect = str(effect)
        for category in ("user_presets", "ledfx_presets"):
            presets = response.get(category, {})
            if not isinstance(presets, dict):
                continue

            for raw_id, preset_data in presets.items():
                if not isinstance(preset_data, dict) or not preset_data.get("active"):
                    continue

                preset_id = str(raw_id)
                label = self._preset_label_for_id(effect, category, preset_id)
                if label != preset_id:
                    return label

                name = preset_data.get("name")
                if name is not None and str(name).strip():
                    return str(name).strip()

                return preset_id

        return None

    async def _async_prepare_active_presets(
        self, data: dict, virtuals: dict
    ) -> None:
        """Refresh the active preset of every LedFx 2.x virtual."""

        for code, virtual in virtuals.items():
            active_key = f"{code}_{ATTR_LIGHT_ACTIVE_PRESET}"

            if (
                not isinstance(virtual, dict)
                or not virtual.get("effect")
                or not virtual.get("active", False)
            ):
                # While a virtual is inactive, keep Home Assistant's last
                # known preset.  Normal HA off/on no longer clears the effect,
                # and once LedFx is active again we read the authoritative
                # preset from /virtuals/{id}/presets.  This prevents an
                # inactive/transitional response from replacing the saved
                # preset with None/Default.
                continue

            try:
                response = await self.client.virtual_presets(str(code))
            except LedFxRequestError as err:
                # A virtual can temporarily have no active effect while LedFx is
                # stopped or transitioning. Preserve the last known preset
                # instead of making Home Assistant jump to another/default one.
                _LOGGER.debug(
                    "Unable to read active preset for LedFx virtual %s: %s",
                    code,
                    err,
                )
                continue

            data[active_key] = self._active_preset_from_response(response)

    async def _async_prepare_devices(self, data: dict) -> None:
        """Prepare devices.

        :param data: dict
        """

        response: dict = await self.client.devices()

        if "devices" not in response:
            return

        physical_devices = response.get("devices", {})
        if not isinstance(physical_devices, dict):
            physical_devices = {}

        data[ATTR_DIAG_PHYSICAL_DEVICES] = len(physical_devices)
        data[ATTR_DIAG_ONLINE_DEVICES] = sum(
            1
            for device in physical_devices.values()
            if isinstance(device, dict) and bool(device.get("online", False))
        )

        if self.version == Version.V1:
            if not physical_devices:  # pragma: no cover
                return

            self._build_device(data, physical_devices)

            return

        v_response: dict = await self.client.virtuals()
        data["paused"] = bool(v_response.get("paused", False))

        virtuals = v_response.get("virtuals", {})
        if not isinstance(virtuals, dict):
            virtuals = {}

        data[ATTR_DIAG_VIRTUALS] = len(virtuals)
        data[ATTR_DIAG_ACTIVE_VIRTUALS] = sum(
            1
            for virtual in virtuals.values()
            if isinstance(virtual, dict) and bool(virtual.get("active", False))
        )
        data[ATTR_DIAG_STREAMING_VIRTUALS] = sum(
            1
            for virtual in virtuals.values()
            if isinstance(virtual, dict) and bool(virtual.get("streaming", False))
        )

        if virtuals:
            devices: dict = {}
            for key, virtual in virtuals.items():
                devices[key] = virtual

                if (
                    virtual.get("is_device")
                    and virtual.get("is_device", "") in response["devices"]
                ):
                    devices[key]["config"] |= {
                        code: value
                        for code, value in response["devices"][
                            virtual.get("is_device")
                        ]["config"].items()
                        if code == "ip_address"
                    }
                    devices[key]["type"] = response["devices"][
                        virtual.get("is_device")
                    ]["type"]

            self._build_device(data, devices)
            await self._async_prepare_active_presets(data, virtuals)

    def _build_device(self, data: dict, devices: dict) -> None:
        """Build device

        :param data: dict
        :param devices: dict
        """

        for code, device in devices.items():
            has_effect = bool("effect" in device and device["effect"])
            # LedFx 2.x can keep an effect configured while the virtual is
            # inactive.  Reflect the virtual output state as the HA light state
            # without discarding the retained effect/preset configuration.
            data[f"{code}_{ATTR_LIGHT_STATE}"] = bool(
                has_effect
                and (
                    self.version != Version.V2
                    or device.get("active", False)
                )
            )

            if has_effect:
                data |= {
                    f"{code}_{ATTR_LIGHT_BRIGHTNESS}": (
                        convert_brightness(
                            float(device["effect"]["config"]["brightness"]), True
                        )
                        if data[f"{code}_{ATTR_LIGHT_STATE}"]
                        else 0
                    ),
                    f"{code}_{ATTR_LIGHT_EFFECT}": device["effect"].get("type"),
                    f"{code}_{ATTR_LIGHT_EFFECT_CONFIG}": self._convert_effect_config(
                        device["effect"]["config"]
                    ),
                }
            else:
                # If LedFx genuinely has no configured effect, keep the last
                # known effect/preset metadata instead of substituting the
                # first effect from the global list.
                data[f"{code}_{ATTR_LIGHT_BRIGHTNESS}"] = 0

            if self.version == Version.V2 and has_effect:
                data[f"{code}_{ATTR_LIGHT_COLOR}"] = device["effect"]["config"].get(
                    "background_color"
                )

            data[f"{code}_{ATTR_LIGHT_CONFIG}"] = {
                config: value
                for config, value in device.get("config", {}).items()
                if config not in ["icon_name", "name"]
            }

            device_config: dict = device.get("config", {})
            device_info: DeviceInfo = DeviceInfo(
                identifiers={
                    (DOMAIN, device_config.get("ip_address", f"{self.address}-{code}"))
                },
                name=device_config.get("name", code),
                model=device_config.get("type"),
                configuration_url=f"http://{self.address}/devices/{code}",
            )

            self._prepare_device_fields(code, device_info)

            for select_key, select_name, select_icon, select_type in (
                (
                    ATTR_SELECT_DEVICE_EFFECT,
                    ATTR_SELECT_DEVICE_EFFECT_NAME,
                    "mdi:auto-fix",
                    ActionType.DEVICE_EFFECT,
                ),
                (
                    ATTR_SELECT_DEVICE_PRESET,
                    ATTR_SELECT_DEVICE_PRESET_NAME,
                    "mdi:playlist-star",
                    ActionType.DEVICE_PRESET,
                ),
            ):
                field_key: str = f"{code}_{select_key}"
                if field_key in self.selects:
                    continue

                self.selects[field_key] = LedFxEntityDescription(
                    description=SelectEntityDescription(
                        key=select_key,
                        name=select_name,
                        icon=select_icon,
                        entity_category=EntityCategory.CONFIG,
                        entity_registry_enabled_default=True,
                    ),
                    type=select_type,
                    device_info=device_info,
                    device_code=code,
                )

                if self.new_select_callback:
                    async_dispatcher_send(
                        self.hass, SIGNAL_NEW_SELECT, self.selects[field_key]
                    )

            if code in self.devices:
                continue

            icon: str = device_config.get("icon_name", "")

            self.devices[code] = LedFxEntityDescription(
                description=LightEntityDescription(
                    key=code,
                    name=device_config.get("name", code),
                    icon=icon if icon.startswith("mdi:") else "mdi:led-strip-variant",
                    entity_registry_enabled_default=True,
                ),
                type=ActionType.DEVICE,
                device_info=device_info,
            )

            if self.new_device_callback:
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, self.devices[code])

    def _convert_effect_config(self, config: dict) -> dict:
        """Convert effect config

        :param config: dict
        :return dict
        """

        for code, value in config.items():
            if (
                code in self.effect_properties
                and self.effect_properties[code][ATTR_FIELD_TYPE] == "color"
            ):
                colors = self.colors if value in self.colors else self.gradients
                for name, color in colors.items():
                    if color == value:
                        config[code] = name

                        break

        return config

    def _prepare_device_fields(self, code: str, device_info: DeviceInfo) -> None:
        """Prepare device fields

        :param code: str: Device code
        :param device_info: DeviceInfo: Device Info object
        """

        for prop, info in self.effect_properties.items():
            field: LedFxEntityDescription | None = None
            signal: str | None = None

            if isinstance(info[ATTR_FIELD], NumberEntityDescription):
                if f"{code}_{prop}" in self.numbers:
                    continue

                field = self.numbers[f"{code}_{prop}"] = LedFxEntityDescription(
                    description=info[ATTR_FIELD],
                    type=ActionType.DEVICE,
                    device_info=device_info,
                    device_code=code,
                    extra={
                        ATTR_FIELD_EFFECTS: sorted(info.get(ATTR_FIELD_EFFECTS, {})),
                        ATTR_FIELD_TYPE: info.get(ATTR_FIELD_TYPE),
                    },
                )

                if self.new_number_callback:
                    signal = SIGNAL_NEW_NUMBER
            elif isinstance(info[ATTR_FIELD], SwitchEntityDescription):
                if f"{code}_{prop}" in self.switches:
                    continue

                field = self.switches[f"{code}_{prop}"] = LedFxEntityDescription(
                    description=info[ATTR_FIELD],
                    type=ActionType.DEVICE,
                    device_info=device_info,
                    device_code=code,
                    extra={
                        ATTR_FIELD_EFFECTS: sorted(info.get(ATTR_FIELD_EFFECTS, {})),
                        ATTR_FIELD_TYPE: info.get(ATTR_FIELD_TYPE),
                    },
                )

                if self.new_switch_callback:
                    signal = SIGNAL_NEW_SWITCH
            elif isinstance(info[ATTR_FIELD], SelectEntityDescription):
                if f"{code}_{prop}" in self.selects:
                    continue

                field = self.selects[f"{code}_{prop}"] = LedFxEntityDescription(
                    description=info[ATTR_FIELD],
                    type=ActionType.DEVICE,
                    device_info=device_info,
                    device_code=code,
                    extra={
                        ATTR_FIELD_EFFECTS: sorted(info.get(ATTR_FIELD_EFFECTS, [])),
                        ATTR_FIELD_OPTIONS: sorted(info.get(ATTR_FIELD_OPTIONS, [])),
                        ATTR_FIELD_TYPE: info.get(ATTR_FIELD_TYPE),
                    },
                )

                if self.new_select_callback:
                    signal = SIGNAL_NEW_SELECT
            elif isinstance(info[ATTR_FIELD], TextEntityDescription):
                if f"{code}_{prop}" in self.texts:
                    continue

                field = self.texts[f"{code}_{prop}"] = LedFxEntityDescription(
                    description=info[ATTR_FIELD],
                    type=ActionType.DEVICE,
                    device_info=device_info,
                    device_code=code,
                    extra={
                        ATTR_FIELD_EFFECTS: sorted(info.get(ATTR_FIELD_EFFECTS, {})),
                        ATTR_FIELD_TYPE: info.get(ATTR_FIELD_TYPE),
                    },
                )

                if self.new_text_callback:
                    signal = SIGNAL_NEW_TEXT

            if field is not None and signal is not None:
                async_dispatcher_send(
                    self.hass,
                    signal,
                    field,
                )

    async def _async_prepare_audio_devices(self, data: dict) -> None:
        """Prepare audio_devices.

        :param data: dict
        """

        if self.version != Version.V1:
            return

        response: dict = await self.client.audio_devices()

        if "devices" in response:
            data[ATTR_SELECT_AUDIO_INPUT_OPTIONS] = dict(response["devices"])



@dataclass
class LedFxEntityDescription:
    """LedFx entity description."""

    description: EntityDescription
    device_info: DeviceInfo
    device_code: str | None = None
    type: ActionType = ActionType.DEFAULT
    extra: dict | None = None


def convert_brightness(brightness: float, is_reverse: bool = False) -> float:
    """Convert brightness

    :param brightness: float
    :param is_reverse: bool
    :return: float
    """

    if is_reverse:
        return min(float(math.ceil(brightness * 100 * 2.55)), 255)

    # pylint: disable=consider-using-f-string
    return float("{:.1f}".format(min(float(brightness / 100 / 2.55), 1.0)))


@callback
def async_get_updater(hass: HomeAssistant, identifier: str) -> LedFxUpdater:
    """Return LedFxUpdater for ip address or entry id.

    :param hass: HomeAssistant
    :param identifier: str
    :return LedFxUpdater
    """

    if (
        DOMAIN not in hass.data
        or identifier not in hass.data[DOMAIN]
        or UPDATER not in hass.data[DOMAIN][identifier]
    ):
        raise ValueError(f"Integration with identifier: {identifier} not found.")

    return hass.data[DOMAIN][identifier][UPDATER]
