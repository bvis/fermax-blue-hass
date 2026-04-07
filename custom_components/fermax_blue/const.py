"""Constants for the Fermax Blue integration."""

from __future__ import annotations

DOMAIN = "fermax_blue"
MANUFACTURER = "Fermax"

# Simulated device headers
APP_HEADERS = {
    "app-version": "4.3.0",
    "accept-language": "en-ES;q=1.0, es-ES;q=0.9",
    "phone-os": "14.0",
    "user-agent": (
        "Blue/4.3.0 (com.fermax.blue.app; build:1; Android 14.0) okhttp/4.12.0"
    ),
    "phone-model": "HA-Integration",
    "app-build": "1",
}

# Signal dispatchers
SIGNAL_DOORBELL_RING = f"{DOMAIN}_doorbell_ring_{{}}_{{}}"
SIGNAL_CALL_ENDED = f"{DOMAIN}_call_ended_{{}}"
SIGNAL_DOOR_OPENED = f"{DOMAIN}_door_opened_{{}}"
SIGNAL_CAMERA_ON = f"{DOMAIN}_camera_on_{{}}"

# Platforms
PLATFORMS = [
    "binary_sensor",
    "button",
    "camera",
    "event",
    "lock",
    "select",
    "sensor",
    "switch",
]

# Call mode options (select entity)
CALL_MODE_NOTIFY = "notify_only"
CALL_MODE_RECORD = "record"
CALL_MODE_AUTO_RESPOND = "auto_respond"
CALL_MODES = [CALL_MODE_NOTIFY, CALL_MODE_RECORD, CALL_MODE_AUTO_RESPOND]

# Config keys — API/Firebase credentials (provided by the user)
CONF_FERMAX_AUTH_URL = "fermax_auth_url"
CONF_FERMAX_BASE_URL = "fermax_base_url"
CONF_FERMAX_AUTH_BASIC = "fermax_auth_basic"
CONF_FIREBASE_API_KEY = "firebase_api_key"
CONF_FIREBASE_SENDER_ID = "firebase_sender_id"
CONF_FIREBASE_APP_ID = "firebase_app_id"
CONF_FIREBASE_PROJECT_ID = "firebase_project_id"
CONF_FIREBASE_PACKAGE_NAME = "firebase_package_name"

# Options flow defaults
DEFAULT_SCAN_INTERVAL = 5  # minutes
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 30
CONF_SCAN_INTERVAL = "scan_interval"
CONF_RECORDING_RETENTION = "recording_retention"
DEFAULT_RECORDING_RETENTION = 10  # days
RECORDINGS_DIR = "fermax_recordings"
