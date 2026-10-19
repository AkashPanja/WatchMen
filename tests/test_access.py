"""Access review service tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.watchmen.const import (
    DOMAIN,
    EVENT_GATE_APPROVED,
    EVENT_GATE_DENIED,
    SERVICE_REQUEST_ACCESS,
)

from .helpers import (
    CAMERA_ENTITY,
    MOTION_SOURCE,
    decision_unique_id,
    make_event_entry,
    motion_unique_id,
    register_event_camera,
)


async def _setup_event_entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up an event-mode Watchmen entry."""
    entry = make_event_entry(options)
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _register_fake_ai(
    hass: HomeAssistant, calls: list[dict[str, Any]], response: Any
) -> None:
    """Register a fake ai_task.generate_data service."""

    async def _fake(call):
        calls.append(dict(call.data))
        if isinstance(response, Exception):
            raise response
        return response

    hass.services.async_register(
        "ai_task",
        "generate_data",
        _fake,
        supports_response=SupportsResponse.ONLY,
    )


async def _review(hass: HomeAssistant, **data: Any) -> dict[str, Any]:
    """Call watchmen.request_access and return the decision."""
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_REQUEST_ACCESS,
        {"camera": CAMERA_ENTITY, **data},
        blocking=True,
        return_response=True,
    )


async def test_request_access_approves(hass: HomeAssistant) -> None:
    """A confident approval is returned, recorded and fired as event."""
    await _setup_event_entry(hass, {"residents": "Alice, Bob"})
    calls: list[dict[str, Any]] = []
    _register_fake_ai(
        hass,
        calls,
        {"approved": True, "person": "Alice", "confidence": 0.93, "reason": "ok"},
    )

    events: list[Any] = []
    hass.bus.async_listen(EVENT_GATE_APPROVED, events.append)

    response = await _review(hass, context="doorbell pressed")
    assert response["approved"] is True
    assert response["person"] == "Alice"
    assert response["confidence"] == 0.93
    assert response["context"] == "doorbell pressed"

    # The AI call received the snapshot attachment and structured schema.
    assert calls[0]["attachments"][0]["media_content_id"].startswith(
        "media-source://camera/"
    )
    assert "approved" in calls[0]["structure"]
    assert "Alice" in calls[0]["instructions"]

    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["person"] == "Alice"


async def test_low_confidence_is_denied(hass: HomeAssistant) -> None:
    """Approvals below the confidence threshold become denials."""
    await _setup_event_entry(hass)
    calls: list[dict[str, Any]] = []
    _register_fake_ai(
        hass,
        calls,
        {"approved": True, "person": "Alice", "confidence": 0.5, "reason": "unsure"},
    )

    denials: list[Any] = []
    hass.bus.async_listen(EVENT_GATE_DENIED, denials.append)

    response = await _review(hass)
    assert response["approved"] is False
    assert "below threshold" in response["reason"]

    await hass.async_block_till_done()
    assert len(denials) == 1


async def test_string_outputs_normalized(hass: HomeAssistant) -> None:
    """Model outputs like 'yes' / '0.91' are normalized."""
    await _setup_event_entry(hass)
    calls: list[dict[str, Any]] = []
    _register_fake_ai(
        hass,
        calls,
        {"approved": "yes", "person": "Bob", "confidence": "0.91", "reason": "clear"},
    )

    response = await _review(hass)
    assert response["approved"] is True
    assert response["confidence"] == 0.91


async def test_cooldown_blocks_second_review(hass: HomeAssistant) -> None:
    """A second review within the cooldown is denied without an AI call."""
    await _setup_event_entry(hass)
    calls: list[dict[str, Any]] = []
    _register_fake_ai(
        hass,
        calls,
        {"approved": True, "person": "Alice", "confidence": 0.99, "reason": "ok"},
    )

    first = await _review(hass)
    assert first["approved"] is True

    second = await _review(hass)
    assert second["approved"] is False
    assert second["reason"] == "cooldown_active"
    assert len(calls) == 1


async def test_ai_error_fails_closed(hass: HomeAssistant) -> None:
    """Provider errors raise so automations stop (fail closed)."""
    await _setup_event_entry(hass)
    calls: list[dict[str, Any]] = []
    _register_fake_ai(hass, calls, HomeAssistantError("provider down"))

    with pytest.raises(HomeAssistantError):
        await _review(hass)


async def test_missing_ai_task_raises(hass: HomeAssistant) -> None:
    """Without any AI task service the review fails closed."""
    await _setup_event_entry(hass)

    with pytest.raises(HomeAssistantError) as err:
        await _review(hass)
    assert "ai task" in str(err.value).lower()


async def test_unconfigured_camera_raises(hass: HomeAssistant) -> None:
    """Requesting a review for an unknown camera raises a validation error."""
    await _setup_event_entry(hass)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REQUEST_ACCESS,
            {"camera": "camera.not_watched"},
            blocking=True,
            return_response=True,
        )


async def test_decision_sensor_updated(hass: HomeAssistant) -> None:
    """The decision sensor reflects the latest review."""
    entry = await _setup_event_entry(hass)
    calls: list[dict[str, Any]] = []
    _register_fake_ai(
        hass,
        calls,
        {"approved": True, "person": "Bob", "confidence": 0.88, "reason": "ok"},
    )

    await _review(hass, context="gate")
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, decision_unique_id(entry)
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.state == "approved"
    assert state.attributes["person"] == "Bob"
    assert state.attributes["confidence"] == 0.88
    assert state.attributes["context"] == "gate"


async def test_motion_sensor_mirrors_source(hass: HomeAssistant) -> None:
    """Event mode exposes a motion entity mirroring the source sensor."""
    entry = await _setup_event_entry(hass)
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, motion_unique_id(entry)
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == STATE_OFF

    hass.states.async_set(
        MOTION_SOURCE,
        STATE_ON,
        {"device_class": "motion", "friendly_name": "Source Motion"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON
