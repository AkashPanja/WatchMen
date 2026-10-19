"""Test helpers for Watchmen."""

from __future__ import annotations

import io
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from PIL import Image as PILImage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.watchmen.const import CONF_CAMERAS, CONF_MODE, MODE_EVENT

MOTION_SOURCE = "binary_sensor.test_source_motion"
CAMERA_ENTITY = "camera.test_cam"


def register_fake_ai_task(
    hass: HomeAssistant, responses: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Register a fake ai_task.generate_data; returns the recorded calls."""
    calls: list[dict[str, Any]] = []

    async def _fake(call):
        calls.append(dict(call.data))
        task_name = call.data.get("task_name", "")
        return {"data": responses.get(task_name, {})}

    hass.services.async_register(
        "ai_task",
        "generate_data",
        _fake,
        supports_response=SupportsResponse.ONLY,
    )
    return calls


def make_jpeg(color: tuple[int, int, int]) -> bytes:
    """Create a tiny JPEG of a solid color."""
    buffer = io.BytesIO()
    PILImage.new("RGB", (64, 48), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def register_camera(
    hass: HomeAssistant,
    entry: ConfigEntry,
    entity_id: str = CAMERA_ENTITY,
    friendly_name: str = "Test Cam",
) -> None:
    """Create a camera entity (registry + state) on its own device."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", f"device_{entity_id}")},
        name=friendly_name,
    )
    entity_registry.async_get_or_create(
        "camera",
        "test",
        f"cam_{entity_id}",
        suggested_object_id=entity_id.split(".", 1)[1],
        device_id=device.id,
    )
    hass.states.async_set(
        entity_id, "idle", attributes={"friendly_name": friendly_name}
    )


def register_event_camera(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> str:
    """Create a camera plus a motion binary sensor on the same device."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("test", "device_event_cam")},
        name="Event Cam",
    )
    entity_registry.async_get_or_create(
        "camera",
        "test",
        "event_cam",
        suggested_object_id="test_cam",
        device_id=device.id,
    )
    entity_registry.async_get_or_create(
        "binary_sensor",
        "test",
        "source_motion",
        suggested_object_id="test_source_motion",
        device_id=device.id,
    )
    hass.states.async_set(
        CAMERA_ENTITY, "idle", attributes={"friendly_name": "Event Cam"}
    )
    hass.states.async_set(
        MOTION_SOURCE,
        "off",
        attributes={"device_class": "motion", "friendly_name": "Source Motion"},
    )
    return MOTION_SOURCE


def make_event_entry(options: dict | None = None) -> MockConfigEntry:
    """Config entry in event mode."""
    return MockConfigEntry(
        domain="watchmen",
        data={CONF_CAMERAS: [CAMERA_ENTITY], CONF_MODE: MODE_EVENT},
        options=options or {},
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )


def motion_unique_id(entry: ConfigEntry) -> str:
    """Unique id of the motion binary sensor."""
    return f"{entry.entry_id}_{CAMERA_ENTITY}_motion"


def decision_unique_id(entry: ConfigEntry) -> str:
    """Unique id of the decision sensor."""
    return f"{entry.entry_id}_{CAMERA_ENTITY}_access_decision"


def get_entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    """Resolve an entity id from its unique id."""
    entity_registry = er.async_get(hass)
    return entity_registry.async_get_entity_id("binary_sensor", "watchmen", unique_id) or (
        entity_registry.async_get_entity_id("sensor", "watchmen", unique_id)
    )
