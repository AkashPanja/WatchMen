"""Sensors exposing the latest Watchmen access decision."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .binary_sensor import build_device_info
from .const import (
    ATTR_APPROVED,
    ATTR_CONFIDENCE,
    ATTR_CONTEXT,
    ATTR_PERSON,
    ATTR_REASON,
    CONF_CAMERAS,
    DOMAIN,
)
from .coordinator import CameraRuntime, WatchmenCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Watchmen decision sensors."""
    coordinator: WatchmenCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WatchmenAccessDecisionSensor(hass, coordinator, entry, camera)
        for camera in entry.options.get(
            CONF_CAMERAS, entry.data.get(CONF_CAMERAS, [])
        )
        if coordinator.get_runtime(camera) is not None
    )


class WatchmenAccessDecisionSensor(
    CoordinatorEntity[WatchmenCoordinator], SensorEntity
):
    """Latest LLM access decision for one camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "access_decision"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: WatchmenCoordinator,
        entry: ConfigEntry,
        camera_entity_id: str,
    ) -> None:
        """Initialize the decision sensor."""
        super().__init__(coordinator)
        self._camera = camera_entity_id
        self._attr_unique_id = f"{entry.entry_id}_{camera_entity_id}_access_decision"
        self._attr_device_info = build_device_info(hass, entry, camera_entity_id)

    @property
    def runtime(self) -> CameraRuntime | None:
        """Return the runtime state for this camera."""
        return self.coordinator.get_runtime(self._camera)

    @property
    def native_value(self) -> str | None:
        """Return approved/denied, or None before the first review."""
        runtime = self.runtime
        if runtime is None or runtime.decision is None:
            return None
        return "approved" if runtime.decision.get(ATTR_APPROVED) else "denied"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Details of the latest decision."""
        runtime = self.runtime
        if runtime is None or runtime.decision is None:
            return {"source_camera": self._camera}
        decision = runtime.decision
        attrs: dict[str, Any] = {
            "source_camera": self._camera,
            ATTR_PERSON: decision.get(ATTR_PERSON),
            ATTR_CONFIDENCE: decision.get(ATTR_CONFIDENCE),
            ATTR_REASON: decision.get(ATTR_REASON),
            ATTR_CONTEXT: decision.get(ATTR_CONTEXT),
        }
        reviewed: datetime | None = runtime.last_decision_at
        if reviewed is not None:
            attrs["reviewed_at"] = reviewed.isoformat()
        return attrs
