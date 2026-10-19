"""Blueprint validation: gate access + person identification blueprints."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.setup import async_setup_component

from custom_components.watchmen.const import EVENT_PERSON_IDENTIFIED
from custom_components.watchmen.faces import FaceStore
from custom_components.watchmen.identify import TASK_FACE_MATCH, TASK_FACE_SELECT

from .helpers import (
    CAMERA_ENTITY,
    make_event_entry,
    make_jpeg,
    register_event_camera,
    register_fake_ai_task,
)

REPO_ROOT = Path(__file__).parent.parent
BLUEPRINT_SRC = REPO_ROOT / "blueprints" / "automation" / "watchmen" / "gate_access.yaml"
IDENTIFY_BLUEPRINT_SRC = (
    REPO_ROOT / "blueprints" / "automation" / "watchmen" / "identify_person.yaml"
)

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

IDENTIFY_AUTOMATION = {
    "automation": [
        {
            "alias": "Watchmen identify blueprint test",
            "use_blueprint": {
                "path": "watchmen/identify_person.yaml",
                "input": {
                    "trigger": [
                        {
                            "platform": "state",
                            "entity_id": DOORBELL,
                            "to": "on",
                        }
                    ],
                    "camera": CAMERA_ENTITY,
                    "context_note": "bell pressed",
                    "frames_back": 1,
                    "frames_forward": 0,
                    "min_confidence": 70,
                    "matched_actions": [
                        {
                            "action": "input_boolean.turn_on",
                            "target": {"entity_id": "input_boolean.gate"},
                        }
                    ],
                    "unmatched_actions": [
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


# ----------------------------------------------------------------------
# Person identification blueprint
# ----------------------------------------------------------------------
SELECT_FACE = {
    "face_visible": True,
    "frame_index": 0,
    "bbox_x": 0.25,
    "bbox_y": 0.2,
    "bbox_w": 0.4,
    "bbox_h": 0.5,
    "reason": "face clearly visible",
}


async def _setup_identify_stack(
    hass: HomeAssistant, responses: dict[str, Any]
) -> list[dict[str, Any]]:
    """Watchmen + input_boolean + identify blueprint + keyed fake AI."""
    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await FaceStore(hass).async_add("Alice", make_jpeg((210, 170, 140)))

    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"gate": {}}}
    )
    await hass.async_block_till_done()

    calls = register_fake_ai_task(hass, responses)

    dest_dir = Path(hass.config.path("blueprints", "automation", "watchmen"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(IDENTIFY_BLUEPRINT_SRC, dest_dir / "identify_person.yaml")

    assert await async_setup_component(
        hass, "automation", IDENTIFY_AUTOMATION
    )
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids("automation")
    return calls


async def _fire_trigger(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Fire the doorbell with a mocked frame; return recorded AI calls."""
    events: list[dict[str, Any]] = []
    hass.bus.async_listen(
        EVENT_PERSON_IDENTIFIED, lambda event: events.append(dict(event.data))
    )
    frame = Image(content_type="image/jpeg", content=make_jpeg((90, 80, 70)))
    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(return_value=frame),
    ):
        hass.states.async_set(DOORBELL, "on")
        await hass.async_block_till_done()
        await hass.async_block_till_done()
    await hass.async_block_till_done()
    return events


async def test_identify_blueprint_match_unlocks_gate(hass: HomeAssistant) -> None:
    """Identified face above the blueprint percentage runs matched actions."""
    calls = await _setup_identify_stack(
        hass,
        {
            TASK_FACE_SELECT: SELECT_FACE,
            TASK_FACE_MATCH: {
                "name": "Alice",
                "confidence": 87,
                "reason": "matches enrolled Alice",
            },
        },
    )

    assert hass.states.get("input_boolean.gate").state == "off"
    events = await _fire_trigger(hass)

    assert hass.states.get("input_boolean.gate").state == "on"
    assert len(calls) == 2
    assert calls[0]["task_name"] == TASK_FACE_SELECT
    assert calls[1]["task_name"] == TASK_FACE_MATCH
    assert events and events[0]["matched"] is True
    assert events[0]["name"] == "Alice"


async def test_identify_blueprint_low_percentage_runs_unmatched(
    hass: HomeAssistant,
) -> None:
    """A confident-looking match below min_confidence is not a match."""
    await _setup_identify_stack(
        hass,
        {
            TASK_FACE_SELECT: SELECT_FACE,
            TASK_FACE_MATCH: {
                "name": "Alice",
                "confidence": 40,
                "reason": "blurry photo",
            },
        },
    )
    hass.states.async_set("input_boolean.gate", "on")

    await _fire_trigger(hass)

    assert hass.states.get("input_boolean.gate").state == "off"


async def test_identify_blueprint_no_face_runs_unmatched(
    hass: HomeAssistant,
) -> None:
    """No visible face: single AI call, unmatched actions run."""
    calls = await _setup_identify_stack(
        hass,
        {
            TASK_FACE_SELECT: {
                "face_visible": False,
                "frame_index": 0,
                "bbox_x": 0,
                "bbox_y": 0,
                "bbox_w": 0,
                "bbox_h": 0,
                "reason": "nobody at the door",
            },
        },
    )
    hass.states.async_set("input_boolean.gate", "on")

    await _fire_trigger(hass)

    assert hass.states.get("input_boolean.gate").state == "off"
    assert len(calls) == 1


async def test_identify_blueprint_fails_closed_without_faces(
    hass: HomeAssistant,
) -> None:
    """With no enrolled faces the service errors and gate stays locked."""
    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"gate": {}}}
    )
    await hass.async_block_till_done()

    dest_dir = Path(hass.config.path("blueprints", "automation", "watchmen"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(IDENTIFY_BLUEPRINT_SRC, dest_dir / "identify_person.yaml")
    assert await async_setup_component(hass, "automation", IDENTIFY_AUTOMATION)
    await hass.async_block_till_done()

    hass.states.async_set("input_boolean.gate", "on")
    await _fire_trigger(hass)

    # Service raised (no faces enrolled) → continue_on_error → unmatched ran.
    assert hass.states.get("input_boolean.gate").state == "off"
