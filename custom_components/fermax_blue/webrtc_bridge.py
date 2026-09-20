"""WebRTC bridge: publishes a live session to go2rtc with a return audio channel.

go2rtc (bundled with Home Assistant) dials the signaling endpoint below when a
viewer opens the camera. Its offer carries three media lines: video and audio
it wants to receive, plus an audio line it wants to *send* — the viewer's
microphone, when a card captures one. We answer at once with the last
snapshot and silence, wake the intercom, and switch both tracks to the live
session when it comes up; a slow answer would make go2rtc give up on ICE. The
microphone is routed into the audio producer the session publishes to the
panel, and its first packet answers the call.

Signaling is go2rtc's own JSON-over-WebSocket format:
  {"type": "webrtc/offer", "value": sdp} -> {"type": "webrtc/answer", "value": sdp}
  {"type": "webrtc/candidate", "value": candidate} (go2rtc -> us, trickled)
go2rtc closes the WebSocket as soon as the media connection is up; the media
session must outlive it and end on its own connection-state changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import WEBRTC_PATH
from .streaming import _create_switchable_audio_track

if TYPE_CHECKING:
    from .coordinator import FermaxBlueCoordinator
    from .streaming import FermaxStreamSession

_LOGGER = logging.getLogger(__name__)

# How often a peer checks that the session behind it is still alive
SESSION_POLL_SECONDS = 1.0
# go2rtc redials the moment we hang up on a viewer it still serves; when the
# intercom could not be woken (it answers 409 while tearing a session down)
# this pause spaces the attempts out instead of hammering the API
WAKE_RETRY_DELAY = 5.0
# Placeholder video while the intercom wakes up
PLACEHOLDER_FPS = 2
PLACEHOLDER_SIZE = (640, 480)

_FAST_H264_APPLIED = False


def webrtc_stream_source(hass: HomeAssistant, token: str) -> str:
    """Return the go2rtc source string for a coordinator's signaling endpoint."""
    api = hass.config.api
    scheme = "wss" if api and api.use_ssl else "ws"
    port = api.port if api else 8123
    # ponytail: assumes go2rtc runs next to HA (the bundled instance). A go2rtc
    # add-on in another container needs HA's reachable host here instead.
    return f"webrtc:{scheme}://127.0.0.1:{port}{WEBRTC_PATH.format(token=token)}"


def _make_h264_context(width: int, height: int, bitrate: int) -> Any:
    """aiortc's x264 context, plus the cheapest preset."""
    import fractions

    import av
    from aiortc.codecs import h264

    codec = av.CodecContext.create("libx264", "w")
    codec.width = width
    codec.height = height
    codec.bit_rate = bitrate
    codec.pix_fmt = "yuv420p"
    codec.framerate = fractions.Fraction(h264.MAX_FRAME_RATE, 1)
    codec.time_base = fractions.Fraction(1, h264.MAX_FRAME_RATE)
    codec.options = {"level": "31", "tune": "zerolatency", "preset": "ultrafast"}
    codec.profile = "Baseline"
    return codec


def _prefer_fast_h264() -> None:
    """Run aiortc's H264 encoder on x264's cheapest preset.

    aiortc leaves x264 on its default preset, which on a Raspberry Pi 4 costs
    ~150% of a core for a 720x480 stream and halves the frame rate. The codec
    context is created lazily on the first frame, so the subclass pre-creates
    it with the stock settings plus the preset before the parent gets to.
    Idempotent; any failure leaves aiortc's defaults in place.
    """
    global _FAST_H264_APPLIED
    if _FAST_H264_APPLIED:
        return
    _FAST_H264_APPLIED = True
    try:
        from aiortc import codecs
        from aiortc.codecs import h264

        class _FastH264Encoder(h264.H264Encoder):  # type: ignore[misc]
            def _encode_frame(self, frame: Any, force_keyframe: bool) -> Any:
                if self.codec and (
                    frame.width != self.codec.width or frame.height != self.codec.height
                ):
                    self.buffer_data = b""
                    self.buffer_pts = None
                    self.codec = None
                if self.codec is None:
                    self.codec = _make_h264_context(frame.width, frame.height, self.target_bitrate)
                yield from super()._encode_frame(frame, force_keyframe)

        codecs.H264Encoder = _FastH264Encoder  # type: ignore[misc]
    except Exception:
        _LOGGER.debug("Could not tune the H264 encoder; using aiortc defaults", exc_info=True)


