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
- **Identifies people by face**: enroll photos of your residents, then let a
  triggered `watchmen.identify_person` call pick the clearest face around the
  event, zoom into it and match it against the gallery — returning the person's
  **name and a confidence percentage**.
- Talks to any **LLM provider**: Home Assistant's built-in AI Task (default) or
  direct APIs — **OpenAI, Claude, Groq, OpenRouter** (local gateways supported
  via base URL override).

## Requirements

- Home Assistant **2024.11** or newer
- Any camera integration that exposes a `camera.*` entity (ONVIF, RTSP/generic
  camera, Frigate, ESPHome, motionEye, FFmpeg, ...)
- For AI reviews and identification, one of:
  - an integration that provides an **AI task** entity — OpenAI Conversation,
    Anthropic, Google Generative AI, Ollama (fully local), etc.
    (`ai_task.*` entity must exist), **or**
  - a direct provider API key (OpenAI / Claude / Groq / OpenRouter) entered in
    the Watchmen options
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
4. **(Gate access) Set up AI review** — pick an *LLM provider* in the options:
   **AI Task** (uses any configured HA AI provider) or a direct provider
   (OpenAI / Claude / Groq / OpenRouter — enter the API key there).
5. **(Optional) Enroll faces** — add one or more photos per resident with the
   `watchmen.add_face` service (see [Face
   identification](#face-identification)) so triggers can tell people apart.
6. **Create the automation** — follow [Gate unlock
   blueprint](#gate-unlock-blueprint-generic-trigger--ai-approval--unlock)
   or the [Person identification blueprint](#person-identification-blueprint-trigger--match--action)
   (UI or the YAML example).
7. **Test it** — trigger your automation's trigger (press the doorbell, fire
   the webhook, ...) and watch:
   - the automation **trace** (Settings → Automations → trace icon)
   - `sensor.*_last_access_decision` (state + `reason` attribute)
   - **Developer tools → Events** listening for `watchmen_gate_approved` /
     `watchmen_gate_denied` / `watchmen_person_identified`
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
| LLM provider | AI Task | `AI Task`, `OpenAI`, `Claude`, `Groq` or `OpenRouter` |
| LLM API key | *(empty)* | API key for direct providers (never logged) |
| LLM model | *(provider default)* | Model id override (e.g. `gpt-4o`, `llama-3.3-70b-versatile`) |
| Base URL | *(provider default)* | API base URL override (local gateways/proxies) |
| AI task entity | *(auto)* | AI Task entity used when the provider is *AI Task* |
| Minimum confidence | 0.85 | Gate approvals below this become denials |
| Cooldown | 30 s | Minimum time between two AI reviews (anti-loop) |
| Face buffer interval | 0 (off) | Seconds between pre-roll snapshots buffered for identification |
| Frames back / forward | 4 / 2 | Buffered frames before / live frames after an identification trigger |
| Match threshold | 70 % | Face matches below this percentage count as unidentified |

### LLM providers

- **AI Task** (default) — uses Home Assistant's built-in `ai_task.generate_data`
  service, so any configured AI provider integration works (OpenAI
  Conversation, Anthropic, Google Gemini, Ollama for fully local, ...). Pick its
  entity under *AI task entity*, or leave **Auto**.
- **OpenAI / Claude / Groq / OpenRouter** — Watchmen calls the provider API
  directly with the key stored in the entry options (HA options storage, never
  written to logs). *Base URL* lets you point at OpenAI-compatible gateways
  (LiteLLM, local proxies, ...).

All LLM calls (access reviews **and** face identification) go through the
selected provider. Errors fail closed in both pipelines.

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

Access reviews run through the configured **LLM provider** (AI Task by
default, or direct OpenAI / Claude / Groq / OpenRouter — see
[LLM providers](#llm-providers)).

### Events

Every recorded decision fires one event with the same payload as the service
response — subscribe to them for logging, dashboards or notifications:

| Event | When |
| --- | --- |
| `watchmen_gate_approved` | Review approved (confidence ≥ threshold) |
| `watchmen_gate_denied` | Review denied, low confidence, or on cooldown |
| `watchmen_person_identified` | An identification ran (matched or not) |

Gate payload: `approved`, `person`, `confidence`, `reason`, `context`,
`camera`. Identification payload: `name`, `matched`, `confidence` (percent),
`face_visible`, `reason`, `frame_index`, `candidates`, `context`, `camera`.

## Face identification

Watchmen can tell **who** is at the camera when something triggers — fail
closed, without local face-detection models:

1. **Enroll** one or more photos per person (multiple photos improve the
   match). Photos are stored under `<config>/media/watchmen/faces/<name>/` and
   can come from a camera snapshot or a base64 image.
2. **(Recommended)** set *Face buffer interval* (e.g. `1 s`) so Watchmen keeps
   a rolling buffer of recent frames — identification then checks the frames
   *before* the trigger (plus optional frames after it), like a doorbell
   camera. With buffering off, a fresh snapshot is used as fallback.
3. On trigger, `watchmen.identify_person` runs a two-stage LLM pipeline:
   - **Select** — the model picks the frame with the clearest face and returns
     its bounding box;
   - **Crop & zoom** — Watchmen crops and upscales that region locally (PIL,
     no OpenCV);
   - **Match** — the crop is compared against the enrolled gallery, returning
     an exact enrolled `name` and a `confidence` percentage.
4. Names **not in the gallery** are discarded, matches below *Match threshold*
   (default 70 %) count as unidentified, model errors raise — matched actions
   only ever run on a verified, confident match.

### Services: face gallery & identification

```yaml
# Enroll from a camera snapshot (or use `image: <base64>` instead of `camera:`)
service: watchmen.add_face
data:
  name: Alice
  camera: camera.front_door

# List enrolled people and photo counts
service: watchmen.list_faces
response_variable: faces          # {"faces": {"Alice": 2}}

# Forget one person (all photos)
service: watchmen.remove_face
data:
  name: Alice
```

```yaml
# Identify around a trigger — frame window + LLM match
service: watchmen.identify_person
data:
  camera: camera.front_door
  context: doorbell pressed
  frames_back: 4        # optional (defaults to the option)
  frames_forward: 2     # optional
response_variable: watchmen_person
```

Response:

```yaml
name: Alice             # enrolled name, or null when unidentified
matched: true           # name known AND confidence >= Match threshold
confidence: 87.0        # percentage (0–100)
face_visible: true      # false when no face could be located
reason: Facial features match enrolled Alice
frame_index: 1          # which candidate frame was used (-1 = none)
candidates: 6           # frames analyzed around the trigger
context: doorbell pressed
camera: camera.front_door
```

Fails closed: `no_faces_enrolled` (empty gallery), `no_frames_captured`
(camera gave nothing), and provider errors all raise; the
`watchmen_person_identified` event fires on every completed identification
(including `face_visible: false` outcomes).

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

## Person identification blueprint (trigger → match → action)

1. Copy `blueprints/automation/watchmen/identify_person.yaml` to
   `<config>/blueprints/automation/watchmen/`
2. Settings → Automations → **Create automation** → **Use blueprint**
3. Choose **Watchmen person identification** and configure:
   - **Trigger** — doorbell, button, webhook, motion, ...
   - **Camera** — where faces should be recognized
   - **Frames back / forward** — window around the trigger (enable the
     *Face buffer interval* option for true pre-trigger frames)
   - **Minimum match percentage** — matches below it count as unidentified
   - **Matched actions** / **Unmatched actions** — e.g. announce the name vs
     a generic greeting

Flow:

```
trigger → watchmen.identify_person (buffer frames → pick face → zoom → match)
       → matched & >= minimum % → matched actions   (watchmen_person has name)
       → nobody / low % / error  → unmatched actions (fail closed)
```

Or as YAML (inputs nest under `use_blueprint`):

```yaml
alias: Doorbell face identification
use_blueprint:
  path: watchmen/identify_person.yaml
  input:
    trigger:
      - platform: state
        entity_id: binary_sensor.doorbell
        to: "on"
    camera: camera.front_door
    context_note: doorbell pressed
    frames_back: 4
    frames_forward: 2
    min_confidence: 70
    matched_actions:
      - action: notify.notify
        data:
          message: "Watchmen: {{ watchmen_person.name }} ({{ watchmen_person.confidence }}%)"
    unmatched_actions:
      - action: notify.notify
        data:
          message: Watchmen: unidentified person at the door
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
  `watchmen_gate_*` / `watchmen_person_identified` events and an `INFO` log
  line per review/identification
- Face matching only ever returns **enrolled names** — unknown faces and
  sub-threshold confidences are reported as unidentified (`matched: false`)
- Direct provider **API keys** live in Home Assistant's options storage and
  are never included in log output or error messages
- Temporary capture frames are written under `<config>/media/watchmen/captures/`
  only for the duration of one identification and deleted afterwards
- Use `mode: queued` (both blueprints already do) so overlapping triggers are
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
| `identify_person` raises *no faces enrolled* | Gallery is empty | Enroll at least one photo with `watchmen.add_face` |
| Identification always *unidentified* | No pre-roll frames / weak match / threshold too high | Set *Face buffer interval* to ~1 s, improve lighting, add more photos per person, lower *Match threshold* |
| Direct provider errors (HTTP 401 / "api key") | Missing or invalid key, wrong provider selected | Options → *LLM provider* + *LLM API key*; check *Base URL* for gateways |
| *No frames could be captured* | Camera snapshot failing at trigger time | Test `camera.snapshot`/`camera.*` state; check camera availability |
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
fail-closed access reviews, face enrollment + the identification pipeline
(buffer → pick → crop/zoom → match), direct LLM providers (request shapes,
fail-closed errors), and end-to-end blueprint runs for both blueprints
(trigger → AI → gate unlock/deny, trigger → identify → matched/unmatched).

## Roadmap

- [x] v0.1 — config flow, event-based + frame-diff motion, entities, events
- [x] v0.2 — `request_access` LLM decision service + gate blueprint
- [ ] v0.3 — snapshot archiving on motion
- [x] v0.4 — face identification (gallery, frame buffer, crop/zoom match) +
  direct LLM providers (OpenAI, Claude, Groq, OpenRouter) +
  identification blueprint
- [ ] v0.5 — notification actions/UI cards, multi-person results, snapshot
  archiving UI

## License

MIT — see [LICENSE](LICENSE).

Issues and feature requests: https://github.com/AkashPanja/WatchMen/issues
