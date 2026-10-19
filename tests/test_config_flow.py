"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.watchmen.const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAMERAS,
    CONF_MODE,
    DOMAIN,
)

from .helpers import CAMERA_ENTITY, register_camera

USER_INPUT = {CONF_CAMERAS: [CAMERA_ENTITY], CONF_MODE: "auto"}


async def _create_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Register a config entry and camera prerequisites."""
    entry.add_to_hass(hass)
    register_camera(hass, entry)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """Test the user config step creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Watchmen"
    assert result["data"][CONF_CAMERAS] == [CAMERA_ENTITY]
    assert result["data"][CONF_MODE] == "auto"


async def test_duplicate_cameras_aborts(hass: HomeAssistant) -> None:
    """Adding the same camera set twice aborts."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], USER_INPUT)

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant) -> None:
    """Test the options flow stores updated options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    await _create_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CAMERAS: [CAMERA_ENTITY],
            CONF_MODE: "frame_diff",
            "frame_interval": 5.0,
            "diff_threshold": 0.05,
            "motion_off_delay": 15,
            "residents": "Alice, Bob",
            CONF_AI_TASK_ENTITY: "",
            "confidence_threshold": 0.9,
            "cooldown": 60,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["residents"] == "Alice, Bob"
    assert entry.options["confidence_threshold"] == 0.9
    assert entry.options[CONF_AI_TASK_ENTITY] == ""
