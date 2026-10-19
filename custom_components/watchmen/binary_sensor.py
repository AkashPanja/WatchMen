"""Binary sensors exposing Watchmen motion state."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CAMERAS, DOMAIN, MODE_FRAME_DIFF
from .coordinator import CameraRuntime, WatchmenCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Watchmen motion binary sensors."""
    coordinator: WatchmenCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WatchmenMotionSensor(hass, coordinator, entry, camera)
        for camera in entry.options.get(
            CONF_CAMERAS, entry.data.get(CONF_CAMERAS, [])
        )
        if coordinator.get_runtime(camera) is not None
    )


class WatchmenMotionSensor(
    CoordinatorEntity[WatchmenCoordinator], BinarySensorEntity
):
    """Unified motion state for one camera."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_has_entity_name = True
    _attr_translation_key = "motion"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: WatchmenCoordinator,
        entry: ConfigEntry,
        camera_entity_id: str,
    ) -> None:
        """Initialize the motion sensor."""
        super().__init__(coordinator)
        self._camera = camera_entity_id
        self._attr_unique_id = f"{entry.entry_id}_{camera_entity_id}_motion"
        self._attr_device_info = build_device_info(
            hass, entry, camera_entity_id
        )

    @property
    def runtime(self) -> CameraRuntime | None:
        """Return the runtime state for this camera."""
        return self.coordinator.get_runtime(self._camera)

    @property
    def available(self) -> bool:
        """Available unless frame analysis cannot fetch snapshots."""
        runtime = self.runtime
        if runtime is None:
            return False
        if runtime.mode == MODE_FRAME_DIFF:
            return runtime.image_ok
        return True

    @property
    def is_on(self) -> bool | None:
        """Return true if motion is detected."""
        runtime = self.runtime
        return runtime.motion if runtime is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostics about detection."""
        runtime = self.runtime
        if runtime is None:
            return {}
        attrs: dict[str, Any] = {
            "detection_mode": runtime.mode,
            "source_camera": self._camera,
        }
        if runtime.last_motion is not None:
            attrs["last_detection"] = runtime.last_motion.isoformat()
        if runtime.last_change_ratio is not None:
            attrs["change_ratio"] = round(runtime.last_change_ratio, 4)
        if runtime.source_sensors:
            attrs["source_sensors"] = runtime.source_sensors
        return attrs


def build_device_info(
    hass: HomeAssistant, entry: ConfigEntry, camera_entity_id: str
) -> DeviceInfo:
    """Build device info, linked to the camera's device when possible."""
    name = camera_entity_id
    via_device_id: str | None = None

    entity_registry = er.async_get(hass)
    camera_entry = entity_registry.async_get(camera_entity_id)
    if camera_entry is not None:
        state = hass.states.get(camera_entity_id)
        if state is not None and state.name:
            name = state.name
        via_device_id = camera_entry.device_id

    info: dict[str, Any] = {
        "identifiers": {(DOMAIN, f"{entry.entry_id}_{camera_entity_id}")},
        "name": name,
        "manufacturer": "Watchmen",
        "model": "Motion detector",
    }
    if via_device_id is not None:
        info["via_device_id"] = via_device_id
    return DeviceInfo(**info)
