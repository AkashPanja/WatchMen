"""Face identification pipeline for Watchmen.

Triggered by an event (doorbell, button, ...). The pipeline:

1. Collects frames captured *before* the trigger from a rolling buffer and
   fetches additional frames *after* it ("back and forward").
2. Asks the LLM to pick the frame with the clearest visible face and to
   return its bounding box.
3. Crops and zooms that face locally, then sends the crop plus the enrolled
   face gallery to the LLM for matching.
4. Returns the person name and a confidence percentage, fires a
   ``watchmen_person_identified`` event, and fails closed on any error.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from PIL import Image

from .const import (
    ATTR_CAMERA,
    ATTR_CANDIDATES,
    ATTR_CONFIDENCE,
    ATTR_CONTEXT,
    ATTR_FACE_VISIBLE,
    ATTR_FRAME_INDEX,
    ATTR_MATCHED,
    ATTR_NAME,
    ATTR_REASON,
    CAPTURES_SUBDIR,
    CONF_FRAMES_BACK,
    CONF_FRAMES_FORWARD,
    DEFAULT_FRAMES_BACK,
    DEFAULT_FRAMES_FORWARD,
    DOMAIN,
    EVENT_PERSON_IDENTIFIED,
    FACE_CROP_MIN_SIDE,
    IMAGE_TIMEOUT,
    MAX_GALLERY_ATTACHMENTS,
)
from .faces import FaceStore, media_uri
from .llm import async_call_llm, normalize_bool

if TYPE_CHECKING:
    from .coordinator import WatchmenCoordinator

_LOGGER = logging.getLogger(__name__)

TASK_FACE_SELECT = "watchmen_face_select"
TASK_FACE_MATCH = "watchmen_face_match"

# Enlargement applied to the detected bbox before cropping (head + margin).
CROP_PADDING = 1.8
# Minimum crop size in pixels worth sending to the model.
MIN_CROP_SIDE = 16
# Maximum upscale factor when zooming into a small face.
MAX_ZOOM = 8.0

FACE_SELECT_STRUCTURE: dict[str, dict[str, Any]] = {
    "face_visible": {
        "selector": {"boolean": {}},
        "description": "True only if a human face is clearly visible.",
        "required": True,
    },
    "frame_index": {
        "selector": {"number": {"min": 0, "max": 50, "step": 1}},
        "description": "0-based index of the selected photo.",
        "required": True,
    },
    "bbox_x": {
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
        "description": "Face bbox left edge, normalized 0-1.",
        "required": True,
    },
    "bbox_y": {
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
        "description": "Face bbox top edge, normalized 0-1.",
        "required": True,
    },
    "bbox_w": {
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
        "description": "Face bbox width, normalized 0-1.",
        "required": True,
    },
    "bbox_h": {
        "selector": {"number": {"min": 0, "max": 1, "step": 0.01}},
        "description": "Face bbox height, normalized 0-1.",
        "required": True,
    },
    "reason": {
        "selector": {"text": {"multiline": True}},
        "description": "One short sentence describing the selection.",
        "required": True,
    },
}

FACE_MATCH_STRUCTURE: dict[str, dict[str, Any]] = {
    "name": {
        "selector": {"text": {}},
        "description": "Exact enrolled name of the best match, or 'unknown'.",
        "required": True,
    },
    "confidence": {
        "selector": {"number": {"min": 0, "max": 100, "step": 1}},
        "description": "Match confidence as a percentage from 0 to 100.",
        "required": True,
    },
    "reason": {
        "selector": {"text": {"multiline": True}},
        "description": "One short sentence explaining the match.",
        "required": True,
    },
}


class FrameBuffer:
    """Rolling buffer of recent JPEG frames for one camera."""

    def __init__(self, maxlen: int) -> None:
        """Keep at most ``maxlen`` frames, oldest dropped first."""
        self._frames: deque[bytes] = deque(maxlen=max(1, maxlen))

    def add(self, data: bytes) -> None:
        """Append one frame."""
        self._frames.append(data)

    def frames(self) -> list[bytes]:
        """Return buffered frames, oldest first."""
        return list(self._frames)

    def clear(self) -> None:
        """Drop all buffered frames."""
        self._frames.clear()


async def _fetch_snapshot(hass: HomeAssistant, camera: str) -> bytes | None:
    """Fetch one camera snapshot, returning None on any failure."""
    try:
        image = await async_get_image(hass, camera, timeout=IMAGE_TIMEOUT)
    except Exception:  # noqa: BLE001 - camera failures must not abort the flow
        _LOGGER.debug("Snapshot failed for %s during identification", camera)
        return None
    return image.content or None


async def async_collect_candidates(
    hass: HomeAssistant,
    coordinator: WatchmenCoordinator,
    camera: str,
    frames_back: int,
    frames_forward: int,
) -> list[bytes]:
    """Collect frames from before the trigger (buffer) and after it.

    If the buffer is empty (or buffering is disabled) a fresh snapshot is
    used as the first candidate.
    """
    back: list[bytes] = []
    if frames_back:
        buffer = coordinator.buffers.get(camera)
        if buffer is not None:
            back = buffer.frames()[-frames_back:]
            buffer.clear()
        if not back:
            fresh = await _fetch_snapshot(hass, camera)
            if fresh is not None:
                back = [fresh]

    forward: list[bytes] = []
    interval = coordinator.face_buffer_interval
    pace = interval if interval > 0 else 1.0
    for _ in range(frames_forward):
        await asyncio.sleep(pace)
        snapshot = await _fetch_snapshot(hass, camera)
        if snapshot is not None:
            forward.append(snapshot)

    return back + forward


def crop_and_zoom_face(
    data: bytes, bbox: tuple[float, float, float, float]
) -> bytes | None:
    """Crop the face region from a frame and zoom it up for the model.

    ``bbox`` is (x, y, w, h) normalized to 0-1. Returns JPEG bytes or None
    when the region is degenerate.
    """
    x, y, w, h = bbox
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        return None
    try:
        with Image.open(io.BytesIO(data)) as raw:
            img = raw.convert("RGB")
    except Exception:  # noqa: BLE001 - unreadable frame
        return None

    width, height = img.size
    center_x = (x + w / 2) * width
    center_y = (y + h / 2) * height
    box_w = w * width * CROP_PADDING
    box_h = h * height * CROP_PADDING

    left = max(0, int(center_x - box_w / 2))
    upper = max(0, int(center_y - box_h / 2))
    right = min(width, int(center_x + box_w / 2))
    lower = min(height, int(center_y + box_h / 2))
    if right - left < MIN_CROP_SIDE or lower - upper < MIN_CROP_SIDE:
        return None

    crop = img.crop((left, upper, right, lower))
    scale = FACE_CROP_MIN_SIDE / min(crop.size)
    if scale > 1.0:
        scale = min(scale, MAX_ZOOM)
        new_size = (
            max(1, round(crop.width * scale)),
            max(1, round(crop.height * scale)),
        )
        crop = crop.resize(new_size, Image.Resampling.LANCZOS)

    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _attachment(relative_path: str) -> dict[str, str]:
    """Build an ai_task attachment dict for a local media file."""
    return {"media_content_id": media_uri(relative_path)}


async def _write_capture(hass: HomeAssistant, data: bytes) -> str:
    """Persist a frame under <config>/media/watchmen/captures; return rel path."""
    filename = f"{uuid4().hex}.jpg"
    relative = str(Path(*CAPTURES_SUBDIR, filename).as_posix())

    def _write() -> None:
        folder = Path(hass.config.path("media", *CAPTURES_SUBDIR))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(data)

    await hass.async_add_executor_job(_write)
    return relative


async def _cleanup_captures(hass: HomeAssistant, relative_paths: list[str]) -> None:
    """Delete temporary capture files."""

    def _delete() -> None:
        for relative in relative_paths:
            Path(hass.config.path("media", relative)).unlink(missing_ok=True)

    await hass.async_add_executor_job(_delete)


def _build_select_instructions(
    camera: str, context: str, candidate_count: int
) -> str:
    """Build the frame-selection prompt."""
    return (
        "You select the best photo from a short camera sequence captured "
        "around an event.\n"
        f"Event context: {context or 'trigger fired'}\n"
        f"The {candidate_count} attachments are frames from camera {camera}, "
        "ordered oldest to newest.\n"
        "Find the photo with the clearest, largest, most frontal human face "
        "and report:\n"
        "- face_visible: true only if a human face is clearly visible in the "
        "selected photo.\n"
        "- frame_index: the 0-based index of the selected photo.\n"
        "- bbox_x, bbox_y, bbox_w, bbox_h: the face bounding box inside the "
        "selected photo, normalized to 0-1 with (x, y) the top-left corner.\n"
        "If no face is clearly visible in any photo, set face_visible to "
        "false (and use frame_index 0 with zero bounding boxes).\n"
        "- reason: one short sentence."
    )


def _build_match_instructions(
    gallery_lines: list[str], crop_index: int, context: str
) -> str:
    """Build the identity-matching prompt."""
    gallery = "\n".join(gallery_lines)
    return (
        "You match a captured face against an enrolled gallery.\n"
        f"Event context: {context or 'trigger fired'}\n"
        "Attachments, in order:\n"
        f"{gallery}\n"
        f"attachment {crop_index}: captured face crop from a camera.\n"
        "Report which enrolled person the captured face belongs to:\n"
        "- name: the exact enrolled name of the best match, or 'unknown'.\n"
        "- confidence: how sure you are, as a percentage from 0 to 100.\n"
        "- reason: one short sentence.\n"
        "Rules: only use enrolled names; if the crop is not a clear face or "
        "nobody matches, answer 'unknown' with confidence at most 40."
    )


def _normalize_percent(value: Any) -> float:
    """Coerce a model value to a 0-100 percentage."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 < score <= 1.0:
        score *= 100.0  # tolerate models answering 0-1
    return max(0.0, min(100.0, score))


