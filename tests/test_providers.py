"""Direct LLM provider tests: OpenAI, Claude, Groq, OpenRouter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.watchmen.const import (
    CONF_LLM_API_KEY,
    CONF_LLM_PROVIDER,
    LLM_BASE_URLS,
    LLM_PROVIDER_GROQ,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_OPENROUTER,
)
from custom_components.watchmen.llm import async_call_llm
from custom_components.watchmen.providers import (
    async_call_provider,
    build_prompt,
    extract_json,
)

from .helpers import make_event_entry, make_jpeg, register_event_camera

STRUCTURE = {
    "ok": {
        "selector": {"boolean": {}},
        "description": "Whether it worked.",
        "required": True,
    },
    "score": {
        "selector": {"number": {"min": 0, "max": 100, "step": 1}},
        "description": "A percentage.",
        "required": True,
    },
}


class _FakeResponse:
    """Minimal aiohttp-like response."""

    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSession:
    """Records posts and returns scripted responses."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: Any = None, headers: Any = None, timeout: Any = None):
        self.calls.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._responses.pop(0)


def _local_attachment(hass: HomeAssistant) -> dict[str, str]:
    """Write an image into the media dir and return its attachment dict."""
    relative = "watchmen/captures/test_frame.jpg"
    path = Path(hass.config.path("media", relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_jpeg((42, 42, 42)))
    return {"media_content_id": f"media-source://media_source/local/{relative}"}


async def _call(
    hass: HomeAssistant,
    session: _FakeSession,
    *,
    provider: str,
    api_key: str = "test-key",
) -> dict[str, Any]:
    """Run async_call_provider against a scripted session."""
    with patch(
        "custom_components.watchmen.providers.async_get_clientsession",
        return_value=session,
    ):
        return await async_call_provider(
            hass,
            provider=provider,
            api_key=api_key,
            model="",
            base_url="",
            instructions="Check this frame.",
            structure=STRUCTURE,
            attachments=[_local_attachment(hass)],
        )


async def test_openai_chat_completions(hass: HomeAssistant) -> None:
    """OpenAI: OpenAI-compatible endpoint, bearer auth, JSON parsed."""
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {
                    "choices": [
                        {"message": {"content": '{"ok": true, "score": 88}'}}
                    ]
                },
            )
        ]
    )
    result = await _call(hass, session, provider=LLM_PROVIDER_OPENAI)
    assert result == {"ok": True, "score": 88}

    call = session.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["messages"][0]["content"][0]["type"] == "text"
    image_part = call["json"]["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # Default model applied.
    assert call["json"]["model"] == "gpt-4o-mini"


async def test_groq_and_openrouter_endpoints(hass: HomeAssistant) -> None:
    """Groq and OpenRouter use their own base URLs (OpenAI-style)."""
    for provider in (LLM_PROVIDER_GROQ, LLM_PROVIDER_OPENROUTER):
        session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    {"choices": [{"message": {"content": '{"ok":1,"score":50}'}}]},
                )
            ]
        )
        result = await _call(hass, session, provider=provider)
        assert result == {"ok": 1, "score": 50}
        assert (
            session.calls[0]["url"]
            == f"{LLM_BASE_URLS[provider]}/chat/completions"
        )


async def test_claude_messages_endpoint(hass: HomeAssistant) -> None:
    """Claude: /messages with x-api-key, image blocks, joined text reply."""
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                {
                    "content": [
                        {"type": "text", "text": '{"ok": '},
                        {"type": "text", "text": 'false, "score": 12}'},
                    ]
                },
            )
        ]
    )
    result = await _call(hass, session, provider="claude")
    assert result == {"ok": False, "score": 12}

    call = session.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "test-key"
    content = call["json"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["type"] == "base64"


async def test_api_key_missing_fails_closed(hass: HomeAssistant) -> None:
    """No key configured → error instead of an unauthenticated request."""
    session = _FakeSession([])
    with pytest.raises(HomeAssistantError) as excinfo:
        await _call(hass, session, provider=LLM_PROVIDER_OPENAI, api_key="")
    assert excinfo.value.translation_key == "api_key_missing"
    assert session.calls == []


async def test_http_error_fails_closed(hass: HomeAssistant) -> None:
    """Non-200 responses raise and never leak the key."""
    session = _FakeSession([_FakeResponse(401, {"error": "bad key sk-secret"})])
    with pytest.raises(HomeAssistantError) as excinfo:
        await _call(hass, session, provider=LLM_PROVIDER_OPENAI)
    placeholders = excinfo.value.translation_placeholders or {}
    assert placeholders["error"].startswith("HTTP 401")
    assert "test-key" not in str(placeholders)


async def test_non_json_reply_fails_closed(hass: HomeAssistant) -> None:
    """A reply without a JSON object is an error."""
    session = _FakeSession(
        [_FakeResponse(200, {"choices": [{"message": {"content": "no json here"}}]})]
    )
    with pytest.raises(HomeAssistantError):
        await _call(hass, session, provider=LLM_PROVIDER_OPENAI)


def test_extract_json_tolerates_markdown_and_prose() -> None:
    """Fences and surrounding prose are stripped."""
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here you go: {"a": 2} done.') == {"a": 2}
    assert extract_json('{"a": 3}') == {"a": 3}
    with pytest.raises(ValueError):
        extract_json("no object at all")


def test_build_prompt_includes_structure_spec() -> None:
    """The prompt lists every expected key and the JSON-only rule."""
    prompt = build_prompt("Do the thing.", STRUCTURE)
    assert "Do the thing." in prompt
    assert '"ok" (boolean)' in prompt
    assert '"score" (number)' in prompt
    assert "ONLY one JSON object" in prompt


async def test_direct_provider_dispatch_not_ai_task(hass: HomeAssistant) -> None:
    """With llm_provider=openai the dispatcher skips the ai_task service."""
    entry = make_event_entry(
        {
            CONF_LLM_PROVIDER: LLM_PROVIDER_OPENAI,
            CONF_LLM_API_KEY: "sk-x",
        }
    )
    entry.add_to_hass(hass)
    register_event_camera(hass, entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data["watchmen"][entry.entry_id]

    fake = AsyncMock(return_value={"ok": True})
    with patch(
        "custom_components.watchmen.providers.async_call_provider", fake
    ):
        result = await async_call_llm(
            hass,
            coordinator,
            task_name="t",
            instructions="i",
            structure=STRUCTURE,
            attachments=[],
        )
    assert result == {"ok": True}
    fake.assert_awaited_once()
    # The AI Task service was never needed for the actual call.
    assert fake.await_args.kwargs["provider"] == LLM_PROVIDER_OPENAI
