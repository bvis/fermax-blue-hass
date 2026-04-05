#!/usr/bin/env python3
"""Interactive CLI to test Fermax Blue API features locally.

Usage (via Docker):
    make cli

Or directly:
    docker run --rm -it -v $(pwd):/app -w /app python:3.12-slim \
      sh -c "pip install -q httpx 2>/dev/null && python scripts/cli.py"

Environment variables (optional, to skip prompts):
    FERMAX_USER=your@email.com
    FERMAX_PASS=yourpassword
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Add project root to path so we can import the integration code
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.fermax_blue.api import (
    FermaxBlueApi,
)


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def print_menu(options: list[tuple[str, str]]) -> None:
    for key, label in options:
        print(f"  [{key}] {label}")
    print()


async def main() -> None:
    print_header("Fermax Blue CLI - Local API Tester")

    # Get credentials
    username = os.environ.get("FERMAX_USER") or input("Email: ").strip()
    password = os.environ.get("FERMAX_PASS") or input("Password: ").strip()

    if not username or not password:
        print("Error: credentials required")
        return

    api = FermaxBlueApi(username, password)

    try:
        # Authenticate
        print("\nAuthenticating...", end=" ", flush=True)
        token = await api.authenticate()
        print(f"OK (token: {token[:20]}...)")

        # Discover devices
        print("Discovering devices...", end=" ", flush=True)
        pairings = await api.get_pairings()
        print(f"OK ({len(pairings)} device(s))\n")

        if not pairings:
            print("No devices found. Check your account.")
            return

        # Show devices
        for i, p in enumerate(pairings):
            print(f"  [{i}] {p.tag} (ID: {p.device_id})")
            for name, door in p.access_doors.items():
                vis = "visible" if door.visible else "hidden"
                print(f"      Door: {name} - {door.title} ({vis})")

        # Select device
        if len(pairings) == 1:
            pairing = pairings[0]
            print(f"\nUsing: {pairing.tag}")
        else:
            idx = int(input("\nSelect device [0]: ").strip() or "0")
            pairing = pairings[idx]

        # Get device info
        print(f"\nFetching device info for {pairing.device_id}...", end=" ", flush=True)
        info = await api.get_device_info(pairing.device_id)
        print("OK")
        print(f"  Connection: {info.connection_state}")
        print(f"  Status: {info.status}")
        print(f"  Family: {info.family}")
        print(f"  Type: {info.device_type} {info.subtype}")
        print(f"  WiFi signal: {info.wireless_signal}/4")
        print(f"  Photo caller: {info.photocaller}")
        print(f"  Streaming mode: {info.streaming_mode}")

        # Interactive menu
        while True:
            print_header(f"Actions for {pairing.tag}")
            options = [
                ("1", "Open door"),
                ("2", "Get device info (refresh)"),
                ("3", "Press F1"),
                ("4", "Call guard/janitor"),
                ("5", "Get DND status"),
                ("6", "Toggle DND"),
                ("7", "Toggle photo caller"),
                ("8", "Get opening history"),
                ("9", "Start camera preview (auto-on)"),
                ("10", "Get call log"),
                ("11", "Raw API call (GET)"),
                ("12", "Raw API call (POST)"),
                ("q", "Quit"),
            ]
            print_menu(options)

            choice = input("Choose action: ").strip()

            try:
                if choice == "q":
                    break

                if choice == "1":
                    # List visible doors
                    visible_doors = [
                        (n, d) for n, d in pairing.access_doors.items() if d.visible
                    ]
                    if not visible_doors:
                        print("No visible doors found!")
                        continue

                    for i, (name, door) in enumerate(visible_doors):
                        print(f"  [{i}] {name} - {door.title}")

                    idx = int(input("Select door [0]: ").strip() or "0")
                    door_name, door = visible_doors[idx]

                    print(f"Opening {door_name}...", end=" ", flush=True)
                    result = await api.open_door(pairing.device_id, door.access_id)
                    print("OK" if result else "FAILED")

                elif choice == "2":
                    info = await api.get_device_info(pairing.device_id)
                    print(f"  Connection: {info.connection_state}")
                    print(f"  Status: {info.status}")
                    print(f"  WiFi: {info.wireless_signal}/4")
                    print(f"  Photo caller: {info.photocaller}")

                elif choice == "3":
                    print("Pressing F1...", end=" ", flush=True)
                    await api.press_f1(pairing.device_id)
                    print("OK")

                elif choice == "4":
                    print("Calling guard...", end=" ", flush=True)
                    await api.call_guard(pairing.device_id)
                    print("OK")

                elif choice == "5":
                    fcm_token = input("FCM token (or press Enter to skip): ").strip()
                    if not fcm_token:
                        fcm_token = "cli_test_token"
                    status = await api.get_dnd_status(pairing.device_id, fcm_token)
                    print(f"  DND enabled: {status}")

                elif choice == "6":
                    fcm_token = input("FCM token (or press Enter to skip): ").strip()
                    if not fcm_token:
                        fcm_token = "cli_test_token"
                    enable = input("Enable DND? [y/n]: ").strip().lower() == "y"
                    print(f"Setting DND to {enable}...", end=" ", flush=True)
                    await api.set_dnd(pairing.device_id, fcm_token, enabled=enable)
                    print("OK")

                elif choice == "7":
                    enable = (
                        input("Enable photo caller? [y/n]: ").strip().lower() == "y"
                    )
                    print(f"Setting photo caller to {enable}...", end=" ", flush=True)
                    await api.set_photo_caller(pairing.device_id, enabled=enable)
                    print("OK")

                elif choice == "8":
                    user_id = (
                        input("User ID (or press Enter for 'me'): ").strip() or "me"
                    )
                    print("Fetching opening history...", end=" ", flush=True)
                    records = await api.get_opening_history(pairing.device_id, user_id)
                    print(f"OK ({len(records)} entries)")
                    for r in records[:10]:
                        guest = f" (guest: {r.guest_email})" if r.guest_email else ""
                        print(f"  {r.timestamp} - {r.user} - {r.door}{guest}")

                elif choice == "9":
                    fcm_token = input("FCM token (required for auto-on): ").strip()
                    if not fcm_token:
                        print("FCM token is required for auto-on")
                        continue
                    print("Starting camera preview...", end=" ", flush=True)
                    result = await api.auto_on(pairing.device_id, fcm_token)
                    if result:
                        print("OK")
                        print(f"  Reason: {result.reason}")
                        print(f"  Description: {result.description}")
                        print(f"  Directed to: {result.directed_to}")
                    else:
                        print("FAILED (no response)")

                elif choice == "10":
                    fcm_token = input("FCM token (required): ").strip()
                    if not fcm_token:
                        print("FCM token required")
                        continue
                    entries = await api.get_call_log(fcm_token)
                    print(f"Call log: {len(entries)} entries")
                    for e in entries[:10]:
                        photo = f" [photo: {e.photo_id}]" if e.photo_id else ""
                        answered = " (answered)" if e.answered else ""
                        print(f"  {e.call_date} - {e.device_id}{answered}{photo}")

                elif choice == "11":
                    path = input(
                        "GET path (e.g. /pairing/api/v4/pairings/me): "
                    ).strip()
                    params_str = input(
                        "Query params JSON (or Enter for none): "
                    ).strip()
                    params = json.loads(params_str) if params_str else None
                    response = await api._api_get(path, params=params)
                    print(f"Status: {response.status_code}")
                    try:
                        print(json.dumps(response.json(), indent=2)[:2000])
                    except Exception:
                        print(response.text[:2000])

                elif choice == "12":
                    path = input("POST path: ").strip()
                    body_str = input("JSON body (or Enter for none): ").strip()
                    body = json.loads(body_str) if body_str else None
                    response = await api._api_post(path, json=body)
                    print(f"Status: {response.status_code}")
                    try:
                        print(json.dumps(response.json(), indent=2)[:2000])
                    except Exception:
                        print(response.text[:2000])

                else:
                    print("Invalid option")

            except Exception as e:
                print(f"\nError: {type(e).__name__}: {e}")

    finally:
        await api.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
