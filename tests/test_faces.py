"""Face enrollment service tests."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.watchmen.const import DOMAIN

from .helpers import CAMERA_ENTITY, make_event_entry, make_jpeg, register_event_camera


async def _setup(hass: HomeAssistant) -> None:
    """Set up the Watchmen domain with one camera."""
    entry = make_event_entry()
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call_add(hass: HomeAssistant, data: dict) -> dict:
    """Call watchmen.add_face with a response."""
    return await hass.services.async_call(
        DOMAIN, "add_face", data, blocking=True, return_response=True
    )


async def test_add_list_remove_face_base64(hass: HomeAssistant) -> None:
    """Enroll multiple photos per person, list and remove them."""
    await _setup(hass)
    payload = base64.b64encode(make_jpeg((120, 90, 60))).decode()

    result = await _call_add(hass, {"name": "Alice", "image": payload})
    assert result == {"name": "Alice", "photos": 1}

    result = await _call_add(hass, {"name": "Alice", "image": payload})
    assert result == {"name": "Alice", "photos": 2}

    listing = await hass.services.async_call(
        DOMAIN, "list_faces", {}, blocking=True, return_response=True
    )
    assert listing == {"faces": {"Alice": 2}}

    removed = await hass.services.async_call(
        DOMAIN,
        "remove_face",
        {"name": "Alice"},
        blocking=True,
        return_response=True,
    )
    assert removed == {"name": "Alice", "removed": True}

    listing = await hass.services.async_call(
        DOMAIN, "list_faces", {}, blocking=True, return_response=True
    )
    assert listing == {"faces": {}}

    removed = await hass.services.async_call(
        DOMAIN,
        "remove_face",
        {"name": "Alice"},
        blocking=True,
        return_response=True,
    )
    assert removed == {"name": "Alice", "removed": False}


async def test_add_face_from_camera(hass: HomeAssistant) -> None:
    """A camera snapshot is enrolled when no image payload is given."""
    await _setup(hass)
    image = Image(content_type="image/jpeg", content=make_jpeg((10, 20, 30)))

    with patch(
        "custom_components.watchmen.async_get_image",
        AsyncMock(return_value=image),
    ) as mock_fetch:
        result = await _call_add(
            hass, {"name": "Bob", "camera": CAMERA_ENTITY}
        )
    assert result == {"name": "Bob", "photos": 1}
    assert mock_fetch.await_count == 1


async def test_add_face_requires_exactly_one_source(hass: HomeAssistant) -> None:
    """Providing both sources (or none) is rejected."""
    await _setup(hass)
    payload = base64.b64encode(make_jpeg((1, 2, 3))).decode()

    with pytest.raises(ServiceValidationError):
        await _call_add(
            hass,
            {"name": "X", "camera": CAMERA_ENTITY, "image": payload},
        )
    with pytest.raises(ServiceValidationError):
        await _call_add(hass, {"name": "X"})


async def test_add_face_rejects_invalid_payloads(hass: HomeAssistant) -> None:
    """Bad base64 and non-image payloads fail closed."""
    await _setup(hass)

    with pytest.raises(ServiceValidationError):
        await _call_add(hass, {"name": "X", "image": "!!!not-base64!!!"})

    not_an_image = base64.b64encode(b"hello world, not an image").decode()
    with pytest.raises(ServiceValidationError):
        await _call_add(hass, {"name": "X", "image": not_an_image})
