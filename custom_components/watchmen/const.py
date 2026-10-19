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
# Options sentinel: "use Home Assistant's preferred AI task entity".
# A non-empty value so the required select can be pre-selected in the UI.
AI_TASK_AUTO = "auto"
CONF_CONFIDENCE_THRESHOLD = "confidence_threshold"
CONF_COOLDOWN = "cooldown"
CONF_FACE_BUFFER_INTERVAL = "face_buffer_interval"
CONF_FRAMES_BACK = "frames_back"
CONF_FRAMES_FORWARD = "frames_forward"
CONF_IDENTIFY_THRESHOLD = "identify_threshold"
CONF_LLM_PROVIDER = "llm_provider"
CONF_LLM_API_KEY = "llm_api_key"
CONF_LLM_MODEL = "llm_model"
CONF_LLM_BASE_URL = "llm_base_url"

# LLM providers: HA built-in AI Task, or direct API calls.
LLM_PROVIDER_AI_TASK = "ai_task"
LLM_PROVIDER_OPENAI = "openai"
LLM_PROVIDER_CLAUDE = "claude"
LLM_PROVIDER_GROQ = "groq"
LLM_PROVIDER_OPENROUTER = "openrouter"
LLM_PROVIDERS = [
    LLM_PROVIDER_AI_TASK,
    LLM_PROVIDER_OPENAI,
    LLM_PROVIDER_CLAUDE,
    LLM_PROVIDER_GROQ,
    LLM_PROVIDER_OPENROUTER,
]

# Direct API endpoints (base URLs; overridable via the base URL option).
LLM_BASE_URLS = {
    LLM_PROVIDER_OPENAI: "https://api.openai.com/v1",
    LLM_PROVIDER_CLAUDE: "https://api.anthropic.com/v1",
    LLM_PROVIDER_GROQ: "https://api.groq.com/openai/v1",
    LLM_PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
}

# Sensible per-provider model defaults (editable in options).
LLM_DEFAULT_MODELS = {
    LLM_PROVIDER_OPENAI: "gpt-4o-mini",
    LLM_PROVIDER_CLAUDE: "claude-3-5-sonnet-latest",
    LLM_PROVIDER_GROQ: "llama-3.3-70b-versatile",
    LLM_PROVIDER_OPENROUTER: "openai/gpt-4o-mini",
}

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
DEFAULT_FACE_BUFFER_INTERVAL = 0.0  # 0 = off; set ~1.0 for pre-trigger frames
DEFAULT_FRAMES_BACK = 4
DEFAULT_FRAMES_FORWARD = 2
DEFAULT_IDENTIFY_THRESHOLD = 70.0
DEFAULT_LLM_PROVIDER = LLM_PROVIDER_AI_TASK
DEFAULT_LLM_API_KEY = ""
DEFAULT_LLM_MODEL = ""
DEFAULT_LLM_BASE_URL = ""

DEFAULT_OPTIONS = {
    CONF_MODE: DEFAULT_MODE,
    CONF_FRAME_INTERVAL: DEFAULT_FRAME_INTERVAL,
    CONF_DIFF_THRESHOLD: DEFAULT_DIFF_THRESHOLD,
    CONF_MOTION_OFF_DELAY: DEFAULT_MOTION_OFF_DELAY,
    CONF_RESIDENTS: DEFAULT_RESIDENTS,
    CONF_AI_TASK_ENTITY: AI_TASK_AUTO,
    CONF_CONFIDENCE_THRESHOLD: DEFAULT_CONFIDENCE_THRESHOLD,
    CONF_COOLDOWN: DEFAULT_COOLDOWN,
    CONF_FACE_BUFFER_INTERVAL: DEFAULT_FACE_BUFFER_INTERVAL,
    CONF_FRAMES_BACK: DEFAULT_FRAMES_BACK,
    CONF_FRAMES_FORWARD: DEFAULT_FRAMES_FORWARD,
    CONF_IDENTIFY_THRESHOLD: DEFAULT_IDENTIFY_THRESHOLD,
    CONF_LLM_PROVIDER: DEFAULT_LLM_PROVIDER,
    CONF_LLM_API_KEY: DEFAULT_LLM_API_KEY,
    CONF_LLM_MODEL: DEFAULT_LLM_MODEL,
    CONF_LLM_BASE_URL: DEFAULT_LLM_BASE_URL,
}

SERVICE_REQUEST_ACCESS = "request_access"
SERVICE_ADD_FACE = "add_face"
SERVICE_REMOVE_FACE = "remove_face"
SERVICE_LIST_FACES = "list_faces"
SERVICE_IDENTIFY_PERSON = "identify_person"
CONF_CAMERA = "camera"
CONF_CONTEXT = "context"
CONF_NAME = "name"
CONF_IMAGE = "image"

ATTR_PERSON = "person"
ATTR_NAME = "name"
ATTR_CONFIDENCE = "confidence"
ATTR_REASON = "reason"
ATTR_CONTEXT = "context"
ATTR_APPROVED = "approved"
ATTR_CAMERA = "camera"
ATTR_MATCHED = "matched"
ATTR_FACE_VISIBLE = "face_visible"
ATTR_PHOTOS = "photos"
ATTR_FRAME_INDEX = "frame_index"
ATTR_CANDIDATES = "candidates"

EVENT_GATE_APPROVED = "watchmen_gate_approved"
EVENT_GATE_DENIED = "watchmen_gate_denied"
EVENT_PERSON_IDENTIFIED = "watchmen_person_identified"

# Files live under <config>/media so they can be attached to AI tasks
# via media-source:// URIs.
MEDIA_ROOT_SUBDIR = ("watchmen", "faces")
CAPTURES_SUBDIR = ("watchmen", "captures")
MEDIA_SOURCE_BASE = "media-source://media_source/local"

# Frame analysis: small grayscale working resolution.
FRAME_SIZE = (160, 90)
# Per-pixel gray delta (0-255) considered a real change, ignores sensor noise.
PIXEL_DIFF_THRESHOLD = 25
# Snapshot fetch timeout in seconds for frame analysis.
IMAGE_TIMEOUT = 10

# Face identification: minimum short side of a zoomed crop sent to the model.
FACE_CROP_MIN_SIDE = 320
# Enrolled photos kept per person (oldest replaced beyond this).
MAX_PHOTOS_PER_PERSON = 12
# Cap on gallery attachments sent to the model per match call.
MAX_GALLERY_ATTACHMENTS = 12
