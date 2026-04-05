#!/usr/bin/env python3
"""Test the camera/streaming flow against the real Fermax API.

This script tests the full auto-on → push notification → signaling flow
to understand what's needed for live video.

Usage:
    docker run --rm -it -v $(pwd):/app -w /app fermax-blue-dev \
      python scripts/test_camera.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from custom_components.fermax_blue.api import FermaxBlueApi
from custom_components.fermax_blue.notification import FermaxNotificationListener
from custom_components.fermax_blue.streaming import FermaxSignalingClient


async def main() -> None:
    username = os.environ.get("FERMAX_USER") or input("Email: ").strip()
    password = os.environ.get("FERMAX_PASS") or input("Password: ").strip()

    api = FermaxBlueApi(username, password)

    try:
        # 1. Auth
        print("[1/7] Authenticating...", end=" ", flush=True)
        token = await api.authenticate()
        print(f"OK (token: {token[:20]}...)")

        # 2. Get pairings
        print("[2/7] Getting pairings...", end=" ", flush=True)
        pairings = await api.get_pairings()
        if not pairings:
            print("No devices found")
            return
        pairing = pairings[0]
        print(f"OK ({pairing.tag}, device: {pairing.device_id})")

        # 3. Register FCM and start listening
        print("[3/7] Registering FCM...", end=" ", flush=True)
        notification_data: dict | None = None
        notification_event = asyncio.Event()

        def on_notification(notification: dict, persistent_id: str) -> None:
            nonlocal notification_data
            notification_data = notification
            print(f"\n  >> Push received: {json.dumps(notification, indent=2)[:500]}")
            notification_event.set()

        import tempfile
        from pathlib import Path

        storage = Path(tempfile.mkdtemp())
        listener = FermaxNotificationListener(
            storage_path=storage,
            notification_callback=on_notification,
        )
        fcm_token = await listener.register()
        if not fcm_token:
            print("FAILED to get FCM token")
            return
        print(f"OK (token: {fcm_token[:30]}...)")

        print("[3b/7] Registering token with Fermax...", end=" ", flush=True)
        await api.register_app_token(fcm_token, active=True)
        print("OK")

        print("[3c/7] Starting push listener...", end=" ", flush=True)
        await listener.start()
        print("OK")

        # 4. Trigger auto-on
        print("[4/7] Triggering auto-on...", end=" ", flush=True)
        divert = await api.auto_on(pairing.device_id, fcm_token)
        if divert:
            print(f"OK (reason: {divert.reason}, desc: {divert.description})")
            print(f"  divert_service: {divert.divert_service}")
            print(f"  directed_to: {divert.directed_to}")
            print(f"  local: {divert.local_address}")
            print(f"  remote: {divert.remote_address}")
        else:
            print("FAILED - no divert response")
            return

        # 5. Wait for push notification with room info
        print("[5/7] Waiting for push notification (up to 15s)...")
        try:
            await asyncio.wait_for(notification_event.wait(), timeout=15)
        except TimeoutError:
            print("  TIMEOUT - no push received in 15s")
            print("  This means the auto-on didn't trigger a push notification.")
            print("  The notification may need a real monitor to respond.")
            return

        if not notification_data:
            print("  No notification data")
            return

        # Extract signaling info from push
        room_id = notification_data.get("RoomId")
        socket_url = notification_data.get(
            "SocketUrl", "http://signaling-pro-duoxme.fermax.io"
        )
        streaming_mode = notification_data.get("StreamingMode", "")
        preview_timeout = notification_data.get("PreviewTimeout", "29")
        notif_type = notification_data.get("FermaxNotificationType", "")

        print(f"  Notification type: {notif_type}")
        print(f"  Room ID: {room_id}")
        print(f"  Socket URL: {socket_url}")
        print(f"  Streaming mode: {streaming_mode}")
        print(f"  Preview timeout: {preview_timeout}s")

        if not room_id:
            print("  No RoomId in notification - cannot connect to signaling")
            return

        # 6. Connect to signaling server
        print(f"[6/7] Connecting to signaling server at {socket_url}...")
        client = FermaxSignalingClient(
            signaling_url=socket_url,
            oauth_token=token,
            fcm_token=fcm_token,
        )

        result = await client.connect(room_id)
        if result:
            print("  Room joined!")
            print(f"  Video producer: {result.video_producer_id}")
            print(f"  Audio producer: {result.audio_producer_id}")
            print(f"  RTP capabilities: {result.router_rtp_capabilities[:200]}...")
            print(f"  Recv video transport: {result.recv_video_transport.id}")
            print(f"  Recv audio transport: {result.recv_audio_transport.id}")
            print(f"  Send transport: {result.send_transport.id}")
            if result.ice_servers:
                print(f"  ICE servers: {result.ice_servers}")

            # 7. Try to consume video
            print("[7/7] Requesting video consume...")
            consume = await client.consume_transport(
                transport_id=result.recv_video_transport.id,
                producer_id=result.video_producer_id,
                rtp_capabilities=result.router_rtp_capabilities,
            )
            if consume:
                print(f"  Consumer ID: {consume.consumer_id}")
                print(f"  Kind: {consume.kind}")
                print(f"  RTP params: {str(consume.rtp_parameters)[:300]}...")
                print()
                print("=" * 60)
                print("  VIDEO STREAM IS AVAILABLE!")
                print("  The signaling is complete. To get actual video frames,")
                print("  we need a WebRTC/mediasoup client to receive RTP packets.")
                print("=" * 60)
            else:
                print("  Failed to consume video transport")

            print("\nHanging up...")
            await client.hangup()
            await client.disconnect()
        else:
            print("  Failed to join room")

    except Exception as e:
        print(f"\nError: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()

    finally:
        if "listener" in dir():
            await listener.stop()
        await api.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
