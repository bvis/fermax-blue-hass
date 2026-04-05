# Fermax Blue for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant custom integration for **Fermax Blue** video door entry systems (DUOX PLUS / blueStream).

This integration simulates a Fermax Blue mobile app client, connecting to the Fermax cloud API and receiving real-time push notifications via Firebase Cloud Messaging when someone rings your doorbell.

## Features

- **Doorbell detection** — Real-time push notification when someone rings (via Firebase Cloud Messaging)
- **Door opening** — Open your building's door remotely (lock entity + button)
- **Camera preview** — On-demand camera view via auto-on (triggers the intercom camera without a doorbell ring)
- **Visitor camera** — View the last captured visitor photo
- **F1 auxiliary button** — Trigger the intercom's F1 function
- **Call guard** — Call the building's guard/janitor
- **Do Not Disturb** — Toggle DND mode per device (useful for night automations)
- **Photo caller control** — Enable/disable automatic visitor photo capture
- **Opening history** — Track who opened the door and when
- **Connection status** — Monitor if your intercom is online (entities go unavailable when offline)
- **WiFi signal** — Track the intercom's wireless signal strength
- **Notification control** — Enable/disable doorbell notifications
- **Diagnostics** — Built-in troubleshooting data (with redacted credentials)
- **Configurable polling** — Adjust the status polling interval (1–30 minutes)

## Supported Devices

Tested with:
- Fermax VEO-XL WiFi DUOX PLUS
- Fermax VEO-XS WiFi DUOX PLUS (REF: 9449)

Should work with any Fermax Blue-compatible intercom (devices that work with the Fermax Blue / DuoxMe mobile app).

## Installation

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
3. Adjust the **polling interval** (1–30 minutes, default: 5)

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
| `binary_sensor.<name>_connection` | Binary Sensor | Device connectivity (entities go unavailable when disconnected) |
| `event.<name>_doorbell` | Event | Fires `ring` event when someone rings the doorbell |
| `lock.<name>_<door>_lock` | Lock | Lock/unlock (open) the door |
| `button.<name>_<door>_open` | Button | One-press door opening |
| `button.<name>_camera_preview` | Button | Start camera preview (auto-on) |
| `button.<name>_f1` | Button | F1 auxiliary function |
| `button.<name>_call_guard` | Button | Call the building's guard/janitor |
| `camera.<name>_visitor` | Camera | Last captured visitor photo (supports turn_on for live preview) |
| `sensor.<name>_wifi_signal` | Sensor | WiFi signal strength (0-4 bars) |
| `sensor.<name>_status` | Sensor | Device activation status |
| `sensor.<name>_last_opening` | Sensor | Last door opening timestamp (with user, door, guest attributes) |
| `switch.<name>_notifications` | Switch | Enable/disable push notifications |
| `switch.<name>_dnd` | Switch | Do Not Disturb mode |
| `switch.<name>_photo_caller` | Switch | Enable/disable automatic visitor photos |

## Automations

### Flash lights when doorbell rings

```yaml
automation:
  - alias: "Flash lights on doorbell"
    trigger:
      - platform: state
        entity_id: event.fermax_your_home_doorbell
        attribute: event_type
    action:
      - service: light.turn_on
        target:
          entity_id: light.hallway
        data:
          flash: long
```

### Send notification with visitor photo

```yaml
automation:
  - alias: "Notify on doorbell with photo"
    trigger:
      - platform: state
        entity_id: event.fermax_your_home_doorbell
        attribute: event_type
    action:
      - service: camera.snapshot
        target:
          entity_id: camera.fermax_your_home_visitor
        data:
          filename: /config/www/snapshots/visitor.jpg
      - service: notify.mobile_app_your_phone
        data:
          title: "Doorbell"
          message: "Someone is at the door!"
          data:
            image: /local/snapshots/visitor.jpg
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

### View camera on demand

```yaml
automation:
  - alias: "View intercom camera"
    trigger:
      - platform: state
        entity_id: input_boolean.view_intercom
        to: "on"
    action:
      - service: button.press
        target:
          entity_id: button.fermax_your_home_camera_preview
```

## How It Works

1. **Authentication**: The integration authenticates with the Fermax Blue cloud API (`pro-duoxme.fermax.io`) using your account credentials
2. **Device Discovery**: It fetches all paired intercom devices and their accessible doors
3. **Firebase Registration**: It registers a Firebase Cloud Messaging client (simulating the mobile app) to receive real-time doorbell push notifications
4. **Camera Preview**: The auto-on feature sends a request to `/deviceaction/api/v2/device/{id}/autoon` which triggers the intercom camera
5. **Push Notifications**: When someone rings your doorbell, Fermax sends a push notification via Firebase, which the integration receives instantly and acknowledges
6. **Polling**: Device status (connection, signal) is polled at a configurable interval (default: 5 minutes)
7. **Retry Logic**: API calls are automatically retried with exponential backoff on transient errors (5xx, connection failures)

## Troubleshooting

### "Invalid credentials" error
Make sure you're using the same email and password you use in the Fermax Blue mobile app. The password is case-sensitive.

### Doorbell notifications not working
The integration needs to register with Firebase Cloud Messaging. This happens automatically but can take a few minutes on first setup. Check the Home Assistant logs for `fermax_blue` entries.

### Camera shows no image
The visitor camera only shows photos captured when someone rings the doorbell. If no one has rung since the integration was set up, the camera will be empty.

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

This launches a Docker container with a menu-driven interface to:

| Option | Description |
|--------|-------------|
| Open door | Select a door and open it |
| Device info | View connection state, WiFi signal, status |
| Press F1 | Trigger the F1 auxiliary function |
| Call guard | Call the building's guard/janitor |
| DND status | Check Do Not Disturb mode |
| Toggle DND | Enable/disable Do Not Disturb |
| Photo caller | Enable/disable automatic visitor photos |
| Opening history | View who opened the door and when |
| Camera preview | Start auto-on (requires FCM token) |
| Call log | View recent call entries |
| Raw GET/POST | Make arbitrary API calls for debugging |

No local Python installation needed — everything runs in Docker.

## Development

All development tools run via Docker — no local Python dependencies needed. Only Docker is required.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

```bash
# Run all checks (lint + format + type-check + tests)
make check

# Individual commands
make lint          # Ruff linting
make format        # Auto-format code
make format-check  # Verify formatting (CI mode)
make typecheck     # Mypy type checking
make test          # Pytest with coverage
make cli           # Interactive API tester
```

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

## Credits

- Inspired by [HASS-BlueCon](https://github.com/AfonsoFGarcia/hass-bluecon) by Afonso Garcia
- Door opening based on [fermax-blue-intercom](https://github.com/marcosav/fermax-blue-intercom) by marcosav
- Firebase push notifications via [firebase-messaging](https://github.com/sdb9696/firebase-messaging)

## Disclaimer

This integration is not affiliated with or endorsed by Fermax. It uses unofficial APIs that may change at any time. Use at your own risk.

## License

MIT License - see [LICENSE](LICENSE) file.
