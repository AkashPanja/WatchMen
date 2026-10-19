"""Motion detection coordinator for Watchmen.

Supports two detection backends per camera:

* ``event``: listens to existing motion binary sensors (ONVIF events,
  FFmpeg motion, Frigate, motionEye, ...). Watchmen finds every motion
  binary sensor living on the same device as the camera.
* ``frame_diff``: polls snapshots from any camera entity and detects
  motion by comparing consecutive frames (works with every camera).
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components.camera import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from PIL import Image, ImageChops

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_CAMERAS,
    CONF_CONFIDENCE_THRESHOLD,
    CONF_COOLDOWN,
    CONF_DIFF_THRESHOLD,
    CONF_FACE_BUFFER_INTERVAL,
    CONF_FRAME_INTERVAL,
    CONF_FRAMES_BACK,
    CONF_FRAMES_FORWARD,
    CONF_IDENTIFY_THRESHOLD,
    CONF_LLM_API_KEY,
    CONF_LLM_BASE_URL,
    CONF_LLM_MODEL,
    CONF_LLM_PROVIDER,
    CONF_MODE,
    CONF_MOTION_OFF_DELAY,
    CONF_RESIDENTS,
    DEFAULT_AI_TASK_ENTITY,
    DEFAULT_DIFF_THRESHOLD,
    DEFAULT_FACE_BUFFER_INTERVAL,
    DEFAULT_FRAME_INTERVAL,
    DEFAULT_FRAMES_BACK,
    DEFAULT_FRAMES_FORWARD,
    DEFAULT_IDENTIFY_THRESHOLD,
    DEFAULT_LLM_API_KEY,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPTIONS,
    DEFAULT_RESIDENTS,
    DOMAIN,
    FRAME_SIZE,
    IMAGE_TIMEOUT,
    MODE_EVENT,
    MODE_FRAME_DIFF,
    PIXEL_DIFF_THRESHOLD,
)
from .identify import FrameBuffer

_LOGGER = logging.getLogger(__name__)

type CameraId = str


@dataclass
class CameraRuntime:
    """Runtime state for a single watched camera."""

    camera_entity_id: str
    mode: str
    motion: bool = False
    last_motion: Any = None
    last_change_ratio: float | None = None
    image_ok: bool = True
    decision: dict[str, Any] | None = None
    last_decision_at: Any = None
    source_sensors: list[str] = field(default_factory=list)
    previous_frame: Any = field(default=None, repr=False)
    off_cancel: Callable[[], None] | None = field(default=None, repr=False)


class WatchmenCoordinator(DataUpdateCoordinator[dict[CameraId, CameraRuntime]]):
    """Coordinates motion state for all cameras of one config entry."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> bool:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
        )
        self.entry = entry
        self.options: dict[str, Any] = {
            **DEFAULT_OPTIONS,
            **entry.data,
            **entry.options,
        }
        self.runtimes: dict[str, CameraRuntime] = {}
        self._unsubs: list[Callable[[], None]] = []
        self._sensor_to_camera: dict[str, str] = {}
        self.data = self.runtimes

        self.face_buffer_interval = max(
            0.0,
            float(
                self.options.get(
                    CONF_FACE_BUFFER_INTERVAL, DEFAULT_FACE_BUFFER_INTERVAL
                )
            ),
        )
        self.buffers: dict[str, FrameBuffer] = {}
        self._buffer_unsub: Callable[[], None] | None = None
        self._buffer_tick_task: asyncio.Task | None = None

        cameras: list[str] = list(
            self.options.get(CONF_CAMERAS) or entry.data.get(CONF_CAMERAS) or []
        )
        for camera in cameras:
            mode = self._resolve_mode(camera)
            runtime = CameraRuntime(camera_entity_id=camera, mode=mode)
            self.runtimes[camera] = runtime
            if mode == MODE_EVENT:
                runtime.source_sensors = self._find_motion_sensors(camera)
                for sensor in runtime.source_sensors:
                    self._sensor_to_camera[sensor] = camera

        if self.face_buffer_interval > 0:
            maxlen = int(
                self.options.get(CONF_FRAMES_BACK, DEFAULT_FRAMES_BACK)
            )
            self.buffers = {
                camera: FrameBuffer(maxlen) for camera in cameras
            }

        if any(rt.mode == MODE_FRAME_DIFF for rt in self.runtimes.values()):
            self.update_interval = timedelta(
                seconds=float(self.options[CONF_FRAME_INTERVAL])
            )
        else:
            self.update_interval = None

    # ------------------------------------------------------------------
    # Options helpers
    # ------------------------------------------------------------------
    @property
    def motion_off_delay(self) -> float:
        """Seconds without detection before motion clears."""
        return float(self.options[CONF_MOTION_OFF_DELAY])

    @property
    def confidence_threshold(self) -> float:
        """Minimum LLM confidence required to approve access."""
        return float(self.options[CONF_CONFIDENCE_THRESHOLD])

    @property
    def cooldown_seconds(self) -> float:
        """Minimum seconds between two AI access reviews."""
        return float(self.options[CONF_COOLDOWN])

    @property
    def residents(self) -> list[str]:
        """Configured resident names (comma separated)."""
        raw = str(self.options.get(CONF_RESIDENTS, DEFAULT_RESIDENTS))
        return [name.strip() for name in raw.split(",") if name.strip()]

    @property
    def ai_task_entity(self) -> str:
        """AI task entity used for access reviews."""
        return str(self.options.get(CONF_AI_TASK_ENTITY, DEFAULT_AI_TASK_ENTITY))

    @property
    def llm_provider(self) -> str:
        """Configured LLM provider (ai_task, openai, claude, groq, openrouter)."""
        return str(self.options.get(CONF_LLM_PROVIDER, DEFAULT_LLM_PROVIDER))

    @property
    def llm_api_key(self) -> str:
        """API key for direct LLM providers (never logged)."""
        return str(self.options.get(CONF_LLM_API_KEY, DEFAULT_LLM_API_KEY))

    @property
    def llm_model(self) -> str:
        """Model name for direct LLM providers ("" = provider default)."""
        return str(self.options.get(CONF_LLM_MODEL, DEFAULT_LLM_MODEL))

    @property
    def llm_base_url(self) -> str:
        """Base URL override for direct LLM providers ("" = provider default)."""
        return str(self.options.get(CONF_LLM_BASE_URL, DEFAULT_LLM_BASE_URL))

    @property
    def frames_back(self) -> int:
        """Buffered frames collected before an identification trigger."""
        return int(self.options.get(CONF_FRAMES_BACK, DEFAULT_FRAMES_BACK))

    @property
    def frames_forward(self) -> int:
        """Frames fetched after an identification trigger."""
        return int(self.options.get(CONF_FRAMES_FORWARD, DEFAULT_FRAMES_FORWARD))

    @property
    def identify_threshold(self) -> float:
        """Minimum match percentage to report a person as identified."""
        return float(
            self.options.get(CONF_IDENTIFY_THRESHOLD, DEFAULT_IDENTIFY_THRESHOLD)
        )

    def get_runtime(self, camera_entity_id: str) -> CameraRuntime | None:
        """Return the runtime for a camera entity id."""
        return self.runtimes.get(camera_entity_id)

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _resolve_mode(self, camera_entity_id: str) -> str:
        """Resolve the effective detection mode for a camera."""
        mode = self.options[CONF_MODE]
        if mode == MODE_EVENT:
            if self._find_motion_sensors(camera_entity_id):
                return MODE_EVENT
            _LOGGER.warning(
                "%s: no motion binary sensor found on the camera device, "
                "falling back to frame analysis",
                camera_entity_id,
            )
            return MODE_FRAME_DIFF
        if mode == MODE_FRAME_DIFF:
            return MODE_FRAME_DIFF
        # auto
        if self._find_motion_sensors(camera_entity_id):
            return MODE_EVENT
        return MODE_FRAME_DIFF

    def _find_motion_sensors(self, camera_entity_id: str) -> list[str]:
        """Find motion binary sensors attached to the camera's device."""
        entity_registry = er.async_get(self.hass)
        camera_entry = entity_registry.async_get(camera_entity_id)
        if camera_entry is None or camera_entry.device_id is None:
            return []
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get(camera_entry.device_id)
        if device is None:
            return []
        sensors: list[str] = []
        for entry in er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities=True
        ):
            if entry.domain != "binary_sensor":
                continue
            state = self.hass.states.get(entry.entity_id)
            if state is None:
                continue
            if state.attributes.get(ATTR_DEVICE_CLASS) == "motion":
                sensors.append(entry.entity_id)
        return sensors

    @callback
    def _async_start_event_listeners(self) -> None:
        """Subscribe to state changes of the source motion sensors."""
        if not self._sensor_to_camera:
            return
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                list(self._sensor_to_camera),
                self._async_sensor_state_changed,
            )
        )

    async def async_start(self) -> None:
        """Start event listeners and the frame buffer poller."""
        self._async_start_event_listeners()
        if self.buffers and any(
            rt.mode != MODE_FRAME_DIFF for rt in self.runtimes.values()
        ):
            self._buffer_unsub = async_track_time_interval(
                self.hass,
                self._async_buffer_tick,
                timedelta(seconds=self.face_buffer_interval),
            )

    @callback
    def _async_buffer_tick(self, _now: Any) -> None:
        """Schedule one buffer fetch round (skips if the last one lags)."""
        if self._buffer_tick_task is not None and not self._buffer_tick_task.done():
            return
        self._buffer_tick_task = self.hass.async_create_task(
            self._async_buffer_fetch_all(), name=f"{DOMAIN}_buffer"
        )

    async def _async_buffer_fetch_all(self) -> None:
        """Snapshot every non-frame-analysis camera into its buffer."""
        await asyncio.gather(
            *(
                self._async_buffer_fetch_one(camera, buffer)
                for camera, buffer in self.buffers.items()
                if (runtime := self.runtimes.get(camera)) is not None
                and runtime.mode != MODE_FRAME_DIFF
            )
        )

    async def _async_buffer_fetch_one(
        self, camera: str, buffer: FrameBuffer
    ) -> None:
        """Fetch one snapshot into the buffer (frame-analysis cameras skip)."""
        try:
            image = await async_get_image(self.hass, camera, timeout=IMAGE_TIMEOUT)
        except Exception:
            _LOGGER.debug("Buffer snapshot failed for %s", camera, exc_info=True)
            return
        if image.content:
            buffer.add(image.content)

    # ------------------------------------------------------------------
    # Event mode
    # ------------------------------------------------------------------
    async def _async_sensor_state_changed(self, event: Event) -> None:
        """Handle a state change of a source motion binary sensor."""
        sensor_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if sensor_id is None or new_state is None:
            return
        camera = self._sensor_to_camera.get(sensor_id)
        if camera is None:
            return
        runtime = self.runtimes.get(camera)
        if runtime is None:
            return

        if new_state.state == STATE_ON:
            self._set_motion(runtime, True)
            self._schedule_off_timer(runtime)
        else:
            self._clear_off_timer(runtime)
            self._set_motion(runtime, False)

        self.async_set_updated_data(self.data)

    @callback
    def _set_motion(self, runtime: CameraRuntime, active: bool) -> None:
        """Update motion state and timestamp."""
        if runtime.motion == active:
            if active:
                runtime.last_motion = dt_util.utcnow()
            return
        runtime.motion = active
        if active:
            runtime.last_motion = dt_util.utcnow()

    @callback
    def _schedule_off_timer(self, runtime: CameraRuntime) -> None:
        """Schedule clearing of a stuck-on motion state."""
        self._clear_off_timer(runtime)
        delay = self.motion_off_delay

        @callback
        def _async_off(_now: Any) -> None:
            runtime.off_cancel = None
            self._set_motion(runtime, False)
            self.async_set_updated_data(self.data)

        runtime.off_cancel = async_call_later(self.hass, delay, _async_off)

    @callback
    def _clear_off_timer(self, runtime: CameraRuntime) -> None:
        """Cancel a pending motion-off timer."""
        if runtime.off_cancel is not None:
            runtime.off_cancel()
            runtime.off_cancel = None

    # ------------------------------------------------------------------
    # Frame analysis mode
    # ------------------------------------------------------------------
    async def _async_update_data(self) -> dict[CameraId, CameraRuntime]:
        """Fetch and compare snapshots for frame-analysis cameras."""
        frame_cameras = [
            rt for rt in self.runtimes.values() if rt.mode == MODE_FRAME_DIFF
        ]
        if not frame_cameras:
            return self.data

        await asyncio.gather(
            *(self._async_analyze_camera(rt) for rt in frame_cameras)
        )
        return self.data

    async def _async_analyze_camera(self, runtime: CameraRuntime) -> None:
        """Fetch one snapshot and compare it to the previous frame."""
        try:
            image = await async_get_image(
                self.hass,
                runtime.camera_entity_id,
                timeout=IMAGE_TIMEOUT,
            )
        except Exception:
            runtime.image_ok = False
            runtime.previous_frame = None
            self._set_motion(runtime, False)
            _LOGGER.debug(
                "Snapshot failed for %s", runtime.camera_entity_id, exc_info=True
            )
            return

        if not image.content:
            runtime.image_ok = False
            runtime.previous_frame = None
            self._set_motion(runtime, False)
            return

        runtime.image_ok = True
        image_bytes = image.content

        buffer = self.buffers.get(runtime.camera_entity_id)
        if buffer is not None:
            buffer.add(image_bytes)

        def _process() -> tuple[float | None, bool]:
            with Image.open(io.BytesIO(image_bytes)) as raw:
                frame = raw.convert("L").resize(FRAME_SIZE)
            ratio: float | None = None
            detected = False
            previous = runtime.previous_frame
            if previous is not None:
                diff = ImageChops.difference(previous, frame)
                mask = diff.point(
                    lambda p: 255 if p > PIXEL_DIFF_THRESHOLD else 0
                )
                histogram = mask.histogram()
                total = sum(histogram)
                ratio = histogram[255] / total if total else 0.0
                threshold = float(self.options[CONF_DIFF_THRESHOLD])
                detected = ratio >= threshold
            runtime.previous_frame = frame
            return ratio, detected

        ratio, detected = await self.hass.async_add_executor_job(_process)
        runtime.last_change_ratio = ratio

        if detected:
            self._set_motion(runtime, True)
        elif runtime.motion and runtime.last_motion is not None:
            elapsed = (dt_util.utcnow() - runtime.last_motion).total_seconds()
            if elapsed > self.motion_off_delay:
                self._set_motion(runtime, False)

    # ------------------------------------------------------------------
    # Access decisions
    # ------------------------------------------------------------------
    @property
    def frame_interval(self) -> float:
        """Frame analysis polling interval in seconds."""
        return float(self.options.get(CONF_FRAME_INTERVAL, DEFAULT_FRAME_INTERVAL))

    @property
    def diff_threshold(self) -> float:
        """Changed-pixel ratio required to flag motion."""
        return float(self.options.get(CONF_DIFF_THRESHOLD, DEFAULT_DIFF_THRESHOLD))

    @callback
    def async_record_decision(
        self, camera_entity_id: str, decision: dict[str, Any]
    ) -> None:
        """Store the latest access decision for a camera."""
        runtime = self.runtimes.get(camera_entity_id)
        if runtime is None:
            return
        runtime.decision = decision
        runtime.last_decision_at = dt_util.utcnow()
        self.async_set_updated_data(self.data)

    async def async_stop(self) -> None:
        """Clean up listeners, timers and the buffer poller."""
        if self._buffer_unsub is not None:
            self._buffer_unsub()
            self._buffer_unsub = None
        if self._buffer_tick_task is not None and not self._buffer_tick_task.done():
            self._buffer_tick_task.cancel()
            try:
                await self._buffer_tick_task
            except asyncio.CancelledError:
                pass
        self._buffer_tick_task = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for runtime in self.runtimes.values():
            self._clear_off_timer(runtime)
        shutdown = getattr(super(), "async_shutdown", None)
        if shutdown is not None:
            await shutdown()
