"""End-to-end check: act as a HA frontend viewer with a microphone.

Talks to HA's websocket API (camera/webrtc/offer) exactly like the frontend
would, but offers an extra sendonly audio line (a 1 kHz tone as "mic").
HA -> go2rtc -> our bridge -> the intercom session. Prints what arrives.
"""

import asyncio
import fractions
import logging
import math
import os
import sys
import time

import aiohttp
import av
import numpy as np
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from aiortc.rtcconfiguration import RTCBundlePolicy
from aiortc.sdp import candidate_from_sdp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("viewer")
ENTITY = sys.argv[1]
WITH_MIC = "--mic" in sys.argv
DURATION = int(os.environ.get("DURATION", "25"))
SR, SAMPLES = 48000, 960


class ToneAudio(MediaStreamTrack):
    kind = "audio"

    def __init__(self, freq=1000.0):
        super().__init__()
        self.freq, self.n, self.t0 = freq, 0, time.monotonic()

    async def recv(self):
        await asyncio.sleep(max(0.0, self.t0 + self.n * SAMPLES / SR - time.monotonic()))
        t = (np.arange(SAMPLES) + self.n * SAMPLES) / SR
        pcm = (np.sin(2 * math.pi * self.freq * t) * 12000).astype(np.int16)
        frame = av.AudioFrame(format="s16", layout="mono", samples=SAMPLES)
        frame.planes[0].update(pcm.tobytes())
        frame.sample_rate = SR
        frame.pts = self.n * SAMPLES
        frame.time_base = fractions.Fraction(1, SR)
        self.n += 1
        return frame


async def count(track, label, stop):
    n, t0 = 0, time.monotonic()
    try:
        while not stop.is_set():
            frame = await asyncio.wait_for(track.recv(), 20)
            n += 1
            if n == 1:
                LOG.info("%s first frame after %.1fs: %s", label, time.monotonic() - t0, frame)
    except Exception as exc:
        LOG.info("%s stopped: %r", label, exc)
    LOG.info("%s total frames: %d", label, n)


async def main():
    token = os.environ["HASS_TOKEN"]
    stop = asyncio.Event()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[], bundlePolicy=RTCBundlePolicy.MAX_BUNDLE))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")
    if WITH_MIC:
        pc.addTransceiver(ToneAudio(), direction="sendonly")

    readers = []

    @pc.on("track")
    def on_track(track):
        readers.append(asyncio.ensure_future(count(track, f"RECV {track.kind}", stop)))

    @pc.on("connectionstatechange")
    async def on_state():
        LOG.info("PC state: %s", pc.connectionState)

    await pc.setLocalDescription(await pc.createOffer())
    offer = pc.localDescription.sdp

    async with (
        aiohttp.ClientSession() as s,
        s.ws_connect("http://127.0.0.1:8123/api/websocket") as ws,
    ):
        await ws.receive_json()  # auth_required
        await ws.send_json({"type": "auth", "access_token": token})
        LOG.info("auth: %s", (await ws.receive_json())["type"])
        await ws.send_json({"id": 1, "type": "camera/capabilities", "entity_id": ENTITY})
        LOG.info("capabilities: %s", (await ws.receive_json()).get("result"))
        await ws.send_json(
            {"id": 2, "type": "camera/webrtc/offer", "entity_id": ENTITY, "offer": offer}
        )
        t0 = time.monotonic()
        answered = False
        while not answered:
            msg = await asyncio.wait_for(ws.receive_json(), 40)
            if msg.get("type") == "result":
                LOG.info("offer accepted=%s %s", msg.get("success"), msg.get("error", ""))
                if not msg.get("success"):
                    return
                continue
            ev = msg.get("event", {})
            if ev.get("type") == "session":
                LOG.info("session %s", ev.get("session_id"))
            elif ev.get("type") == "answer":
                LOG.info(
                    "ANSWER after %.1fs; m-lines:\n%s",
                    time.monotonic() - t0,
                    "\n".join(
                        line
                        for line in ev["answer"].splitlines()
                        if line.startswith(("m=", "a=sendonly", "a=recvonly", "a=inactive"))
                    ),
                )
                await pc.setRemoteDescription(
                    RTCSessionDescription(sdp=ev["answer"], type="answer")
                )
                answered = True
            elif ev.get("type") == "candidate":
                c = ev["candidate"]
                cand = candidate_from_sdp(c["candidate"].removeprefix("candidate:"))
                cand.sdpMid = c.get("sdpMid") or "0"
                await pc.addIceCandidate(cand)
            elif ev.get("type") == "error":
                LOG.error("HA error: %s", ev)
                return

        # keep draining candidates while media flows
        async def drain():
            try:
                while not stop.is_set():
                    msg = await asyncio.wait_for(ws.receive_json(), 5)
                    ev = msg.get("event", {})
                    if ev.get("type") == "candidate":
                        c = ev["candidate"]
                        cand = candidate_from_sdp(c["candidate"].removeprefix("candidate:"))
                        cand.sdpMid = c.get("sdpMid") or "0"
                        await pc.addIceCandidate(cand)
                    else:
                        LOG.info("ws event: %s", str(msg)[:200])
            except Exception:
                pass

        d = asyncio.ensure_future(drain())
        await asyncio.sleep(DURATION)
        stop.set()
        d.cancel()
    await pc.close()


asyncio.run(main())
