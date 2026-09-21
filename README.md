# Fermax Blue for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/bvis/fermax-blue-hass.svg)](https://github.com/bvis/fermax-blue-hass/releases)
[![Tests](https://github.com/bvis/fermax-blue-hass/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/bvis/fermax-blue-hass/actions/workflows/tests.yml)
[![CodeQL](https://github.com/bvis/fermax-blue-hass/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/bvis/fermax-blue-hass/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/github/license/bvis/fermax-blue-hass.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Home Assistant custom integration for **Fermax Blue** video door entry systems (DUOX PLUS / blueStream).

This integration simulates a Fermax Blue mobile app client, connecting to the Fermax cloud API and receiving real-time push notifications via Firebase Cloud Messaging when someone rings your doorbell.

## Features

- **Live video streaming** — Real-time video from the intercom camera (~720x480) with the intercom's audio, over WebRTC in the standard camera cards and the companion apps; MJPEG is still served. Opening the card wakes the intercom, and the card shows the last snapshot until the live picture arrives.
- **Talk-back** — Speak to the visitor from a card that captures the microphone; the first word answers the call (see [Live view and talk-back](#live-view-and-talk-back))
- **Answer from your phone** — A ready-made dashboard and notification blueprint: ring → notification with the visitor's photo → see, hear, talk and open the door from the companion app (see [Answer calls from your phone](#answer-calls-from-your-phone))
- **Camera preview** — Last captured frame persists across HA restarts, always visible in the camera card
- **Doorbell detection** — Real-time push notification when someone rings (via Firebase Cloud Messaging)
- **Door opening** — Open your building's door remotely (lock entity + button)
- **On-demand camera** — Triggers the intercom camera without a doorbell ring via auto-on
- **F1 auxiliary button** — Trigger the intercom's F1 function
- **Switch camera** — Step through the cameras of multi-camera installations while a live stream is up (the intercom only accepts the change inside a session)
- **Call guard** — Call the building's guard/janitor
- **Do Not Disturb** — Toggle DND mode per device (useful for night automations)
- **Photo caller control** — Enable/disable automatic visitor photo capture
- **Opening history** — Track who opened the door and when
- **Connection status** — Monitor if your intercom is online (entities go unavailable when offline)
- **WiFi signal** — Track the intercom's wireless signal strength
- **Notification control** — Enable/disable doorbell notifications
- **Media browser** — Browse doorbell photos and call recordings from the HA media browser
- **Diagnostics** — Built-in troubleshooting data (with redacted credentials)
- **Configurable polling** — Adjust the status polling interval (1-30 minutes)

## Supported Devices

Tested with:
- Fermax VEO-XL WiFi DUOX PLUS
- Fermax VEO-XS WiFi DUOX PLUS (REF: 9449)

Should work with any Fermax Blue-compatible intercom (devices that work with the Fermax Blue / DuoxMe mobile app).

## Installation

Requires Home Assistant **2025.12 or newer** (the live-view dependencies need the `av` version that ships with it).

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for "Fermax Blue" and install
5. Restart Home Assistant
6. Go to **Settings** > **Devices & Services** > **Add Integration** > **Fermax Blue**

### Manual Installation

1. Download the latest release from GitHub
2. Copy the `custom_components/fermax_blue` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant
4. Go to **Settings** > **Devices & Services** > **Add Integration** > **Fermax Blue**

## Configuration

The integration is configured through the Home Assistant UI:

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for **Fermax Blue**
4. Enter your Fermax Blue app credentials (see below)

The integration will automatically discover all paired devices on your account.

### Options

After setup, you can configure the integration options:

1. Go to **Settings** > **Devices & Services**
2. Click **Configure** on your Fermax Blue integration
3. Available options:
   - **Polling interval** (1-30 minutes, default: 5)
   - **Recording retention** (1-90 days, default: 10) — auto-deletes older call recordings
   - **Audio file path** — WAV/MP3 file played by the auto-respond call mode (e.g., `/config/media/mi_mensaje.wav`)

Two more settings live on the device as entities rather than in the options dialog: `select.<name>_call_mode` chooses what a ring does (*notify only*, *record* the call, or *auto-respond*: answer, play the audio file through the intercom speaker and record), and `number.<name>_stream_duration` sets how long a stream session lasts (10-120 s, default 30).

### API Credentials

During setup, after entering your Fermax Blue username and password, you will be asked for the **API and Firebase credentials**. These values are needed to communicate with Fermax's servers and receive push notifications.

The integration requires:
- **Fermax API**: Auth URL, Base URL, Auth Basic header (OAuth client credentials)
- **Firebase**: API Key, Sender ID, App ID, Project ID, Package Name

A `credentials.example.json` file is included as a template. Copy it to `credentials.json` and fill in your values.

#### How to Obtain Credentials

##### Firebase credentials (from the APK)

The Firebase credentials can be extracted automatically from the official Fermax Blue Android APK:

1. **Download the Fermax Blue APK** from your device or a trusted source (e.g., Softonic)
2. **Run the extraction script:**
   ```bash
   make extract-credentials APK=/path/to/fermax-blue.apk
   ```
   Or directly: `python scripts/extract_credentials.py /path/to/fermax-blue.apk`
3. The script extracts Firebase credentials from `resources.arsc` and API URLs from the binary. The OAuth `Basic` header is only generated from the app sources, so it needs a sources directory, either as the target itself or sitting alongside the app package.

   Run against a bare app package and `fermax_auth_basic` is reported as missing. That is deliberate: `Basic` literals do exist in the binary, but they belong to telemetry, and the login endpoint answers `invalid_client` for them. The script names the one it found and explains why it did not keep it, rather than saving a header that cannot work.

The script reliably finds: `firebase_api_key`, `firebase_sender_id`, `firebase_app_id`, `firebase_project_id`, `firebase_package_name`, `fermax_auth_url`, and `fermax_base_url`.

##### OAuth client credentials (fermax_auth_basic)

The OAuth `client_id` and `client_secret` are combined into the `Basic` auth header used for login. Where the app keeps them **depends on the app version**, and `scripts/extract_credentials.py` handles both layouts when a sources directory is available:

**App 4.3.0 and earlier — encrypted constants:**

- `com.fermax.blue.app.core.utils.OAuthUtils.getAuthorizationHeader()` (holds the AES key)
- `com.fermax.blue.app.data.remoteconfig.Urls.clientId()`
- `com.fermax.blue.app.data.remoteconfig.Urls.clientSecret()`

**App 4.3.4 and later — plain string constants:**

- `BuildConfig.OAUTH_CLIENT_ID`
- `BuildConfig.OAUTH_CLIENT_SECRET`

The encrypted layout is preferred when both are present. The production values should match the production URLs `oauth-pro-duoxme.fermax.io` and `pro-duoxme.fermax.io`.

Several modules can ship a file named `Urls.java`; the script reads the one that actually declares `clientId()` / `clientSecret()` rather than whichever the directory walk finds first. If it still cannot generate the header, it reports what the scan saw — how many `Urls.java` files exist and how many declare those accessors, whether `OAuthUtils.java` and its key were found, and whether any `BuildConfig.java` carries the OAuth constants. That tells you whether the app changed layout again or the sources were simply never scanned (running against the app package instead of the sources directory).

Do not use unrelated `Basic` headers from tracing or observability code. In particular, `TraceManagerOtelImpl.java`, `/monitoring/v1/traces` and the **`BuildConfig.TRACING_BASIC_AUTH`** constant refer to telemetry, not OAuth login, and those headers will cause OAuth `invalid_client` errors. `TRACING_BASIC_AUTH` is especially easy to confuse because it sits in the same `BuildConfig.java` as the OAuth constants; the script skips values assigned to telemetry-named constants for exactly this reason.

Never publish `credentials.json`, generated `Basic` headers, Firebase keys, access tokens, refresh tokens, usernames, or passwords.

The OAuth credentials were identified thanks to the work of the open-source Fermax community:

- [fermax-blue-intercom](https://github.com/marcosav/fermax-blue-intercom) by @marcosav
- [hass-bluecon](https://github.com/AfonsoFGarcia/hass-bluecon) by @AfonsoFGarcia and the [fork](https://github.com/patrikulus/hass-bluecon) by @patrikulus
- [HASS-BlueCon](https://github.com/cvc90/HASS-BlueCon) by @cvc90
- [Fermax-Blue-Intercom](https://github.com/cvc90/Fermax-Blue-Intercom) by @cvc90

This integration exists thanks to the work of these developers. If you find this project useful, please consider starring their repositories.

##### Manual extraction (fallback)

If the script doesn't find all values, search the app sources manually:

1. **API URLs**: search for `oauth/token` and `fermax.io` in `Urls.java`
2. **OAuth Basic header**: on app 4.3.4+, read `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` straight from `BuildConfig.java` — they are stored in plain text. On app 4.3.0 and earlier, inspect `OAuthUtils.getAuthorizationHeader()` and `Urls.clientId()` / `Urls.clientSecret()`, select the production environment that corresponds to `oauth-pro-duoxme.fermax.io` / `pro-duoxme.fermax.io`, and decrypt the encrypted byte arrays from those methods. Either way, URL-encode both values, join them as `client_id:client_secret`, then base64 encode that string and prefix it with `Basic `.

   If you already have the two OAuth values, let the script build the header for you instead of doing it by hand:

   ```bash
   python scripts/extract_credentials.py --client-id <CLIENT_ID> --client-secret <CLIENT_SECRET>
   ```

   It prints the ready-to-paste `fermax_auth_basic` value. Ignore telemetry headers from `TraceManagerOtelImpl.java` and the `TRACING_BASIC_AUTH` constant.
3. **Firebase**: found in `google-services.json` inside the APK:
   - `firebase_api_key` → `client[0].api_key[0].current_key`
   - `firebase_sender_id` → `project_info.project_number`
   - `firebase_app_id` → `client[0].client_info.mobilesdk_app_id`
   - `firebase_project_id` → `project_info.project_id`
   - `firebase_package_name` → `client[0].client_info.android_client_info.package_name`

> **Legal note:** These credentials are part of a publicly distributed application. Extracting configuration from software you own as a paying customer for personal interoperability use is a legitimate exercise of your consumer rights.

### Dedicated User for Doorbell Notifications (Recommended)

Fermax Blue only allows **one active push notification token per user**. If you use your main account for the integration, your mobile app will stop receiving doorbell notifications (or vice versa).

To solve this, **create a dedicated user** for the integration:

1. Open the **Fermax Blue app** on your phone
2. Go to **Settings** > **Users** > **Invite user**
3. Enter a new email address (e.g., `yourname+ha@gmail.com` — Gmail `+` aliases work)
4. The invited user will receive an email with a registration link
5. Open the link, **download the Fermax Blue app** on any phone, and complete the registration with a password
6. Once registered, you can uninstall the app — the account is now active
7. Use this new account's credentials when configuring the integration in Home Assistant

This way, your main account keeps receiving notifications on your phone, and the integration receives them independently via its own account.

> **Note:** If you skip this step and use your main account, the integration will still work for door opening, device status, and camera — but real-time doorbell notifications may conflict with your mobile app.

## Entities

For each paired intercom device, the integration creates:

| Entity | Type | Description |
|--------|------|-------------|
| `camera.<name>_visitor` | Camera | Live video with intercom audio over WebRTC (MJPEG also served); last frame as preview when idle |
| `event.<name>_doorbell` | Event | Fires when someone rings the doorbell |
| `event.<name>_door_opened` | Event | Fires when a door is successfully opened |
| `event.<name>_camera_on` | Event | Fires when camera preview / live stream starts |
| `binary_sensor.<name>_connection` | Binary Sensor | Device connectivity (entities go unavailable when disconnected) |
| `lock.<name>_<door>_lock` | Lock | Lock/unlock (open) the door |
| `button.<name>_<door>_open` | Button | One-press door opening |
| `button.<name>_camera_preview` | Button | Start camera preview / live stream |
| `button.<name>_f1` | Button | F1 auxiliary function |
| `button.<name>_video_source` | Button | Switch the intercom to the next camera |
| `button.<name>_call_guard` | Button | Call the building's guard/janitor |
| `sensor.<name>_wifi_signal` | Sensor | WiFi signal strength (0-4 bars) |
| `sensor.<name>_status` | Sensor | Device activation status |
| `sensor.<name>_last_opening` | Sensor | Last door opening timestamp (with user, door, guest attributes) |
| `sensor.<name>_last_call` | Sensor | Last call/doorbell ring timestamp (with call_id, answered, recent_calls count) |
| `switch.<name>_notifications` | Switch | Enable/disable push notifications |
| `switch.<name>_dnd` | Switch | Do Not Disturb mode |
| `switch.<name>_photo_caller` | Switch | Enable/disable automatic visitor photos |
| `switch.<name>_ring_preview` | Switch | Enable/disable live view-only preview on ring without answering the call (see [Ring preview](#ring-preview)) |
| `select.<name>_call_mode` | Select | What a ring does: notify only, record the call, or auto-respond with the configured audio file |
| `number.<name>_stream_duration` | Number | Length of a stream session in seconds (10-120, default 30) |

### Ring preview

When the `ring_preview` switch is on, a doorbell ring starts a view-only video stream. The camera shows the current visitor within a few seconds while the panel keeps ringing. The call isn't answered and no audio is sent or received.

- The switch only has an effect in `notify_only` call mode. `record` and `auto_respond` already start a stream by answering the call.
- The stream stops after the configured stream duration, capped by a server-side preview limit of about 29 seconds.
- Like any stream session, the preview is recorded to the media folder (subject to the recording retention setting) and updates the last visitor photo. Every ring leaves a snapshot without answering.

### Live view and talk-back

The camera advertises a WebRTC stream served through Home Assistant's bundled go2rtc. Any card that plays a camera stream — the standard picture-entity card with `camera_view: live`, the picture-glance card, the more-info dialog, the companion apps — shows the live video **with the intercom's audio**.

- **Opening the card wakes the intercom.** While the intercom is idle, the card shows the last snapshot and the integration requests a preview, exactly like pressing `button.<name>_camera_preview`; the live picture arrives a few seconds later (up to half a minute on a cold panel). If a session is already running (a ring preview, a recording), the viewer joins it.
- **The session ends on its own**, after the configured stream duration (or the 90 s conversation once you have talked), exactly like in the app. The card then keeps the last snapshot; close and reopen it, or press the camera preview button, to watch again.
- **Talking back needs a card that captures the microphone.** Home Assistant's own cards play video and audio only for now. The [WebRTC card](https://github.com/AlexxIT/WebRTC) does capture it:

  ```yaml
  type: custom:webrtc-camera
  entity: camera.<name>_visitor
  media: video,audio,microphone
  ```

  Pressing the microphone button **answers the call**: the first packet of voice picks up, as the attend button in the app does, and the intercom's audio starts flowing. An answered session lasts the conversation time the server allows (90 seconds by default) rather than the preview limit.
- **Microphone access requires HTTPS.** Browsers and the companion apps refuse to capture audio on plain `http://` addresses, so use your HTTPS URL (Nabu Casa, a reverse proxy) when you want to talk.
- **Hardware budget.** The panel's own H264 is forwarded to the viewer and written to the recording without re-encoding, and frames are only decoded for the still image (at every keyframe, about every 2 s) unless an MJPEG client is connected. On a Raspberry Pi 4 a live session costs about a third of a core; a WebRTC viewer with microphone adds about 0.7 of a core, an MJPEG viewer about 0.4.
- Everything else is unchanged: the MJPEG stream, snapshots, the media browser recordings and the `fermax_blue.send_audio` service keep working, and the session is recorded as before.

## Answer calls from your phone

The flow the integration is built for: someone rings, your phone shows a notification with the visitor's photo, you tap it and, inside the Home Assistant companion app, you see and hear the visitor, talk to them and open the door. Two files in [`blueprints/`](blueprints/) set it up; nothing is installed automatically, so your dashboards stay yours.

**Requirements**

- Home Assistant reachable over **HTTPS** (Nabu Casa, a reverse proxy, DuckDNS…): browsers and the companion apps refuse microphone access on plain `http://`.
- The **WebRTC card** from HACS ([AlexxIT/WebRTC](https://github.com/AlexxIT/WebRTC)), set up with **its own go2rtc** (leave the URL empty in its setup dialog). Home Assistant's bundled go2rtc cannot be shared: it listens on a private socket only. The card is what captures the microphone; Home Assistant's own cards play video and audio only.

**Dashboard** — [`blueprints/fermax_answer_dashboard.yaml`](blueprints/fermax_answer_dashboard.yaml)

1. **Settings** > **Dashboards** > **Add dashboard** > *New dashboard from scratch*, title `Intercom`, URL `lovelace-intercom`.
2. Open it > pencil > three dots > **Raw configuration editor**, paste the file, replace `DEVICE_NAME` and `DOOR` with your entity names.

The view has a clock, the live video with sound (opening it wakes the intercom; the **microphone button answers the call**), buttons to open the door, F1 and switch camera, and a status column. The time is not burnt into the video on purpose: that would cost a full re-encode of the stream.

**Notification** — [`blueprints/fermax_doorbell_answer.yaml`](blueprints/fermax_doorbell_answer.yaml)

1. Copy it to `config/blueprints/automation/fermax_blue/`, then **Settings** > **Automations** > **Create automation** > **Use blueprint** > *Fermax: Answer from your phone*.
2. Pick the doorbell event, the camera, your phone's notification service and, optionally, the door button. The dashboard path defaults to `/lovelace-intercom/call`.

The notification is high priority (critical on iOS, high importance on Android, both optional), carries the latest picture, opens the view when tapped and offers two actions: *See and talk* (opens the view) and *Open door* (opens without entering the app; accepted for three minutes after the ring).

Grant the microphone when the app asks for it the first time; on iOS the prompt can come back per session.

## Dashboard Card

A ready-to-use dashboard card template is included in [`blueprints/fermax_dashboard_card.yaml`](blueprints/fermax_dashboard_card.yaml). It provides a complete intercom control panel with:

<img width="1041" height="866" alt="image" src="https://github.com/user-attachments/assets/59b0f4cc-67ad-495e-bd34-b03ca7e56018" />

- Live camera / preview image
- Door open + camera preview buttons
- F1 + call guard buttons
- Doorbell event, connection status, door lock
- DND, photo caller, notification switches
- WiFi signal + last opening info

**To use it:**

1. Install [mushroom-cards](https://github.com/piitaya/lovelace-mushroom) via HACS (for status indicators)
2. Copy the content of `blueprints/fermax_dashboard_card.yaml`
3. Edit your dashboard > Add card > Manual YAML
4. Paste and replace `DEVICE_NAME` and `DOOR` with your entity names

> **Tip:** To find your entity IDs, go to **Settings** > **Devices & Services** > **Fermax Blue** > click your device.

## Call Recordings

Every video stream session is automatically recorded to MP4 in `/config/media/fermax_recordings/`: the intercom's own H264 video as it was received, plus the call audio (both directions when you talk back) as AAC. Doorbell visitor photos are saved as JPG in the same directory. Files are named with timestamps (e.g., `2026-04-06_13-00-00.mp4`, `2026-04-06_13-00-00_photo.jpg`).

Recordings and photos are automatically deleted after the retention period (default: 10 days, configurable in options).

### Media browser

All recordings and photos are browsable directly from the HA media browser via **Media** > **Fermax Blue**. Files are listed newest-first with thumbnails for photos and playback for videos.

You can also access them from the dashboard via the **Grabaciones** button, or browse **Media** > **Local media** > **fermax_recordings**.

## Services

### `fermax_blue.send_audio`

Send audio to the intercom speaker during an active video stream.

```yaml
# Send a pre-recorded audio file
service: fermax_blue.send_audio
data:
  audio_file: /config/media/mi_mensaje.wav

# Or send a TTS message
service: fermax_blue.send_audio
data:
  message: "Hola, ahora bajo"
  language: es
```

## Automation Blueprints

### Doorbell notification

A doorbell notification blueprint is available at [`blueprints/fermax_doorbell_notification.yaml`](blueprints/fermax_doorbell_notification.yaml). For the full see-hear-talk-open flow use [`blueprints/fermax_doorbell_answer.yaml`](blueprints/fermax_doorbell_answer.yaml) instead (see [Answer calls from your phone](#answer-calls-from-your-phone)).

### Cast doorbell to Google Nest

Show doorbell video on a Nest Hub and announce on speakers: [`blueprints/fermax_cast_doorbell.yaml`](blueprints/fermax_cast_doorbell.yaml). Supports optional auto-response to the intercom via TTS.

**To install:**

1. Copy `blueprints/fermax_doorbell_notification.yaml` to your HA `config/blueprints/automation/fermax_blue/` folder
2. Go to **Settings** > **Automations** > **Create Automation** > **Use Blueprint**
3. Select "Fermax: Doorbell notification"
4. Configure your doorbell event entity, camera, and notification service

The blueprint supports:
- Mobile push notification on doorbell ring
- Optional camera snapshot attachment
- Customizable title and message

## Automation Examples

### Flash lights when doorbell rings

```yaml
automation:
  - alias: "Flash lights on doorbell"
    trigger:
      - platform: state
        entity_id: event.fermax_your_home_doorbell
        not_from:
          - unknown
          - unavailable
        not_to:
          - unknown
          - unavailable
    action:
      - service: light.turn_on
        target:
          entity_id: light.hallway
        data:
          flash: long
```

### Enable Do Not Disturb at night

```yaml
automation:
  - alias: "DND at night"
    trigger:
      - platform: time
        at: "23:00:00"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.fermax_your_home_dnd

  - alias: "Disable DND in the morning"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.fermax_your_home_dnd
```

## How It Works

1. **Authentication**: The integration authenticates with the Fermax Blue cloud API (`pro-duoxme.fermax.io`) using your account credentials
2. **Device Discovery**: It fetches all paired intercom devices and their accessible doors
3. **Firebase Registration**: It registers a Firebase Cloud Messaging client (simulating the mobile app) to receive real-time doorbell push notifications
4. **Camera Preview**: The auto-on feature triggers the intercom camera. A push notification arrives with the media room ID and signaling server URL
5. **Video Streaming**: The integration connects to the Fermax mediasoup SFU via Socket.IO, negotiates WebRTC transport, and receives live video frames (~720x480) that are served as MJPEG to the HA frontend and republished, together with the intercom's audio, over WebRTC through Home Assistant's bundled go2rtc. A viewer's microphone travels the opposite way into the call
6. **Push Notifications**: When someone rings your doorbell, Fermax sends a push notification via Firebase, which the integration receives instantly and acknowledges
7. **Frame Persistence**: The last video frame is saved to disk so the camera card always shows a preview, even after HA restarts
8. **Polling**: Device status (connection, signal, opening history) is polled at a configurable interval (default: 5 minutes)
9. **Retry Logic**: API calls are automatically retried with exponential backoff on transient errors (5xx, connection failures)

## Troubleshooting

### "Invalid credentials" error
Make sure you're using the same email and password you use in the Fermax Blue mobile app. The password is case-sensitive.

If Home Assistant logs show OAuth `invalid_client`, the problem is usually the OAuth client credentials (`fermax_auth_basic`), not your Fermax user email/password. Re-run the extraction against a sources directory so the script can generate the header from the real OAuth constants — `OAuthUtils.java` plus `Urls.clientId()` / `Urls.clientSecret()` on app 4.3.0 and earlier, or `BuildConfig.OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` on 4.3.4 and later. Do not use `Basic` headers found near `TraceManagerOtelImpl.java`, `/monitoring/v1/traces`, OpenTelemetry, tracing, or observability code, nor the `TRACING_BASIC_AUTH` constant in `BuildConfig.java`.

If logs show `invalid_grant` or Home Assistant reports `invalid_auth`, the problem is more likely the Fermax account credentials, account state, or an account without access to paired devices.

### Doorbell notifications not working
The integration needs to register with Firebase Cloud Messaging. This happens automatically but can take a few minutes on first setup. Check the Home Assistant logs for `fermax_blue` entries.

### Camera shows no image
Press the "Camera preview" button to trigger the first stream. After that, the last frame will be saved and shown as preview even after restarts.

### No sound, or the microphone does nothing
Sound comes with the WebRTC stream, so the card has to play the stream rather than the still image: `camera_view: live` on a picture-entity card, or the more-info dialog. Home Assistant's own cards do not capture the microphone yet; use a card that does (see [Live view and talk-back](#live-view-and-talk-back)) and open Home Assistant over **HTTPS** — browsers and the companion apps refuse microphone access on plain `http://`. Check the browser's site permissions if the microphone button stays muted.

### Live video not working after upgrading
Live video is powered by `pymediasoup`/`aiortc`. On v0.16.8 these were temporarily **optional** because no `aiortc` release was compatible with the `av>=17` shipped by Home Assistant 2026.7 — live streaming was disabled on those systems. Since `aiortc` 1.15.0 (compatible with `av 17`) they are regular requirements again and live video works on all supported HA versions.

If live video is unavailable and the logs show a WARNING about missing streaming dependencies, restart Home Assistant so it installs the integration requirements. Everything else — doorbell detection, door opening, visitor photos, F1, DND, photo caller, call log — works normally even without them.

### Diagnostics
For troubleshooting, you can download diagnostics from **Settings** > **Devices & Services** > **Fermax Blue** > **3 dots menu** > **Download diagnostics**. Credentials are automatically redacted.

## Local Testing (CLI)

You can test every API feature locally without Home Assistant using the interactive CLI tool:

```bash
# Interactive mode (will prompt for credentials)
make cli

# Or pass credentials via environment variables
FERMAX_USER=your@email.com FERMAX_PASS=yourpassword make cli
```

This launches a Docker container with a menu-driven interface to test door opening, F1, call guard, DND, photo caller, opening history, camera preview, call log, and raw API calls.

No local Python installation needed — everything runs in Docker.

## Development

All development tools run via Docker — no local Python dependencies needed. Only Docker is required.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

```bash
make check          # Run all checks (lint + format + type-check + dead-code + tests)
make lint           # Ruff linting (E, F, W, I, N, UP, B, A, SIM, TCH)
make format         # Auto-format code
make format-check   # Verify formatting (CI mode)
make typecheck      # Mypy type checking
make deadcode       # Vulture dead code analysis
make test           # Pytest with coverage (min 95%)
make cli            # Interactive API tester
make pre-push       # Full CI replica on Python 3.13 + 3.14 (same as GitHub Actions)
```

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

## Roadmap

Features available in the Fermax Blue mobile app that are not yet implemented:

| Feature | Complexity | Description |
|---------|-----------|-------------|
| ~~**Two-way audio**~~ | ~~High~~ | ✅ Implemented — live talk-back from a microphone-capable card over WebRTC, plus `fermax_blue.send_audio` (file or TTS) and the auto-response configurable in options |
| ~~**Switch camera during call**~~ | ~~Low~~ | ✅ Implemented — `button.<name>_video_source`, addressed to the active session while a stream is up |
| ~~**F1 during active call**~~ | ~~Low~~ | ✅ Implemented — the F1 button follows the stream automatically |
| **Guest management** | Medium | Add, remove, and authorize guest users for the intercom via the API |
| **Delete call history** | Low | Clear call log entries (`DELETE /callmanager/api/v1/callregistry/participants`) |
| **Delete opening history** | Low | Clear door opening records (`DELETE /rexistro/api/v1/opendoorregistry`) |
| **Rename house/doors** | Low | Change the display name of the house or doors (`PATCH /pairing/api/v3/pairings/{id}/tag/`) |

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Acknowledgments

This integration would not be possible without the work of the Fermax open-source community:

- **[@marcosav](https://github.com/marcosav)** — [fermax-blue-intercom](https://github.com/marcosav/fermax-blue-intercom): original script for Fermax Blue API interaction and OAuth credential discovery
- **[@AfonsoFGarcia](https://github.com/AfonsoFGarcia)** — [hass-bluecon](https://github.com/AfonsoFGarcia/hass-bluecon): first Home Assistant integration for Fermax Blue, BlueCon library
- **[@patrikulus](https://github.com/patrikulus)** — [hass-bluecon fork](https://github.com/patrikulus/hass-bluecon): working fork with improved authentication
- **[@cvc90](https://github.com/cvc90)** — [HASS-BlueCon](https://github.com/cvc90/HASS-BlueCon) and [Fermax-Blue-Intercom](https://github.com/cvc90/Fermax-Blue-Intercom): community maintenance and documentation
- **[firebase-messaging](https://github.com/sdb9696/firebase-messaging)** by @sdb9696: FCM push notification library that enables real-time doorbell detection

Thanks to the collaborative work of this community, the OAuth credentials needed for third-party integrations are publicly available. Without their effort, projects like this one would not be possible.

## Disclaimer

This integration is not affiliated with or endorsed by Fermax. It uses unofficial APIs that may change at any time. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file.
