"""Watchmen: motion detection, LLM access review and face identification."""

from __future__ import annotations

import base64
import logging

import voluptuous as vol
from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CAMERA,
    CONF_CONTEXT,
    CONF_FRAMES_BACK,
    CONF_FRAMES_FORWARD,
    CONF_IMAGE,
    CONF_NAME,
    DOMAIN,
    IMAGE_TIMEOUT,
    SERVICE_ADD_FACE,
    SERVICE_IDENTIFY_PERSON,
    SERVICE_LIST_FACES,
    SERVICE_REMOVE_FACE,
    SERVICE_REQUEST_ACCESS,
)
from .coordinator import WatchmenCoordinator
from .faces import FaceStore
from .identify import async_identify_person
from .llm import async_request_access_review

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

REQUEST_ACCESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CAMERA): cv.entity_id,
        vol.Optional(CONF_CONTEXT, default=""): cv.string,
    }
)

ADD_FACE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): vol.All(
            cv.string, vol.Strip, vol.Length(min=1, max=64)
        ),
        vol.Optional(CONF_CAMERA): cv.entity_id,
        vol.Optional(CONF_IMAGE): cv.string,
    }
)

REMOVE_FACE_SCHEMA = vol.Schema({vol.Required(CONF_NAME): cv.string})

IDENTIFY_PERSON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CAMERA): cv.entity_id,
        vol.Optional(CONF_CONTEXT, default=""): cv.string,
        vol.Optional(CONF_FRAMES_BACK): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=20)
        ),
        vol.Optional(CONF_FRAMES_FORWARD): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10)
        ),
    }
)

_UNLOAD_LOCK = "__unload"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Watchmen domain (registers shared services)."""
    hass.data.setdefault(DOMAIN, {})

    async def _handle_request_access(call: ServiceCall) -> ServiceResponse:
        """Handle the watchmen.request_access service call."""
        return await _async_handle_request_access(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_ACCESS,
        _handle_request_access,
        schema=REQUEST_ACCESS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _handle_add_face(call: ServiceCall) -> ServiceResponse:
        """Handle watchmen.add_face: enroll one photo for a person."""
        return await _async_handle_add_face(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_FACE,
        _handle_add_face,
        schema=ADD_FACE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_remove_face(call: ServiceCall) -> ServiceResponse:
        """Handle watchmen.remove_face: forget a person."""
        name = call.data[CONF_NAME]
        removed = await FaceStore(hass).async_remove(name)
        return {"name": name, "removed": removed}

    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_FACE,
        _handle_remove_face,
        schema=REMOVE_FACE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _handle_list_faces(call: ServiceCall) -> ServiceResponse:
        """Handle watchmen.list_faces: list enrolled people and photo counts."""
        faces = await FaceStore(hass).async_list()
        return {"faces": {name: len(info["photos"]) for name, info in faces.items()}}

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_FACES,
        _handle_list_faces,
        supports_response=SupportsResponse.ONLY,
    )

    async def _handle_identify_person(call: ServiceCall) -> ServiceResponse:
        """Handle watchmen.identify_person: name + percentage for the person."""
        return await _async_handle_identify_person(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_IDENTIFY_PERSON,
        _handle_identify_person,
        schema=IDENTIFY_PERSON_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Watchmen from a config entry."""
    coordinator = WatchmenCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: WatchmenCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_find_coordinator(
    hass: HomeAssistant, camera_entity_id: str
) -> WatchmenCoordinator | None:
    """Find the coordinator that watches the given camera."""
    for coordinator in list(hass.data.get(DOMAIN, {}).values()):
        if not isinstance(coordinator, WatchmenCoordinator):
            continue
        if coordinator.get_runtime(camera_entity_id) is not None:
            return coordinator
    return None


async def _async_handle_request_access(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Handle the watchmen.request_access service call."""
    camera: str = call.data[CONF_CAMERA]
    context: str = call.data.get(CONF_CONTEXT, "")

    coordinator = _async_find_coordinator(hass=hass, camera_entity_id=camera)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="camera_not_configured",
            translation_placeholders={"camera": camera},
        )

    try:
        return await async_request_access_review(
            hass=hass,
            coordinator=coordinator,
            camera_entity_id=camera,
            context=context,
        )
    except HomeAssistantError:
        raise
    except Exception as err:
        _LOGGER.exception("Access review failed for %s", camera)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="review_failed",
            translation_placeholders={"error": str(err)},
        ) from err


async def _async_handle_add_face(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Enroll one photo: snapshot from a camera or a base64 payload."""
    name: str = call.data[CONF_NAME]
    camera: str | None = call.data.get(CONF_CAMERA)
    image_b64: str | None = call.data.get(CONF_IMAGE)

    if bool(camera) == bool(image_b64):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="add_face_source",
        )

    if camera:
        try:
            image = await async_get_image(hass, camera, timeout=IMAGE_TIMEOUT)
        except Exception as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_image",
                translation_placeholders={"error": str(err)},
            ) from err
        data = image.content
    else:
        payload = image_b64 or ""
        if payload.startswith("data:"):
            payload = payload.split(",", 1)[-1]
        try:
            data = base64.b64decode(payload, validate=True)
        except (ValueError, TypeError) as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_image",
                translation_placeholders={"error": str(err)},
            ) from err

    if not data:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_image",
            translation_placeholders={"error": "empty image"},
        )

    photos = await FaceStore(hass).async_add(name, data)
    _LOGGER.info("Enrolled a photo for %s (%d photos total)", name, photos)
    return {"name": name, "photos": photos}


async def _async_handle_identify_person(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Identify the person in frames around a trigger (name + percentage)."""
    camera: str = call.data[CONF_CAMERA]
    context: str = call.data.get(CONF_CONTEXT, "")

    coordinator = _async_find_coordinator(hass=hass, camera_entity_id=camera)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="camera_not_configured",
            translation_placeholders={"camera": camera},
        )

    try:
        return await async_identify_person(
            hass,
            coordinator,
            camera,
            context,
            frames_back=call.data.get(CONF_FRAMES_BACK),
            frames_forward=call.data.get(CONF_FRAMES_FORWARD),
        )
    except HomeAssistantError:
        raise
    except Exception as err:
        _LOGGER.exception("Person identification failed for %s", camera)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="identify_failed",
            translation_placeholders={"error": str(err)},
        ) from err
