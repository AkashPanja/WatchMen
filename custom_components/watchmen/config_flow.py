"""Config and options flow for Watchmen."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAMERAS,
    CONF_CONFIDENCE_THRESHOLD,
    CONF_COOLDOWN,
    CONF_DIFF_THRESHOLD,
    CONF_FRAME_INTERVAL,
    CONF_MODE,
    CONF_MOTION_OFF_DELAY,
    CONF_RESIDENTS,
    DEFAULT_OPTIONS,
    DOMAIN,
)

MODE_OPTIONS = [
    {"value": "auto", "label": "Automatic (events or frame analysis)"},
    {"value": "event", "label": "Camera events only"},
    {"value": "frame_diff", "label": "Frame analysis only"},
]


def _cameras_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for camera + mode selection."""
    return vol.Schema(
        {
            vol.Required(
                CONF_CAMERAS,
                default=defaults.get(CONF_CAMERAS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Required(
                CONF_MODE, default=defaults.get(CONF_MODE, DEFAULT_OPTIONS[CONF_MODE])
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MODE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class WatchmenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Watchmen config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Show the camera picker."""
        if user_input is not None:
            cameras = sorted(user_input[CONF_CAMERAS])
            await self.async_set_unique_id(",".join(cameras))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="Watchmen",
                data={CONF_CAMERAS: cameras, CONF_MODE: user_input[CONF_MODE]},
            )

        return self.async_show_form(step_id="user", data_schema=_cameras_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return WatchmenOptionsFlow()


class WatchmenOptionsFlow(OptionsFlow):
    """Handle Watchmen options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Show the options form."""
        entry = self.config_entry
        if user_input is not None:
            user_input[CONF_CAMERAS] = sorted(user_input[CONF_CAMERAS])
            user_input[CONF_AI_TASK_ENTITY] = (
                user_input.get(CONF_AI_TASK_ENTITY) or ""
            )
            return self.async_create_entry(title="", data=user_input)

        defaults: dict[str, Any] = {
            key: entry.options.get(key, value)
            for key, value in DEFAULT_OPTIONS.items()
        }
        defaults[CONF_MODE] = entry.options.get(
            CONF_MODE, entry.data.get(CONF_MODE, DEFAULT_OPTIONS[CONF_MODE])
        )

        schema_dict: dict[Any, Any] = {
            vol.Required(
                CONF_CAMERAS, default=entry.data.get(CONF_CAMERAS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="camera", multiple=True)
            ),
            vol.Required(
                CONF_MODE, default=defaults[CONF_MODE]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MODE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_FRAME_INTERVAL, default=defaults[CONF_FRAME_INTERVAL]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.5,
                    max=60,
                    step=0.5,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_DIFF_THRESHOLD, default=defaults[CONF_DIFF_THRESHOLD]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.005,
                    max=0.5,
                    step=0.005,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MOTION_OFF_DELAY, default=defaults[CONF_MOTION_OFF_DELAY]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=600,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_RESIDENTS, default=defaults[CONF_RESIDENTS]
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=False)),
            vol.Required(
                CONF_CONFIDENCE_THRESHOLD,
                default=defaults[CONF_CONFIDENCE_THRESHOLD],
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.1,
                    max=1,
                    step=0.05,
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_COOLDOWN, default=defaults[CONF_COOLDOWN]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=600,
                    step=5,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }

        ai_task_default = defaults.get(CONF_AI_TASK_ENTITY) or ""
        ai_task_options = _ai_task_options(self.hass, ai_task_default)
        schema_dict[
            vol.Required(CONF_AI_TASK_ENTITY, default=ai_task_default)
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=ai_task_options,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict)
        )


def _ai_task_options(hass: HomeAssistant, current: str) -> list[dict[str, str]]:
    """Build AI task entity options: auto plus every known AI task entity."""
    options: list[dict[str, str]] = [
        {"value": "", "label": "Auto (preferred AI task entity)"}
    ]
    registry = er.async_get(hass)
    entity_ids = sorted(
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "ai_task"
    )
    for entity_id in entity_ids:
        options.append({"value": entity_id, "label": entity_id})
    if current and not any(opt["value"] == current for opt in options):
        options.append({"value": current, "label": f"{current} (missing)"})
    return options
