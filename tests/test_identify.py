"""Face identification pipeline tests: buffer, crop/zoom, two-stage LLM."""

from __future__ import annotations

import io
import pathlib
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from PIL import Image as PILImage
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.watchmen.const import (
    CONF_FACE_BUFFER_INTERVAL,
    DOMAIN,
    EVENT_PERSON_IDENTIFIED,
    FACE_CROP_MIN_SIDE,
)
from custom_components.watchmen.faces import FaceStore
from custom_components.watchmen.identify import (
    TASK_FACE_MATCH,
    TASK_FACE_SELECT,
    async_collect_candidates,
    async_identify_person,
    crop_and_zoom_face,
)

from .helpers import (
    CAMERA_ENTITY,
    make_event_entry,
    make_jpeg,
    register_event_camera,
    register_fake_ai_task,
)

SELECTION_OK = {
    "face_visible": True,
    "frame_index": 0,
    "bbox_x": 0.25,
    "bbox_y": 0.2,
    "bbox_w": 0.4,
    "bbox_h": 0.5,
    "reason": "person clearly visible",
}
MATCH_OK = {
    "name": "Alice",
    "confidence": 87,
    "reason": "facial features match enrolled Alice",
}


async def _setup(
    hass: HomeAssistant, options: dict[str, Any] | None = None
):
    """Set up Watchmen with a camera; return (entry, coordinator)."""
    entry = make_event_entry(options or {})
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return entry, coordinator


async def _enroll(hass: HomeAssistant, *names: str) -> None:
    """Enroll one photo per given name."""
    store = FaceStore(hass)
    for index, name in enumerate(names):
        await store.async_add(name, make_jpeg((200 - index * 60, 150, 100)))


async def test_identify_requires_enrolled_faces(hass: HomeAssistant) -> None:
    """Without a gallery the service fails closed."""
    _, coordinator = await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await async_identify_person(
            hass, coordinator, CAMERA_ENTITY, "bell", frames_forward=0
        )


async def test_identify_returns_name_and_percentage(hass: HomeAssistant) -> None:
    """Buffered frames + two-stage LLM give name, percentage and match."""
    _, coordinator = await _setup(
        hass, {CONF_FACE_BUFFER_INTERVAL: 1.0}
    )
    await _enroll(hass, "Alice")
    coordinator.buffers[CAMERA_ENTITY].add(make_jpeg((10, 10, 10)))
    coordinator.buffers[CAMERA_ENTITY].add(make_jpeg((20, 20, 20)))
    coordinator.buffers[CAMERA_ENTITY].add(make_jpeg((30, 30, 30)))

    calls = register_fake_ai_task(
        hass,
        {
            TASK_FACE_SELECT: SELECTION_OK,
            TASK_FACE_MATCH: MATCH_OK,
        },
    )
    events: list[dict[str, Any]] = []
    hass.bus.async_listen(
        EVENT_PERSON_IDENTIFIED, lambda event: events.append(dict(event.data))
    )

    result = await async_identify_person(
        hass,
        coordinator,
        CAMERA_ENTITY,
        "doorbell pressed",
        frames_back=2,
        frames_forward=0,
    )

    assert result["name"] == "Alice"
    assert result["confidence"] == 87.0
    assert result["matched"] is True
    assert result["face_visible"] is True
    assert result["candidates"] == 2  # frames_back=2 of the 3 buffered
    assert result["context"] == "doorbell pressed"

    # Stage 1 saw both candidate frames; stage 2 saw gallery + crop.
    assert len(calls) == 2
    assert calls[0]["task_name"] == TASK_FACE_SELECT
    assert len(calls[0]["attachments"]) == 2
    assert calls[1]["task_name"] == TASK_FACE_MATCH
    assert len(calls[1]["attachments"]) == 2  # 1 enrolled photo + 1 crop

    await hass.async_block_till_done()
    assert events and events[0]["matched"] is True
    assert events[0]["name"] == "Alice"

    # Consumed buffer so a stale trigger cannot replay the same frames.
    assert coordinator.buffers[CAMERA_ENTITY].frames() == []


async def test_identify_no_face_skips_match(hass: HomeAssistant) -> None:
    """No visible face: one LLM call only, fail-closed result, event fires."""
    _, coordinator = await _setup(hass)
    await _enroll(hass, "Alice")
    calls = register_fake_ai_task(
        hass,
        {
            TASK_FACE_SELECT: {
                "face_visible": False,
                "frame_index": 0,
                "bbox_x": 0,
                "bbox_y": 0,
                "bbox_w": 0,
                "bbox_h": 0,
                "reason": "empty driveway",
            },
        },
    )
    events: list[dict[str, Any]] = []
    hass.bus.async_listen(
        EVENT_PERSON_IDENTIFIED, lambda event: events.append(dict(event.data))
    )

    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(
            return_value=Image(
                content_type="image/jpeg", content=make_jpeg((5, 5, 5))
            )
        ),
    ):
        result = await async_identify_person(
            hass, coordinator, CAMERA_ENTITY, "bell", frames_back=1, frames_forward=0
        )

    assert result["face_visible"] is False
    assert result["name"] is None
    assert result["matched"] is False
    assert result["confidence"] == 0.0
    assert len(calls) == 1  # match call never happened
    await hass.async_block_till_done()
    assert events[0]["face_visible"] is False


