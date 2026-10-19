"""Motion detection tests: event mode and frame analysis mode."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.components.camera import Image
from homeassistant.const import CONF_MODE, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.watchmen.const import (
    CONF_CAMERAS,
    CONF_DIFF_THRESHOLD,
    CONF_FRAME_INTERVAL,
    CONF_MOTION_OFF_DELAY,
    DOMAIN,
    MODE_EVENT,
    MODE_FRAME_DIFF,
)

from .helpers import (
    CAMERA_ENTITY,
    MOTION_SOURCE,
    decision_unique_id,
    make_event_entry,
    make_jpeg,
    motion_unique_id,
    register_camera,
    register_event_camera,
)


async def _motion_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the entity id of the Watchmen motion sensor."""
    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, motion_unique_id(entry)
    )
    assert entity_id is not None, "motion entity not found"
    return entity_id


async def _decision_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the entity id of the Watchmen decision sensor."""
    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, decision_unique_id(entry)
    )
    assert entity_id is not None, "decision entity not found"
    return entity_id


async def test_event_mode_motion(hass: HomeAssistant) -> None:
    """Event mode mirrors the camera's motion binary sensor."""
    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    motion_id = await _motion_entity_id(hass, entry)
    assert hass.states.get(motion_id).state == STATE_OFF
    # Event mode must not poll.
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.runtimes[CAMERA_ENTITY].mode == MODE_EVENT
    assert coordinator.update_interval is None

    hass.states.async_set(
        MOTION_SOURCE,
        STATE_ON,
        {"device_class": "motion", "friendly_name": "Source Motion"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(motion_id).state == STATE_ON
    assert (
        hass.states.get(motion_id).attributes["detection_mode"] == MODE_EVENT
    )

    hass.states.async_set(
        MOTION_SOURCE,
        STATE_OFF,
        {"device_class": "motion", "friendly_name": "Source Motion"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(motion_id).state == STATE_OFF


async def test_event_mode_stuck_on_clears_after_delay(
    hass: HomeAssistant,
) -> None:
    """A sensor stuck ON clears after the auto-off delay."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAMERAS: [CAMERA_ENTITY],
            CONF_MODE: MODE_EVENT,
        },
        options={CONF_MOTION_OFF_DELAY: 20},
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    motion_id = await _motion_entity_id(hass, entry)

    hass.states.async_set(
        MOTION_SOURCE,
        STATE_ON,
        {"device_class": "motion", "friendly_name": "Source Motion"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(motion_id).state == STATE_ON

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=21))
    await hass.async_block_till_done()
    assert hass.states.get(motion_id).state == STATE_OFF


async def test_frame_diff_motion(hass: HomeAssistant) -> None:
    """Frame analysis detects motion from changing snapshots."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CAMERAS: [CAMERA_ENTITY],
            CONF_MODE: MODE_FRAME_DIFF,
        },
        options={
            CONF_FRAME_INTERVAL: 5,
            CONF_DIFF_THRESHOLD: 0.03,
            CONF_MOTION_OFF_DELAY: 30,
        },
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    entry.add_to_hass(hass)
    register_camera(hass, entry)

    first = Image(content_type="image/jpeg", content=make_jpeg((255, 255, 255)))
    changed = Image(content_type="image/jpeg", content=make_jpeg((0, 0, 0)))

    with patch(
        "custom_components.watchmen.coordinator.async_get_image",
        AsyncMock(side_effect=[first, first, changed]),
    ) as mock_image:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        motion_id = await _motion_entity_id(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.runtimes[CAMERA_ENTITY].mode == MODE_FRAME_DIFF
        assert hass.states.get(motion_id).state == STATE_OFF

        # Unchanged frame: still no motion.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(motion_id).state == STATE_OFF

        # Changed frame: motion on.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(motion_id).state == STATE_ON
        attrs = hass.states.get(motion_id).attributes
        assert attrs["change_ratio"] > 0.03
        assert mock_image.await_count == 3

        # Frame fetch failure marks the sensor unavailable.
        mock_image.side_effect = RuntimeError("camera offline")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(motion_id).state == STATE_UNAVAILABLE

    await hass.config_entries.async_unload(entry.entry_id)


async def test_auto_mode_falls_back_to_frame_diff(
    hass: HomeAssistant,
) -> None:
    """Auto mode uses frame analysis when no motion sensor exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CAMERAS: [CAMERA_ENTITY], CONF_MODE: "auto"},
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    entry.add_to_hass(hass)
    register_camera(hass, entry)  # camera device without motion sensors

    with patch(
        "custom_components.watchmen.coordinator.async_get_image",
        AsyncMock(return_value=Image(content_type="image/jpeg", content=make_jpeg((1, 2, 3)))),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.runtimes[CAMERA_ENTITY].mode == MODE_FRAME_DIFF
    await hass.config_entries.async_unload(entry.entry_id)
