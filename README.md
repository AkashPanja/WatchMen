# Watchmen

**Motion detection for any Home Assistant camera, with an optional LLM access
review that can unlock your gate.**

Watchmen is a Home Assistant custom integration (installable via HACS) that:

- Detects **motion** on any camera:
  - **Event mode** — reuses the camera's own motion events (ONVIF motion alarm,
    cell motion, human shape detection, FFmpeg motion, Frigate, motionEye, ...).
    Watchmen finds every motion binary sensor on the camera's device
    automatically.
  - **Frame analysis mode** — polls snapshots from *any* camera entity and
    detects motion by comparing consecutive frames (works even when the camera
    has no event support at all).
  - **Automatic mode** (default) — event mode when available, frame analysis
    otherwise.
- Fires a **generic-trigger → AI approval → action** pipeline: any Home
  Assistant trigger (doorbell, button, webhook, state change, calendar, ...)
  can start an AI review of a camera snapshot; when the AI approves the person
  in view, your gate-unlock actions run.

## Installation

### HACS (custom repository)

1. HACS → **Integrations** → ⋮ menu (top right) → **Custom repositories**
2. Repository: `https://github.com/AkashPanja/WatchMen` — category **Integration**
3. Click **Install** → restart Home Assistant

### Without HACS (git / zip)

Copy `custom_components/watchmen/` into `<config>/custom_components/`, then
restart Home Assistant:

```bash
# Option A: git clone (runs on the Home Assistant host)
cd <config>/custom_components
git clone --depth 1 https://github.com/AkashPanja/WatchMen.git _watchmen
mv _watchmen/custom_components/watchmen ./watchmen
rm -rf _watchmen
```

```bash
# Option B: zip download — get the ZIP from
# https://github.com/AkashPanja/WatchMen/archive/refs/heads/main.zip
# then extract and copy:  WatchMen-main/custom_components/watchmen → <config>/custom_components/watchmen
```

## Configuration

Settings → Devices & services → **Add integration → Watchmen**:

1. Select the cameras to watch
2. Pick a detection mode (Automatic is recommended)

Then open the integration **options** to tune:

| Option | Default | Meaning |
| --- | --- | --- |
| Detection mode | Automatic | Event / frame analysis / automatic |
| Frame analysis interval | 2 s | Snapshot polling rate (frame mode) |
| Motion sensitivity | 0.03 | Fraction of changed pixels that flags motion |
| Motion auto-off delay | 30 s | Motion clears after this long without detection |
| Known residents | *(empty)* | Comma separated names the AI may approve |
| AI task entity | *(preferred)* | AI Task entity used for access reviews |
| Minimum confidence | 0.85 | Approvals below this become denials |
| Cooldown | 30 s | Minimum time between two AI reviews (anti-loop) |

## Entities

Per camera:

- `binary_sensor.*_motion` — unified motion (device class `motion`), works
  with `motion.detected` triggers and all motion automations
- `sensor.*_last_access_decision` — `approved` / `denied` with `person`,
  `confidence`, `reason`, `context` and `reviewed_at` attributes

## Service: `watchmen.request_access`

```yaml
service: watchmen.request_access
data:
  camera: camera.front_door
  context: front gate doorbell pressed
response_variable: decision
```

Returns:

```yaml
approved: true
person: Alice
confidence: 0.93
reason: Known resident clearly visible at the gate
context: front gate doorbell pressed
camera: camera.front_door
```

The call **fails closed**: model errors raise (stopping the automation),
low confidence is converted to a denial, and the cooldown suppresses repeat
calls. Every real decision fires a `watchmen_gate_approved` or
`watchmen_gate_denied` event with the same payload for logging and
notifications.

Access reviews use Home Assistant's built-in **AI Task** integration, so any
configured provider works (OpenAI, Anthropic, Google Gemini, Ollama for fully
local, ...). Make sure you have an AI provider configured in HA, then pick its
AI task entity in the Watchmen options (or rely on the preferred AI task
entity).

## Gate unlock blueprint (generic trigger → AI approval → unlock)

1. Copy `blueprints/automation/watchmen/gate_access.yaml` to
   `<config>/blueprints/automation/watchmen/`
2. Settings → Automations → **Create automation** → **Use blueprint**
3. Choose **Watchmen gate access** and configure:
   - **Trigger** — *any* trigger: doorbell pressed, motion, button, webhook,
     device trigger, calendar, ...
   - **Camera** — snapshot source for the AI
   - **Approved actions** — e.g. `lock.unlock` / `cover.open` / relay on
   - **Minimum confidence**, plus optional denied actions (notify, log)

Flow:

```
generic trigger → watchmen.request_access (snapshot + LLM)
               → approved? → unlock actions
               → denied / cooldown / error → denied actions (gate stays shut)
```

Or as YAML (inputs nest under `use_blueprint`):

```yaml
alias: Gate access
use_blueprint:
  path: watchmen/gate_access.yaml
  input:
    trigger:
      - platform: state
        entity_id: binary_sensor.doorbell
        to: "on"
    camera: camera.front_door
    context_note: doorbell pressed
    min_confidence: 0.85
    approved_actions:
      - action: lock.unlock
        target:
          entity_id: lock.gate
    denied_actions:
      - action: notify.notify
        data:
          message: Gate access denied
```

Safety built in:

- **Fail closed** on any AI/provider error
- **Confidence threshold** enforced twice (integration options + blueprint)
- **Cooldown** prevents unlock loops from a person lingering in view
- **Known residents allowlist** in the prompt
- Decision audit trail in the `sensor.*_last_access_decision` attributes and
  `watchmen_gate_*` events

> Requires Home Assistant **2024.11** or newer (blueprint trigger merging needs
> 2024.10).

## Development

```bash
ruff check custom_components tests
python -m compileall custom_components
python -m pytest tests        # needs pytest-homeassistant-custom-component
```

## Roadmap

- [x] v0.1 — config flow, event-based + frame-diff motion, entities, events
- [x] v0.2 — `request_access` LLM decision service + blueprint
- [ ] v0.3 — snapshot archiving on motion
- [ ] v0.4 — dedicated person/face stage (LLM vision or local face model)

## License

MIT