def _placeholder_frame(jpeg: bytes | None) -> Any:
    """Return a yuv420p VideoFrame of the last snapshot, or a black frame."""
    import av

    if jpeg:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(jpeg)).convert("RGB")
            # yuv420p needs even dimensions
            img = img.crop((0, 0, img.width - img.width % 2, img.height - img.height % 2))
            return av.VideoFrame.from_image(img).reformat(format="yuv420p")
        except Exception:
            _LOGGER.debug("Unusable snapshot for the placeholder frame", exc_info=True)
    width, height = PLACEHOLDER_SIZE
    frame = av.VideoFrame(width, height, "yuv420p")
    frame.planes[0].update(b"\x10" * frame.planes[0].buffer_size)
    for plane in frame.planes[1:]:
        plane.update(b"\x80" * plane.buffer_size)
    return frame


def _create_switchable_video_track(placeholder_jpeg: bytes | None) -> Any:
    """A video track showing a still until a live source is attached."""
    from fractions import Fraction

    from aiortc import MediaStreamTrack

    class _Track(MediaStreamTrack):  # type: ignore[misc]
        kind = "video"

        def __init__(self) -> None:
            super().__init__()
            self._source: Any = None
            self._placeholder = _placeholder_frame(placeholder_jpeg)
            self._n = 0
            # (source pts, our pts) at the first live packet: the live timeline
            # continues where the placeholder left off
            self._origin: tuple[int, int] | None = None

        def set_source(self, track: Any) -> None:
            self._source = track
            self._origin = None

        async def recv(self) -> Any:
            if self._source:
                try:
                    item = await self._source.recv()
                except Exception:
                    self._source = None
                else:
                    if hasattr(item, "pts") and not hasattr(item, "planes"):
                        if self._origin is None:
                            self._origin = (item.pts, self._n * 90000 // PLACEHOLDER_FPS)
                        item.pts = self._origin[1] + item.pts - self._origin[0]
                    return item
            await asyncio.sleep(1 / PLACEHOLDER_FPS)
            frame = self._placeholder
            frame.pts = self._n
            frame.time_base = Fraction(1, PLACEHOLDER_FPS)
            self._n += 1
            return frame

    return _Track()


class FermaxWebRtcView(HomeAssistantView):
    """Signaling endpoint go2rtc connects to, one URL per intercom."""

    url = WEBRTC_PATH
    name = "api:fermax_blue:webrtc"
    # go2rtc dials without HA credentials; the random token gates access
    requires_auth = False

    def __init__(self, coordinators_by_token: dict[str, FermaxBlueCoordinator]) -> None:
        self._coordinators = coordinators_by_token

    async def get(self, request: web.Request, token: str) -> web.WebSocketResponse:
        """Upgrade to WebSocket and bridge the intercom to go2rtc."""
        coordinator = self._coordinators.get(token)
        if coordinator is None:
            raise web.HTTPNotFound

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await WebRtcPeer(coordinator).run(ws)
        return ws


class WebRtcPeer:
    """One go2rtc connection: our media out, the viewer microphone in."""

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        self._coordinator = coordinator
        self._session: FermaxStreamSession | None = None
        self._pc: Any = None
        self._video: Any = None
        self._audio: Any = None
        self._mic_track: Any = None
        self._mic_ready = False
        self._mic_routed = False
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    async def run(self, ws: web.WebSocketResponse) -> None:
        """Drive the signaling exchange over the WebSocket."""
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
        from aiortc.rtcconfiguration import RTCBundlePolicy
        from aiortc.sdp import candidate_from_sdp

        _prefer_fast_h264()
        # go2rtc offers two audio lines; aiortc's default bundle policy attaches
        # the second one to a transport it later discards. max-bundle keeps all
        # media on the primary transport, as go2rtc expects.
        self._pc = pc = RTCPeerConnection(
            RTCConfiguration(iceServers=[], bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )

        @pc.on("track")
        def _on_track(track: Any) -> None:
            if track.kind == "audio":
                self._mic_track = track
                self._tasks.append(asyncio.create_task(self._pump_microphone(track)))

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            _LOGGER.debug("WebRTC peer state: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await self.close()

        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("type") == "webrtc/offer":
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=data["value"], type="offer")
                    )
                    self._video = _create_switchable_video_track(self._coordinator.last_photo)
                    self._audio = _create_switchable_audio_track()
                    pc.addTrack(self._video)
                    pc.addTrack(self._audio)
                    await pc.setLocalDescription(await pc.createAnswer())
                    await ws.send_json({"type": "webrtc/answer", "value": pc.localDescription.sdp})
                    self._tasks.append(asyncio.create_task(self._attach_session()))
                elif data.get("type") == "webrtc/candidate" and data.get("value"):
                    candidate = candidate_from_sdp(str(data["value"]).removeprefix("candidate:"))
                    candidate.sdpMid = "0"
                    await pc.addIceCandidate(candidate)
        except Exception:
            _LOGGER.debug("WebRTC signaling ended with error", exc_info=True)
            await self.close()

    async def _attach_session(self) -> None:
        """Wake the intercom, switch the tracks to the live media, then watch it."""
        try:
            session = await self._coordinator.ensure_stream()
        except Exception:
            # A background task: an unhandled error would leave the viewer on
            # the placeholder for ever, silently
            _LOGGER.warning("Could not start the intercom for a WebRTC viewer", exc_info=True)
            await self.close()
            return
        if session is None or self._closed:
            if not self._closed:
                _LOGGER.warning("WebRTC viewer connected but no live session could be started")
                await asyncio.sleep(WAKE_RETRY_DELAY)
            await self.close()
            return
        self._session = session
        # The panel's own H264 costs nothing to forward; re-encoding is the fallback
        video = session.subscribe_encoded_video() or session.subscribe_video()
        if video is not None:
            self._video.set_source(video)
        session.attach_audio_sink(self._audio)
        await self._route_microphone()
        while not self._closed:
            await asyncio.sleep(SESSION_POLL_SECONDS)
            if not session.is_active:
                await self.close()

    async def _pump_microphone(self, track: Any) -> None:
        """The first microphone packet marks a viewer who wants to talk."""
        from aiortc.mediastreams import MediaStreamError

        try:
            await track.recv()
        except MediaStreamError:
            return
        self._mic_ready = True
        await self._route_microphone()

    async def _route_microphone(self) -> None:
        """Answer the call and feed the microphone to the panel, once both are ready."""
        if not (self._session and self._mic_ready) or self._mic_routed or self._closed:
            return
        self._mic_routed = True
        if not await self._coordinator.pickup():
            _LOGGER.warning("Viewer microphone active but the call could not be answered")
            return
        _LOGGER.info("Viewer microphone connected to the intercom")
        self._session.set_audio_source(self._mic_track)

    async def close(self) -> None:
        """Release tracks and the peer connection (idempotent)."""
        if self._closed:
            return
        self._closed = True
        for track in (self._video, self._audio):
            if track is not None:
                track.stop()
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if self._pc:
            with contextlib.suppress(Exception):
                await self._pc.close()
        _LOGGER.debug("WebRTC peer closed")