async def test_identify_below_threshold_not_matched(hass: HomeAssistant) -> None:
    """A recognized name under the percentage threshold is not a match."""
    _, coordinator = await _setup(hass)
    await _enroll(hass, "Alice")
    register_fake_ai_task(
        hass,
        {
            TASK_FACE_SELECT: SELECTION_OK,
            TASK_FACE_MATCH: {
                "name": "Alice",
                "confidence": 40,
                "reason": "blurry image",
            },
        },
    )

    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(
            return_value=Image(
                content_type="image/jpeg", content=make_jpeg((4, 5, 6))
            )
        ),
    ):
        result = await async_identify_person(
            hass, coordinator, CAMERA_ENTITY, "bell", frames_back=1, frames_forward=0
        )

    assert result["name"] == "Alice"
    assert result["confidence"] == 40.0
    assert result["matched"] is False
    assert "below threshold" in result["reason"]


async def test_identify_unenrolled_name_not_matched(hass: HomeAssistant) -> None:
    """A hallucinated name is discarded even at high confidence."""
    _, coordinator = await _setup(hass)
    await _enroll(hass, "Alice")
    register_fake_ai_task(
        hass,
        {
            TASK_FACE_SELECT: SELECTION_OK,
            TASK_FACE_MATCH: {
                "name": "Mallory",
                "confidence": 95,
                "reason": "looks like Mallory",
            },
        },
    )

    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(
            return_value=Image(
                content_type="image/jpeg", content=make_jpeg((4, 5, 6))
            )
        ),
    ):
        result = await async_identify_person(
            hass, coordinator, CAMERA_ENTITY, "bell", frames_back=1, frames_forward=0
        )

    assert result["name"] is None
    assert result["matched"] is False


async def test_identify_llm_error_fails_closed(hass: HomeAssistant) -> None:
    """Provider errors propagate and temporary captures are cleaned up."""
    _, coordinator = await _setup(hass)
    await _enroll(hass, "Alice")

    async def _boom(call):
        raise HomeAssistantError("provider down")

    hass.services.async_register(
        "ai_task", "generate_data", _boom, supports_response=SupportsResponse.ONLY
    )

    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(
            return_value=Image(
                content_type="image/jpeg", content=make_jpeg((8, 8, 8))
            )
        ),
    ), pytest.raises(HomeAssistantError):
        await async_identify_person(
            hass, coordinator, CAMERA_ENTITY, "bell", frames_back=1, frames_forward=0
        )

    captures = pathlib.Path(hass.config.path("media", "watchmen", "captures"))
    files = list(captures.glob("*.jpg")) if captures.exists() else []
    assert files == []


async def test_identify_without_frames_fails(hass: HomeAssistant) -> None:
    """If nothing can be captured the service raises (fail closed)."""
    _, coordinator = await _setup(
        hass, {CONF_FACE_BUFFER_INTERVAL: 0.01}
    )
    await _enroll(hass, "Alice")
    register_fake_ai_task(hass, {})

    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(side_effect=RuntimeError("camera offline")),
    ), pytest.raises(HomeAssistantError):
        await async_identify_person(
            hass,
            coordinator,
            CAMERA_ENTITY,
            "bell",
            frames_back=1,
            frames_forward=1,
        )


async def test_collect_candidates_back_and_forward(hass: HomeAssistant) -> None:
    """Back frames come from the buffer, forward frames from live fetches."""
    _, coordinator = await _setup(
        hass, {CONF_FACE_BUFFER_INTERVAL: 0.01}
    )
    for index in range(5):
        coordinator.buffers[CAMERA_ENTITY].add(make_jpeg((index, index, index)))

    forward_1 = make_jpeg((100, 100, 100))
    forward_2 = make_jpeg((200, 200, 200))
    with patch(
        "custom_components.watchmen.identify.async_get_image",
        AsyncMock(
            side_effect=[
                Image(content_type="image/jpeg", content=forward_1),
                Image(content_type="image/jpeg", content=forward_2),
            ]
        ),
    ) as mock_fetch:
        candidates = await async_collect_candidates(
            hass, coordinator, CAMERA_ENTITY, frames_back=2, frames_forward=2
        )

    assert len(candidates) == 4
    # Back = last 2 buffered frames (index 3, 4), then the 2 live frames.
    assert candidates[2] == forward_1
    assert candidates[3] == forward_2
    assert mock_fetch.await_count == 2
    assert coordinator.buffers[CAMERA_ENTITY].frames() == []


def test_crop_and_zoom_face() -> None:
    """The crop is padded, clamped and zoomed to at least FACE_CROP_MIN_SIDE."""
    frame = make_jpeg((255, 255, 255))
    crop = crop_and_zoom_face(frame, (0.25, 0.25, 0.5, 0.5))
    assert crop is not None
    with PILImage.open(io.BytesIO(crop)) as img:
        assert min(img.size) >= FACE_CROP_MIN_SIDE

    # Degenerate and invalid boxes are rejected.
    assert crop_and_zoom_face(frame, (0.5, 0.5, 0.0, 0.3)) is None
    assert crop_and_zoom_face(frame, (-0.1, 0.0, 0.5, 0.5)) is None
    assert crop_and_zoom_face(b"not an image", (0.1, 0.1, 0.3, 0.3)) is None


async def test_buffer_poller_fills_frame_buffer(hass: HomeAssistant) -> None:
    """The time-interval poller snapshots event cameras into the buffer."""
    _, coordinator = await _setup(
        hass, {CONF_FACE_BUFFER_INTERVAL: 0.5}
    )
    assert CAMERA_ENTITY in coordinator.buffers
    assert coordinator.buffers[CAMERA_ENTITY].frames() == []

    frame = Image(content_type="image/jpeg", content=make_jpeg((77, 77, 77)))
    with patch(
        "custom_components.watchmen.coordinator.async_get_image",
        AsyncMock(return_value=frame),
    ) as mock_fetch:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
        await hass.async_block_till_done()

    assert mock_fetch.await_count == 1
    assert coordinator.buffers[CAMERA_ENTITY].frames() == [frame.content]
