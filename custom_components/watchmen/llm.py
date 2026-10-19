"""LLM access review engine for Watchmen.

Asks a Home Assistant AI Task entity to review a camera snapshot and
return a structured approve/deny decision. Fails closed: any error or
low confidence results in a denial.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_APPROVED,
    ATTR_CAMERA,
    ATTR_CONFIDENCE,
    ATTR_CONTEXT,
    ATTR_PERSON,
    ATTR_REASON,
    DOMAIN,
    EVENT_GATE_APPROVED,
    EVENT_GATE_DENIED,
)
from .coordinator import WatchmenCoordinator

_LOGGER = logging.getLogger(__name__)

AI_TASK_SERVICE = "generate_data"
TASK_NAME = "watchmen_access_review"

DECISION_STRUCTURE: dict[str, dict[str, Any]] = {
    "approved": {
        "selector": {"boolean": {}},
        "description": (
            "Whether access should be granted. Only approve when a known "
            "resident clearly and unambiguously appears to request entry."
        ),
        "required": True,
    },
    "person": {
        "selector": {"text": {}},
        "description": "Name of the recognized person, or 'unknown'.",
        "required": True,
    },
    "confidence": {
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
        "description": "Confidence of the decision from 0 to 1.",
        "required": True,
    },
    "reason": {
        "selector": {"text": {"multiline": True}},
        "description": "Short explanation of the decision.",
        "required": True,
    },
}


def _build_instructions(
    camera_entity_id: str,
    context: str,
    residents: list[str],
    threshold: float,
) -> str:
    """Build the review prompt."""
    resident_list = ", ".join(residents) if residents else "(none configured)"
    return (
        "You are the access-control reviewer for a home automation system "
        "called Watchmen.\n"
        f"A trigger fired: {context or 'access requested'}\n"
        f"The attached image is a snapshot from camera {camera_entity_id}.\n"
        f"Known residents: {resident_list}.\n"
        "Decide whether to grant access (for example unlocking a gate).\n"
        "Rules:\n"
        f"- Approve ONLY if a known resident is clearly recognizable and "
        f"appears to be requesting entry.\n"
        f"- Deny if the image is unclear, empty, shows no person, shows an "
        f"unrecognized person, or you are not at least {threshold:.2f} "
        f"confident.\n"
        "- When in doubt, deny (fail closed).\n"
        "- 'person' must be a resident name from the list, or 'unknown'.\n"
        "- 'confidence' is a number between 0 and 1.\n"
        "- 'reason' is one short sentence."
    )


def _normalize_bool(value: Any) -> bool:
    """Coerce model output to a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= 0.5
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "approve", "approved"}
    return False


def _normalize_confidence(value: Any) -> float:
    """Coerce model output to a 0..1 confidence score."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:
        score = score / 100.0
    return max(0.0, min(1.0, score))


async def async_request_access_review(
    hass: HomeAssistant,
    coordinator: WatchmenCoordinator,
    camera_entity_id: str,
    context: str,
) -> dict[str, Any]:
    """Run the LLM review and return the normalized decision."""
    now = dt_util.utcnow()
    runtime = coordinator.get_runtime(camera_entity_id)
    assert runtime is not None

    # Cooldown: never spam the model (and never unlock twice in a row).
    if runtime.last_decision_at is not None:
        elapsed = (now - runtime.last_decision_at).total_seconds()
        if elapsed < coordinator.cooldown_seconds:
            _LOGGER.debug(
                "Access review for %s suppressed by cooldown (%.0fs left)",
                camera_entity_id,
                coordinator.cooldown_seconds - elapsed,
            )
            return _finalize(
                coordinator,
                camera_entity_id,
                approved=False,
                person=None,
                confidence=0.0,
                reason="cooldown_active",
                context=context,
                record=False,
            )

    if not hass.services.has_service("ai_task", AI_TASK_SERVICE):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ai_task_missing",
        )

    threshold = coordinator.confidence_threshold
    data: dict[str, Any] = {
        "task_name": TASK_NAME,
        "instructions": _build_instructions(
            camera_entity_id, context, coordinator.residents, threshold
        ),
        "structure": DECISION_STRUCTURE,
        "attachments": [
            {
                "media_content_id": f"media-source://camera/{camera_entity_id}",
                "media_content_type": "image/jpeg",
            }
        ],
    }
    if coordinator.ai_task_entity:
        data["entity_id"] = coordinator.ai_task_entity

    try:
        response = await hass.services.async_call(
            "ai_task",
            AI_TASK_SERVICE,
            data,
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="review_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    result: Any = response
    if isinstance(response, dict):
        result = response.get("data", response)
    if not isinstance(result, dict):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="review_failed",
            translation_placeholders={"error": "unexpected AI response format"},
        )

    model_approved = _normalize_bool(result.get("approved", False))
    confidence = _normalize_confidence(result.get("confidence", 0.0))
    person = str(result.get("person") or "unknown")
    reason = str(result.get("reason") or "")

    approved = model_approved and confidence >= threshold
    if model_approved and not approved:
        reason = f"confidence {confidence:.2f} below threshold {threshold:.2f}"

    return _finalize(
        coordinator,
        camera_entity_id,
        approved=approved,
        person=person,
        confidence=confidence,
        reason=reason,
        context=context,
        record=True,
    )


def _finalize(
    coordinator: WatchmenCoordinator,
    camera_entity_id: str,
    *,
    approved: bool,
    person: str | None,
    confidence: float,
    reason: str,
    context: str,
    record: bool,
) -> dict[str, Any]:
    """Store/fire the decision and build the service response."""
    decision: dict[str, Any] = {
        ATTR_APPROVED: approved,
        ATTR_PERSON: person,
        ATTR_CONFIDENCE: round(confidence, 3),
        ATTR_REASON: reason,
        ATTR_CONTEXT: context,
        ATTR_CAMERA: camera_entity_id,
    }

    if record:
        coordinator.async_record_decision(camera_entity_id, decision)
        event = EVENT_GATE_APPROVED if approved else EVENT_GATE_DENIED
        coordinator.hass.bus.async_fire(event, decision)
        _LOGGER.info(
            "Access review %s for %s (person=%s confidence=%.2f): %s",
            "APPROVED" if approved else "DENIED",
            camera_entity_id,
            person,
            confidence,
            reason,
        )
    return decision
