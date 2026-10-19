"""Direct LLM provider APIs for Watchmen.

Used when the configured provider is not Home Assistant's AI Task:
OpenAI, Claude (Anthropic), Groq and OpenRouter. All calls are structured:
the prompt demands a single JSON object and responses are parsed back into
the same dict shape the AI Task path returns. API keys are never logged.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    IMAGE_TIMEOUT,
    LLM_BASE_URLS,
    LLM_DEFAULT_MODELS,
    LLM_PROVIDER_CLAUDE,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60
ANTHROPIC_VERSION = "2023-06-01"

_CAMERA_PREFIX = "media-source://camera/"
_LOCAL_PREFIX = "media-source://media_source/local/"


def structure_spec(structure: dict[str, dict[str, Any]]) -> str:
    """Render the expected JSON structure as prompt instructions."""
    lines = []
    for key, spec in structure.items():
        selector = spec.get("selector", {})
        kind = next(iter(selector), "text")
        description = spec.get("description", "")
        lines.append(f'- "{key}" ({kind}): {description}')
    return "\n".join(lines)


def build_prompt(instructions: str, structure: dict[str, dict[str, Any]]) -> str:
    """Build the full text prompt ending with a strict JSON rule."""
    return (
        f"{instructions}\n"
        "Respond with ONLY one JSON object. No markdown, no code fences, no "
        "commentary before or after. Use exactly these keys:\n"
        f"{structure_spec(structure)}"
    )


def extract_json(text: str) -> dict[str, Any]:
    """Parse a model reply into a dict, tolerating stray markdown/prose.

    Raises ValueError when no JSON object can be found.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        cleaned = cleaned.removeprefix("json")
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except ValueError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(cleaned[start : end + 1])
            if isinstance(result, dict):
                return result
        except ValueError:
            pass
    raise ValueError("no JSON object in reply")


async def _resolve_attachments(
    hass: HomeAssistant, attachments: list[dict[str, str]]
) -> list[tuple[str, str]]:
    """Resolve attachment URIs to (mime_type, base64_data) pairs."""
    parts: list[tuple[str, str]] = []
    for attachment in attachments:
        content_id = attachment.get("media_content_id", "")
        if content_id.startswith(_CAMERA_PREFIX):
            entity_id = content_id.removeprefix(_CAMERA_PREFIX)
            image = await async_get_image(hass, entity_id, timeout=IMAGE_TIMEOUT)
            data = image.content
            mime = image.content_type or "image/jpeg"
        elif content_id.startswith(_LOCAL_PREFIX):
            relative = content_id.removeprefix(_LOCAL_PREFIX)
            path = Path(hass.config.path("media", relative))

            def _read(file_path: Path = path) -> bytes:
                return file_path.read_bytes()

            try:
                data = await hass.async_add_executor_job(_read)
            except OSError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="attachment_unsupported",
                    translation_placeholders={"attachment": content_id},
                ) from err
            mime = "image/png" if relative.lower().endswith(".png") else "image/jpeg"
        else:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="attachment_unsupported",
                translation_placeholders={"attachment": content_id},
            )
        parts.append((mime, base64.b64encode(data).decode()))
    return parts


def _error(provider: str, error: str) -> HomeAssistantError:
    """Build a fail-closed provider error (never includes the API key)."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="llm_request_failed",
        translation_placeholders={"provider": provider, "error": error},
    )


async def _post_json(
    hass: HomeAssistant,
    provider: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST JSON and return the parsed response body."""
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            url,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            raw = await response.text()
            if response.status != 200:
                raise _error(provider, f"HTTP {response.status}: {raw[:300]}")
            try:
                payload = json.loads(raw)
            except ValueError as err:
                raise _error(provider, "invalid JSON in response") from err
    except HomeAssistantError:
        raise
    except aiohttp.ClientError as err:
        raise _error(provider, str(err)) from err
    except TimeoutError as err:
        raise _error(provider, "request timed out") from err
    if not isinstance(payload, dict):
        raise _error(provider, "unexpected response format")
    return payload


async def _call_openai_compatible(
    hass: HomeAssistant,
    *,
    provider: str,
    api_key: str,
    model: str,
    base_url: str,
    prompt: str,
    parts: list[tuple[str, str]],
) -> dict[str, Any]:
    """Call an OpenAI-style /chat/completions endpoint."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for mime, data in parts:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        headers["X-Title"] = "Watchmen"
    payload = await _post_json(
        hass,
        provider,
        f"{base_url.rstrip('/')}/chat/completions",
        headers,
        {"model": model, "messages": [{"role": "user", "content": content}]},
    )
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as err:
        raise _error(provider, "missing choices in response") from err
    if not isinstance(text, str) or not text.strip():
        raise _error(provider, "empty model reply")
    try:
        return extract_json(text)
    except ValueError as err:
        raise _error(provider, str(err)) from err


async def _call_claude(
    hass: HomeAssistant,
    *,
    api_key: str,
    model: str,
    base_url: str,
    prompt: str,
    parts: list[tuple[str, str]],
) -> dict[str, Any]:
    """Call Anthropic's /messages endpoint."""
    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": data},
        }
        for mime, data in parts
    ]
    content.append({"type": "text", "text": "Return the JSON object now."})
    payload = await _post_json(
        hass,
        LLM_PROVIDER_CLAUDE,
        f"{base_url.rstrip('/')}/messages",
        {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        {
            "model": model,
            "max_tokens": 1024,
            "system": prompt,
            "messages": [{"role": "user", "content": content}],
        },
    )
    try:
        text = "".join(
            block.get("text", "")
            for block in payload["content"]
            if block.get("type") == "text"
        )
    except (KeyError, TypeError) as err:
        raise _error(LLM_PROVIDER_CLAUDE, "missing content in response") from err
    if not text.strip():
        raise _error(LLM_PROVIDER_CLAUDE, "empty model reply")
    try:
        return extract_json(text)
    except ValueError as err:
        raise _error(LLM_PROVIDER_CLAUDE, str(err)) from err


async def async_call_provider(
    hass: HomeAssistant,
    *,
    provider: str,
    api_key: str,
    model: str,
    base_url: str,
    instructions: str,
    structure: dict[str, dict[str, Any]],
    attachments: list[dict[str, str]],
) -> dict[str, Any]:
    """Run one structured call against a direct provider API."""
    if provider not in LLM_BASE_URLS:
        raise _error(provider, "unsupported provider")
    if not api_key:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="api_key_missing",
            translation_placeholders={"provider": provider},
        )

    resolved_model = model or LLM_DEFAULT_MODELS[provider]
    resolved_base = base_url or LLM_BASE_URLS[provider]
    prompt = build_prompt(instructions, structure)
    parts = await _resolve_attachments(hass, attachments)

    _LOGGER.debug(
        "LLM call via %s model=%s attachments=%d",
        provider,
        resolved_model,
        len(parts),
    )
    if provider == LLM_PROVIDER_CLAUDE:
        return await _call_claude(
            hass,
            api_key=api_key,
            model=resolved_model,
            base_url=resolved_base,
            prompt=prompt,
            parts=parts,
        )
    return await _call_openai_compatible(
        hass,
        provider=provider,
        api_key=api_key,
        model=resolved_model,
        base_url=resolved_base,
        prompt=prompt,
        parts=parts,
    )
