#!/usr/bin/env python3
"""E2E test: auto-on → FCM push → signaling → mediasoup → JPEG frames.

Persists FCM credentials to avoid Google rate limiting.

Usage:
    docker run --rm -v $(pwd):/app -v /tmp/fermax_fcm_storage:/fcm_storage \
      -w /app fermax-blue-dev python scripts/test_streaming.py

Env vars: FERMAX_USER, FERMAX_PASS (required)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from custom_components.fermax_blue.api import FermaxBlueApi
from custom_components.fermax_blue.notification import FermaxNotificationListener
from custom_components.fermax_blue.streaming import FermaxStreamSession


async def main() -> None:
    user = os.environ.get("FERMAX_USER", "")
    passwd = os.environ.get("FERMAX_PASS", "")
    if not user or not passwd:
        print("Set FERMAX_USER and FERMAX_PASS env vars")
        return

    storage = Path(os.environ.get("FCM_STORAGE", "/fcm_storage"))
    storage.mkdir(parents=True, exist_ok=True)

    api = FermaxBlueApi(user, passwd)
    listener: FermaxNotificationListener | None = None

    try:
        # 1. Auth + pairings
        await api.authenticate()
        pairings = await api.get_pairings()
        if not pairings:
            print("No devices")
            return
        p = pairings[0]
        print(f"Device: {p.tag} ({p.device_id})")

        # 2. FCM (persistent credentials)
        notif_event = asyncio.Event()
        notif_data: dict = {}
        auto_on_time = 0

        def on_notif(n: dict, pid: str) -> None:
            import time

            data = n.get("data", n)
            room_id = data.get("RoomId", "")
            # RoomId format: {deviceId}_{timestamp_ms} — check if it's fresh
            if room_id and "_" in room_id:
                room_ts = int(room_id.rsplit("_", 1)[-1])
                now_ms = int(time.time() * 1000)
                age_s = (now_ms - room_ts) / 1000
                if age_s > 30:
                    print(f"Push (stale, {age_s:.0f}s old): {room_id}")
                    return
            if auto_on_time == 0:
                print(
                    f"Push (before auto-on): type={data.get('FermaxNotificationType')}"
                )
                return
            notif_data.update(data)
            print(
                f"Push: type={notif_data.get('FermaxNotificationType')}, room={room_id}"
            )
            notif_event.set()

        listener = FermaxNotificationListener(
            storage_path=storage, notification_callback=on_notif
        )
        fcm_token = await listener.register()
        if not fcm_token:
            print("FCM registration failed (rate limited?). Try again later.")
            return
        print(f"FCM: {fcm_token[:50]}...")
        await api.register_app_token(fcm_token, active=True)
        await listener.start()

        # 3. Wait for stale pushes to drain, then auto-on
        import time

        await asyncio.sleep(3)
        auto_on_time = time.time()
        divert = await api.auto_on(p.device_id, fcm_token)
        print(f"Auto-on: {divert.reason if divert else 'FAIL'}")

        # 4. Wait for push
        print("Waiting for push (up to 20s)...")
        try:
            await asyncio.wait_for(notif_event.wait(), timeout=20)
        except TimeoutError:
            print("No push received. Is the monitor online?")
            return

        room_id = notif_data.get("RoomId", "")
        socket_url = notif_data.get("SocketUrl", "")
        fermax_token = notif_data.get("FermaxToken", "")
        print(f"Room: {room_id}")
        print(f"Socket: {socket_url}")

        if not room_id or not socket_url:
            print("Missing room/socket info in push")
            return

        # 5. Stream
        print("Starting stream session...")
        stream = FermaxStreamSession(
            signaling_url=socket_url,
            oauth_token=fermax_token,
            fcm_token=fcm_token,
            room_id=room_id,
        )
        success = await stream.start()
        if not success:
            print("Stream failed to start")
            return

        print("STREAMING! Grabbing frames for 15s...")
        for i in range(15):
            await asyncio.sleep(1)
            frame = stream.latest_frame
            if frame:
                print(f"  Frame {i}: {len(frame)} bytes JPEG")
                if i == 0:
                    out = Path("/app/test_frame.jpg")
                    out.write_bytes(frame)
                    print(f"  >> Saved {out}")
            else:
                print(f"  Frame {i}: waiting...")

        await stream.stop()
        print("Stream stopped.")

    finally:
        if listener:
            await listener.stop()
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
