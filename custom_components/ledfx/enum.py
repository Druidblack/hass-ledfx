"""Enums."""

from __future__ import annotations

from enum import Enum, StrEnum


class Method(StrEnum):
    """Method enum"""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"


class Version(Enum):
    """Version enum"""

    V1 = 1
    V2 = 2


class ActionType(StrEnum):
    """ActionType enum"""

    DEFAULT = "default"
    DEVICE = "device"
    DEVICE_PRESET = "device_preset"
    DEVICE_EFFECT = "device_effect"


class EffectCategory(StrEnum):
    """EffectCategory enum"""

    NONE = "none"
    # Sent on the wire as the `category` field of PUT /api/virtuals/{id}/presets.
    # LedFx 2.x (verified against 2.1.9) only accepts "ledfx_presets"/"user_presets";
    # the old "default_presets"/"custom_presets" values are rejected.
    DEFAULT = "ledfx_presets"
    CUSTOM = "user_presets"