def _parse_bbox(result: dict[str, Any]) -> tuple[float, float, float, float]:
    """Read the four bbox fields, defaulting to a degenerate box."""
    values: list[float] = []
    for key in ("bbox_x", "bbox_y", "bbox_w", "bbox_h"):
        try:
            values.append(float(result.get(key, 0.0)))
        except (TypeError, ValueError):
            values.append(0.0)
    return values[0], values[1], values[2], values[3]


def _parse_frame_index(value: Any, count: int) -> int | None:
    """Clamp the selected frame index into range, or None when invalid."""
    try:
        index = round(float(value))
    except (TypeError, ValueError):
        return None
    if 0 <= index < count:
        return index
    return None


async def async_identify_person(
    hass: HomeAssistant,
    coordinator: WatchmenCoordinator,
    camera_entity_id: str,
    context: str,
    frames_back: int | None = None,
    frames_forward: int | None = None,
) -> dict[str, Any]:
    """Identify the person visible around a trigger; returns name + percent.

    Fails closed: raises when no faces are enrolled, no frames could be
    captured, or the model call fails. A frame with no visible face returns
    ``face_visible: false`` without a second model call.
    """
    faces = await FaceStore(hass).async_list()
    if not faces:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_faces_enrolled",
        )

    back_n = (
        int(frames_back)
        if frames_back is not None
        else int(coordinator.options.get(CONF_FRAMES_BACK, DEFAULT_FRAMES_BACK))
    )
    forward_n = (
        int(frames_forward)
        if frames_forward is not None
        else int(coordinator.options.get(CONF_FRAMES_FORWARD, DEFAULT_FRAMES_FORWARD))
    )

    candidates = await async_collect_candidates(
        hass, coordinator, camera_entity_id, max(0, back_n), max(0, forward_n)
    )
    if not candidates:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_frames_captured",
            translation_placeholders={"camera": camera_entity_id},
        )

    written: list[str] = []
    try:
        for frame in candidates:
            written.append(await _write_capture(hass, frame))

        # Stage 1: pick the clearest face frame and locate the face.
        selection = await async_call_llm(
            hass,
            coordinator,
            task_name=TASK_FACE_SELECT,
            instructions=_build_select_instructions(
                camera_entity_id, context, len(candidates)
            ),
            structure=FACE_SELECT_STRUCTURE,
            attachments=[_attachment(path) for path in written],
        )

        reason = str(selection.get("reason") or "")
        face_visible = normalize_bool(selection.get("face_visible"))
        index = _parse_frame_index(selection.get("frame_index"), len(candidates))
        if not face_visible or index is None:
            return _finalize(
                hass,
                camera=camera_entity_id,
                context=context,
                face_visible=False,
                name=None,
                confidence=0.0,
                matched=False,
                reason=reason or "no face visible in captured frames",
                frame_index=index if index is not None else -1,
                candidates=len(candidates),
            )

        bbox = _parse_bbox(selection)
        crop = await hass.async_add_executor_job(
            crop_and_zoom_face, candidates[index], bbox
        )
        if crop is None:
            return _finalize(
                hass,
                camera=camera_entity_id,
                context=context,
                face_visible=False,
                name=None,
                confidence=0.0,
                matched=False,
                reason="face region too small to analyze",
                frame_index=index,
                candidates=len(candidates),
            )

        # Stage 2: match the zoomed face against the enrolled gallery.
        gallery_uris: list[dict[str, str]] = []
        gallery_lines: list[str] = []
        for name, info in faces.items():
            for photo in info.get("photos", []):
                if len(gallery_uris) >= MAX_GALLERY_ATTACHMENTS:
                    break
                gallery_uris.append(_attachment(str(photo)))
                gallery_lines.append(
                    f"attachment {len(gallery_uris)}: enrolled photo of {name}"
                )

        crop_relative = await _write_capture(hass, crop)
        written.append(crop_relative)

        match = await async_call_llm(
            hass,
            coordinator,
            task_name=TASK_FACE_MATCH,
            instructions=_build_match_instructions(
                gallery_lines, len(gallery_uris) + 1, context
            ),
            structure=FACE_MATCH_STRUCTURE,
            attachments=[*gallery_uris, _attachment(crop_relative)],
        )

        raw_name = str(match.get("name") or "unknown").strip()
        confidence = _normalize_percent(match.get("confidence"))
        known_name = raw_name if raw_name in faces else None
        matched = (
            known_name is not None and confidence >= coordinator.identify_threshold
        )
        match_reason = str(match.get("reason") or reason or "")
        if known_name is not None and not matched:
            match_reason = (
                f"confidence {confidence:.0f}% below threshold "
                f"{coordinator.identify_threshold:.0f}%"
            )

        return _finalize(
            hass,
            camera=camera_entity_id,
            context=context,
            face_visible=True,
            name=known_name,
            confidence=confidence,
            matched=matched,
            reason=match_reason,
            frame_index=index,
            candidates=len(candidates),
        )
    finally:
        await _cleanup_captures(hass, written)


def _finalize(
    hass: HomeAssistant,
    *,
    camera: str,
    context: str,
    face_visible: bool,
    name: str | None,
    confidence: float,
    matched: bool,
    reason: str,
    frame_index: int,
    candidates: int,
) -> dict[str, Any]:
    """Fire the identification event and build the service response."""
    payload: dict[str, Any] = {
        ATTR_NAME: name,
        ATTR_MATCHED: matched,
        ATTR_CONFIDENCE: round(confidence, 1),
        ATTR_FACE_VISIBLE: face_visible,
        ATTR_REASON: reason,
        ATTR_CAMERA: camera,
        ATTR_CONTEXT: context,
        ATTR_FRAME_INDEX: frame_index,
        ATTR_CANDIDATES: candidates,
    }
    hass.bus.async_fire(EVENT_PERSON_IDENTIFIED, payload)
    _LOGGER.info(
        "Person identification on %s: name=%s matched=%s confidence=%.1f%% "
        "face_visible=%s (%s)",
        camera,
        name,
        matched,
        confidence,
        face_visible,
        reason,
    )
    return payload
