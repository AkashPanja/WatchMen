"""Blueprint validation: the gate access blueprint must load and compile."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.setup import async_setup_component

from .helpers import CAMERA_ENTITY, make_event_entry, register_event_camera

REPO_ROOT = Path(__file__).parent.parent
BLUEPRINT_SRC = REPO_ROOT / "blueprints" / "automation" / "watchmen" / "gate_access.yaml"

DOORBELL = "binary_sensor.doorbell"

BLUEPRINT_AUTOMATION = {
    "automation": [
        {
            "alias": "Watchmen blueprint test",
            "use_blueprint": {
                "path": "watchmen/gate_access.yaml",
                "input": {
                    "trigger": [
                        {
                            "platform": "state",
                            "entity_id": DOORBELL,
                            "to": "on",
                        }
                    ],
                    "camera": CAMERA_ENTITY,
                    "context_note": "doorbell pressed",
                    "min_confidence": 0.9,
                    "approved_actions": [
                        {
                            "action": "input_boolean.turn_on",
                            "target": {"entity_id": "input_boolean.gate"},
                        }
                    ],
                    "denied_actions": [
                        {
                            "action": "input_boolean.turn_off",
                            "target": {"entity_id": "input_boolean.gate"},
                        }
                    ],
                },
            },
        }
    ]
}


async def _setup_stack(hass: HomeAssistant, ai_response: Any) -> list[dict]:
    """Set up Watchmen + input_boolean + blueprint automation + fake AI."""
    calls: list[dict] = []

    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"gate": {}}}
    )
    await hass.async_block_till_done()

    async def _fake(call):
        calls.append(dict(call.data))
        return ai_response

    hass.services.async_register(
        "ai_task",
        "generate_data",
        _fake,
        supports_response=SupportsResponse.ONLY,
    )

    dest_dir = Path(hass.config.path("blueprints", "automation", "watchmen"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT_SRC, dest_dir / "gate_access.yaml")

    assert await async_setup_component(
        hass, "automation", BLUEPRINT_AUTOMATION
    )
    await hass.async_block_till_done()
    return calls


async def test_gate_blueprint_loads_and_compiles(hass: HomeAssistant) -> None:
    """Copy the blueprint into the config dir and build an automation from it."""
    # Watchmen must be loaded so watchmen.request_access validates.
    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # input_boolean gives the blueprint a real unlock target.
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"gate": {}}}
    )
    await hass.async_block_till_done()

    dest_dir = Path(hass.config.path("blueprints", "automation", "watchmen"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT_SRC, dest_dir / "gate_access.yaml")

    assert await async_setup_component(
        hass, "automation", BLUEPRINT_AUTOMATION
    )
    await hass.async_block_till_done()

    # The automation must exist and have picked up the blueprint trigger.
    automations = hass.states.async_entity_ids("automation")
    assert automations, "automation was not created from the blueprint"


async def test_trigger_with_approval_unlocks_gate(hass: HomeAssistant) -> None:
    """Generic trigger + AI approval runs the unlock action."""
    await _setup_stack(
        hass,
        {"approved": True, "person": "Alice", "confidence": 0.97, "reason": "ok"},
    )
    assert hass.states.get("input_boolean.gate").state == "off"

    hass.states.async_set(DOORBELL, "on")
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert hass.states.get("input_boolean.gate").state == "on"


async def test_trigger_with_denial_keeps_gate_locked(hass: HomeAssistant) -> None:
    """Generic trigger + AI denial runs denied actions: gate stays locked."""
    await _setup_stack(
        hass,
        {"approved": False, "person": "unknown", "confidence": 0.4, "reason": "stranger"},
    )
    assert hass.states.get("input_boolean.gate").state == "off"

    hass.states.async_set(DOORBELL, "on")
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert hass.states.get("input_boolean.gate").state == "off"


async def test_low_confidence_keeps_gate_locked(hass: HomeAssistant) -> None:
    """An 'approved' answer below the blueprint's confidence stays locked."""
    await _setup_stack(
        hass,
        {"approved": True, "person": "Alice", "confidence": 0.5, "reason": "blurry"},
    )

    hass.states.async_set(DOORBELL, "on")
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert hass.states.get("input_boolean.gate").state == "off"
