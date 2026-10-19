"""Constants for the Watchmen integration."""

from __future__ import annotations

DOMAIN = "watchmen"

CONF_CAMERAS = "cameras"
CONF_MODE = "mode"
CONF_FRAME_INTERVAL = "frame_interval"
CONF_DIFF_THRESHOLD = "diff_threshold"
CONF_MOTION_OFF_DELAY = "motion_off_delay"
CONF_RESIDENTS = "residents"
CONF_AI_TASK_ENTITY = "ai_task_entity"
CONF_CONFIDENCE_THRESHOLD = "confidence_threshold"
CONF_COOLDOWN = "cooldown"

MODE_AUTO = "auto"
MODE_EVENT = "event"
MODE_FRAME_DIFF = "frame_diff"
MODES = [MODE_AUTO, MODE_EVENT, MODE_FRAME_DIFF]

DEFAULT_MODE = MODE_AUTO
DEFAULT_FRAME_INTERVAL = 2.0
DEFAULT_DIFF_THRESHOLD = 0.03
DEFAULT_MOTION_OFF_DELAY = 30
DEFAULT_RESIDENTS = ""
DEFAULT_AI_TASK_ENTITY = ""
DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_COOLDOWN = 30

DEFAULT_OPTIONS = {
    CONF_MODE: DEFAULT_MODE,
    CONF_FRAME_INTERVAL: DEFAULT_FRAME_INTERVAL,
    CONF_DIFF_THRESHOLD: DEFAULT_DIFF_THRESHOLD,
    CONF_MOTION_OFF_DELAY: DEFAULT_MOTION_OFF_DELAY,
    CONF_RESIDENTS: DEFAULT_RESIDENTS,
    CONF_AI_TASK_ENTITY: DEFAULT_AI_TASK_ENTITY,
    CONF_CONFIDENCE_THRESHOLD: DEFAULT_CONFIDENCE_THRESHOLD,
    CONF_COOLDOWN: DEFAULT_COOLDOWN,
}

SERVICE_REQUEST_ACCESS = "request_access"
CONF_CAMERA = "camera"
CONF_CONTEXT = "context"

ATTR_PERSON = "person"
ATTR_CONFIDENCE = "confidence"
ATTR_REASON = "reason"
ATTR_CONTEXT = "context"
ATTR_APPROVED = "approved"
ATTR_CAMERA = "camera"

EVENT_GATE_APPROVED = "watchmen_gate_approved"
EVENT_GATE_DENIED = "watchmen_gate_denied"

# Frame analysis: small grayscale working resolution.
FRAME_SIZE = (160, 90)
# Per-pixel gray delta (0-255) considered a real change, ignores sensor noise.
PIXEL_DIFF_THRESHOLD = 25
# Snapshot fetch timeout in seconds for frame analysis.
IMAGE_TIMEOUT = 10
