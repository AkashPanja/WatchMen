"""Watchmen: motion detection and LLM access review for cameras."""

from __future__ import annotations

import logging

import voluptuous as vol
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
    DOMAIN,
    SERVICE_REQUEST_ACCESS,
)
from .coordinator import WatchmenCoordinator
from .llm import async_request_access_review

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

REQUEST_ACCESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CAMERA): cv.entity_id,
        vol.Optional(CONF_CONTEXT, default=""): cv.string,
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
