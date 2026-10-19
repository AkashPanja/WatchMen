"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.watchmen.const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAMERAS,
    CONF_FACE_BUFFER_INTERVAL,
    CONF_FRAMES_BACK,
    CONF_FRAMES_FORWARD,
    CONF_IDENTIFY_THRESHOLD,
    CONF_LLM_API_KEY,
    CONF_LLM_PROVIDER,
    CONF_MODE,
    DOMAIN,
)

from .helpers import CAMERA_ENTITY, register_camera

USER_INPUT = {CONF_CAMERAS: [CAMERA_ENTITY], CONF_MODE: "auto"}

OPTIONS_INPUT = {
    CONF_CAMERAS: [CAMERA_ENTITY],
    CONF_MODE: "frame_diff",
    "frame_interval": 5.0,
    "diff_threshold": 0.05,
    "motion_off_delay": 15,
    "residents": "Alice, Bob",
    CONF_AI_TASK_ENTITY: "",
    "confidence_threshold": 0.9,
    "cooldown": 60,
    CONF_LLM_PROVIDER: "ai_task",
    CONF_LLM_API_KEY: "",
    "llm_model": "",
    "llm_base_url": "",
    CONF_FACE_BUFFER_INTERVAL: 1.5,
    CONF_FRAMES_BACK: 6,
    CONF_FRAMES_FORWARD: 3,
    CONF_IDENTIFY_THRESHOLD: 85,
}


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
        OPTIONS_INPUT,
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["residents"] == "Alice, Bob"
    assert entry.options["confidence_threshold"] == 0.9
    assert entry.options[CONF_AI_TASK_ENTITY] == ""
    assert entry.options[CONF_LLM_PROVIDER] == "ai_task"
    assert entry.options[CONF_FACE_BUFFER_INTERVAL] == 1.5
    assert entry.options[CONF_FRAMES_BACK] == 6
    assert entry.options[CONF_FRAMES_FORWARD] == 3
    assert entry.options[CONF_IDENTIFY_THRESHOLD] == 85


async def test_options_flow_partial_input_uses_defaults(hass: HomeAssistant) -> None:
    """Old/partial submissions still save; new fields fall back to defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    await _create_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CAMERAS: [CAMERA_ENTITY],
            CONF_MODE: "frame_diff",
            "confidence_threshold": 0.95,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LLM_PROVIDER] == "ai_task"
    assert entry.options[CONF_FRAMES_BACK] == 4
    assert entry.options[CONF_IDENTIFY_THRESHOLD] == 70


async def test_options_flow_requires_api_key_for_direct_provider(
    hass: HomeAssistant,
) -> None:
    """A direct provider without an API key is rejected, then accepted."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        title="Watchmen",
        unique_id=CAMERA_ENTITY,
    )
    await _create_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    no_key = {**OPTIONS_INPUT, CONF_LLM_PROVIDER: "openai", CONF_LLM_API_KEY: ""}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], no_key
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "api_key_required"}
    assert CONF_LLM_PROVIDER not in entry.options

    with_key = {**no_key, CONF_LLM_API_KEY: "sk-test"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], with_key
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LLM_PROVIDER] == "openai"
    assert entry.options[CONF_LLM_API_KEY] == "sk-test"
