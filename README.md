# Fermax Blue for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant custom integration for **Fermax Blue** video door entry systems (DUOX PLUS / blueStream).

This integration simulates a Fermax Blue mobile app client, connecting to the Fermax cloud API and receiving real-time push notifications via Firebase Cloud Messaging when someone rings your doorbell.

## Features

- **Doorbell detection** — Real-time push notification when someone rings (via Firebase Cloud Messaging)
- **Door opening** — Open your building's door remotely (lock entity + button)
- **Visitor camera** — View the last captured visitor photo
- **Connection status** — Monitor if your intercom is online
- **WiFi signal** — Track the intercom's wireless signal strength
- **Notification control** — Enable/disable doorbell notifications

## Supported Devices

Tested with:
- Fermax VEO-XL WiFi DUOX PLUS
- Fermax VEO-XS WiFi DUOX PLUS (REF: 9449)

Should work with any Fermax Blue-compatible intercom (devices that work with the Fermax Blue mobile app).

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
4. Enter your Fermax Blue app credentials (same email/password you use in the mobile app)

The integration will automatically discover all paired devices on your account.

## Entities

For each paired intercom device, the integration creates:

| Entity | Type | Description |
|--------|------|-------------|
| `binary_sensor.<name>_connection` | Binary Sensor | Device connectivity status |
| `binary_sensor.<name>_doorbell` | Binary Sensor | Turns on when someone rings (auto-resets after 30s) |
| `lock.<name>_<door>_lock` | Lock | Lock/unlock (open) the door |
| `button.<name>_<door>_open` | Button | One-press door opening |
| `camera.<name>_visitor` | Camera | Last captured visitor photo |
| `sensor.<name>_wifi_signal` | Sensor | WiFi signal strength (0-4 bars) |
| `sensor.<name>_status` | Sensor | Device activation status |
| `switch.<name>_notifications` | Switch | Enable/disable push notifications |

## Automations

### Flash lights when doorbell rings

```yaml
automation:
  - alias: "Flash lights on doorbell"
    trigger:
      - platform: state
        entity_id: binary_sensor.fermax_your_home_doorbell
        to: "on"
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
        entity_id: binary_sensor.fermax_your_home_doorbell
        to: "on"
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

## How It Works

1. **Authentication**: The integration authenticates with the Fermax Blue cloud API (`pro-duoxme.fermax.io`) using your account credentials
2. **Device Discovery**: It fetches all paired intercom devices and their accessible doors
3. **Firebase Registration**: It registers a Firebase Cloud Messaging client (simulating the mobile app) to receive real-time doorbell push notifications
4. **Push Notifications**: When someone rings your doorbell, Fermax sends a push notification via Firebase, which the integration receives instantly
5. **Polling**: Device status (connection, signal) is polled every 5 minutes

## Troubleshooting

### "Invalid credentials" error
Make sure you're using the same email and password you use in the Fermax Blue mobile app. The password is case-sensitive.

### Doorbell notifications not working
The integration needs to register with Firebase Cloud Messaging. This happens automatically but can take a few minutes on first setup. Check the Home Assistant logs for `fermax_blue` entries.

### Camera shows no image
The visitor camera only shows photos captured when someone rings the doorbell. If no one has rung since the integration was set up, the camera will be empty.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=custom_components/fermax_blue --cov-report=term-missing

# Lint
pip install ruff
ruff check custom_components/ tests/
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
