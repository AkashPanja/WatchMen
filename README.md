# Watchmen

[![HACS validation](https://github.com/AkashPanja/WatchMen/actions/workflows/hacs.yaml/badge.svg)](https://github.com/AkashPanja/WatchMen/actions/workflows/hacs.yaml)
[![Hassfest](https://github.com/AkashPanja/WatchMen/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/AkashPanja/WatchMen/actions/workflows/hassfest.yaml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

## Requirements

- Home Assistant **2024.11** or newer
- Any camera integration that exposes a `camera.*` entity (ONVIF, RTSP/generic
  camera, Frigate, ESPHome, motionEye, FFmpeg, ...)
- For gate access reviews: an integration that provides an **AI task** entity —
  OpenAI Conversation, Anthropic, Google Generative AI, Ollama (fully local),
  etc. (`ai_task.*` entity must exist)
- [HACS](https://hacs.xyz) optional (for one-click install)

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

After any method: **restart Home Assistant**, then continue with Quick start.

## Quick start

1. **Add the integration** — Settings → Devices & services → **Add
   integration** → **Watchmen** → select your cameras and a detection mode
   (Automatic is recommended).
2. **Tune the options** (⋮ → *Configure* on the Watchmen entry) — see the
   [Configuration](#configuration) table below.
3. **Check motion** — the `binary_sensor.*_motion` entity should turn on when
   something moves in front of the camera (walk in front of it to test).
4. **(Gate access) Set up AI review** — make sure an AI provider integration is
   configured and an `ai_task.*` entity exists; select it under *AI task
   entity* in the Watchmen options (or leave **Auto** to use HA's preferred AI
   task).
5. **Create the automation** — follow [Gate unlock
   blueprint](#gate-unlock-blueprint-generic-trigger--ai-approval--unlock)
   (UI or the YAML example).
6. **Test it** — trigger your automation's trigger (press the doorbell, fire
   the webhook, ...) and watch:
   - the automation **trace** (Settings → Automations → trace icon)
   - `sensor.*_last_access_decision` (state + `reason` attribute)
   - **Developer tools → Events** listening for `watchmen_gate_approved` /
     `watchmen_gate_denied`
   - **Settings → System → Logs** for `watchmen` messages

## Configuration

Settings → Devices & services → Watchmen entry → ⋮ → **Configure**:

| Option | Default | Meaning |
| --- | --- | --- |
| Detection mode | Automatic | Event / frame analysis / automatic |
| Frame analysis interval | 2 s | Snapshot polling rate (frame mode) |
| Motion sensitivity | 0.03 | Fraction of changed pixels that flags motion |
| Motion auto-off delay | 30 s | Motion clears after this long without detection |
| Known residents | *(empty)* | Comma separated names the AI may approve |
| AI task entity | *(auto)* | AI Task entity used for access reviews |
| Minimum confidence | 0.85 | Approvals below this become denials |
| Cooldown | 30 s | Minimum time between two AI reviews (anti-loop) |

Motion detection modes in detail:

- **Event mode** — no polling; Watchmen mirrors motion binary sensors that live
  on the *same device* as the camera (ONVIF `isMotionAlarm`, Frigate events,
  ...). Zero additional load, instant.
- **Frame analysis mode** — fetches a snapshot every *interval* seconds and
  compares it with the previous one at a small grayscale size (cheap). Use it
  when the camera has no motion events. The camera must support snapshots.
- **Automatic** — tries event mode first and silently falls back to frame
  analysis if no motion sensor is found.

## Entities

Per camera:

- `binary_sensor.*_motion` — unified motion (device class `motion`), works
  with `motion.detected` triggers and all motion automations
- `sensor.*_last_access_decision` — `approved` / `denied` with `person`,
  `confidence`, `reason`, `context`, `source_camera` and `reviewed_at`
  attributes

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
calls.

Access reviews use Home Assistant's built-in **AI Task** integration, so any
configured provider works (OpenAI, Anthropic, Google Gemini, Ollama for fully
local, ...). Make sure you have an AI provider configured in HA, then pick its
AI task entity in the Watchmen options (or rely on **Auto**).

### Events

Every recorded decision fires one event with the same payload as the service
response — subscribe to them for logging, dashboards or notifications:

| Event | When |
| --- | --- |
| `watchmen_gate_approved` | Review approved (confidence ≥ threshold) |
| `watchmen_gate_denied` | Review denied, low confidence, or on cooldown |

Payload: `approved`, `person`, `confidence`, `reason`, `context`, `camera`.

## Gate unlock blueprint (generic trigger → AI approval → unlock)

1. Copy `blueprints/automation/watchmen/gate_access.yaml` to
   `<config>/blueprints/automation/watchmen/`
   (git clone users: the file is inside the repo at
   `blueprints/automation/watchmen/`)
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

## Safety & security notes

- **Fail closed** on any AI/provider error — the automation stops, the gate
  stays locked
- **Confidence threshold** enforced twice (integration options + blueprint
  input)
- **Cooldown** (default 30 s) prevents unlock loops from a person lingering in
  view — after one review, repeat requests are denied until it elapses
- **Known residents allowlist** is embedded in the prompt for the model
- Decision audit trail in the `sensor.*_last_access_decision` attributes, the
  `watchmen_gate_*` events and an `INFO` log line per review
- Use `mode: queued` (the blueprint already does) so overlapping triggers are
  reviewed one at a time instead of racing
- This is a convenience feature, not a substitute for physical security:
  LLMs can be wrong, cameras can be obstructed, and a denied/error decision
  always leaves the lock engaged — keep a working manual override

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Motion binary sensor never turns on | No motion sensor on the camera's device (event mode) | Options → Detection mode → **Frame analysis**, or set to **Automatic** |
| Frame analysis misses motion | Camera scene barely changes / threshold too high | Lower *Motion sensitivity* (e.g. 0.02), shorten *interval* |
| Frame analysis too noisy | Shadows, trees, compression noise | Raise *Motion sensitivity* (e.g. 0.05), lengthen *auto-off delay* |
| `request_access` raises / automation stops with an error | No AI task entity or provider error | Configure an AI integration, check its `ai_task.*` entity exists, verify the option *AI task entity* |
| Everything is denied right after a success | Cooldown | Wait out the *Cooldown* window, or raise it if reviews come in bursts |
| Approval ignored by the blueprint | Confidence below the blueprint's *Minimum confidence* | Check `sensor.*_last_access_decision` attributes; lower the blueprint input or raise model confidence |
| Automation never fires | Trigger or blueprint inputs wrong | Open the automation **trace**; verify inputs nest under `use_blueprint` in YAML |
| Need details | — | **Settings → System → Logs**, filter for `watchmen` |

## Development

```bash
python -m venv venv && . venv/bin/activate
pip install ruff pytest pytest-homeassistant-custom-component
ruff check custom_components tests
python -m compileall custom_components tests
python -m pytest tests
```

The test suite covers config flow, event & frame-diff motion detection,
fail-closed access reviews, and end-to-end blueprint runs (trigger → AI →
gate unlock/deny).

## Roadmap

- [x] v0.1 — config flow, event-based + frame-diff motion, entities, events
- [x] v0.2 — `request_access` LLM decision service + blueprint
- [ ] v0.3 — snapshot archiving on motion
- [ ] v0.4 — dedicated person/face stage (LLM vision or local face model)

## License

MIT — see [LICENSE](LICENSE).

Issues and feature requests: https://github.com/AkashPanja/WatchMen/issues
