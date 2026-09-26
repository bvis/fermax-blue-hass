"""Mediasoup video streaming client for Fermax Blue.

Connects to the Fermax signaling server via Socket.IO, negotiates
mediasoup transports, and receives video frames from the intercom camera.

Architecture:
  1. API auto-on → push notification with roomId + signalingUrl
  2. Socket.IO connect → join_call → transport params
  3. pymediasoup Device creates RecvTransport
  4. transport_consume → Consumer with aiortc video track
  5. FrameGrabber reads frames, converts to JPEG for HA camera
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from importlib.util import find_spec
from typing import Any

import socketio

_LOGGER = logging.getLogger(__name__)


@cache
def streaming_deps_available() -> bool:
    """Return True when the live-video deps (pymediasoup/aiortc) are installed.

    They are back in the manifest requirements since aiortc 1.15.0 supports
    av>=17, but installs that upgraded while the deps were optional may still
    lack them until HA reinstalls requirements. Callers must skip stream work —
    including the auto-on request that wakes the physical intercom — when this
    returns False.
    """
    return find_spec("pymediasoup") is not None


# Suppress noisy H264 decode warnings (expected on stream start before first keyframe)
logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)

SIGNALING_VERSION = "0.8.2"


def _patch_pymediasoup_audio_channels() -> None:
    """Fix pymediasoup bug: channels=None vs channels=1 for mono audio codecs.

    sdp-transform omits the encoding parameter for mono codecs like PCMA,
    resulting in channels=None. Mediasoup routers set channels=1 explicitly.
    matchCodecs() does strict equality (None != 1), causing canProduce("audio")
    to return False even though both sides support the same codec.
    """
    from pymediasoup.handlers.aiortc_handler import AiortcHandler as _Handler
    from pymediasoup.rtp_parameters import RtpCapabilities as _Caps

    _orig_get = _Handler.getNativeRtpCapabilities

    async def _patched_get(self: _Handler) -> _Caps:
        caps = await _orig_get(self)
        for codec in caps.codecs:
            if codec.kind == "audio" and codec.channels is None:
                codec.channels = 1
        return caps

    _Handler.getNativeRtpCapabilities = _patched_get  # type: ignore[assignment]


_PYMEDIASOUP_PATCHED = False
# Producer audio format: what the switchable track always emits
AUDIO_RATE = 48000
AUDIO_SAMPLES = 960  # 20 ms
# How long the switchable track waits for a live source before sending silence
SOURCE_TIMEOUT = 0.2
# UDP receive buffer for the media sockets. The kernel default (~200 KB)
# overflows when the event loop stalls under encoder load and the panel audio
# gets dropped before we ever see it; 4 MB absorbs stalls of a few seconds.
UDP_RCVBUF = 4 * 1024 * 1024
# asyncio hands one datagram per loop wake-up to a datagram protocol. Under
# viewer load the loop wakes up fewer times per second than the panel sends
# packets, so media falls behind real time without losing anything; reading
# until the socket is empty decouples the media rate from the loop rate.
DATAGRAMS_PER_WAKEUP = 64
# Longest hole in a live source that is filled with silence (samples at AUDIO_RATE);
# beyond it the timeline is re-anchored instead of bursting silence
MAX_GAP_FILL = 2 * AUDIO_RATE
DEFAULT_SIGNALING_URL = "https://signaling-pro-duoxme.fermax.io"


@cache
def _overlay_font(image_font: Any) -> Any:
    """The LIVE badge font, loaded once instead of per frame."""
    return image_font.load_default(size=16)


def _queue_depth(track: Any) -> int | None:
    """Frames waiting in an aiortc track's internal queue (diagnostics only)."""
    queue = getattr(track, "_queue", None)
    return queue.qsize() if queue is not None and hasattr(queue, "qsize") else None


def _drain_on_wakeup(transport: Any) -> None:
    """Make an asyncio datagram transport read every waiting packet per wake-up."""
    loop, sock, protocol = transport._loop, transport._sock, transport._protocol

    def _read_ready() -> None:
        for _ in range(DATAGRAMS_PER_WAKEUP):
            if transport._conn_lost:
                return
            try:
                data, addr = sock.recvfrom(transport.max_size)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as exc:
                protocol.error_received(exc)
                return
            protocol.datagram_received(data, addr)

    # add_reader() refuses an fd owned by a transport; the private pair is what
    # the transport itself uses
    loop._remove_reader(sock.fileno())
    loop._add_reader(sock.fileno(), _read_ready)


def _nal_types(data: bytes) -> list[int]:
    """NAL unit types of an Annex B access unit (start codes 00 00 01)."""
    types = []
    pos = data.find(b"\x00\x00\x01")
    while pos != -1 and pos + 3 < len(data):
        types.append(data[pos + 3] & 0x1F)
        pos = data.find(b"\x00\x00\x01", pos + 3)
    return types


def _parameter_sets(data: bytes) -> tuple[bytes, bytes]:
    """(SPS, PPS) NAL units of an Annex B access unit, each with a start code, or empty."""
    sps = pps = b""
    for chunk in data.split(b"\x00\x00\x01")[1:]:
        nal = chunk.rstrip(b"\x00")
        if nal and nal[0] & 0x1F == 7:
            sps = b"\x00\x00\x00\x01" + nal
        elif nal and nal[0] & 0x1F == 8:
            pps = b"\x00\x00\x00\x01" + nal
    return sps, pps


def _write_mp4(
    path: str,
    access_units: list[tuple[bytes, int]],
    jpegs: list[bytes],
    pcm: bytes,
    rate: int,
    audio_offset: float,
    video_span: float = 0.0,
) -> None:
    """Mux a recording: the panel's own H264 (or re-encoded JPEG frames) plus mono PCM.

    Access units are Annex B and go in untouched. Their timestamps keep the
    panel's spacing but are rescaled so that the first and last frame sit
    ``video_span`` seconds apart (the wall-clock span in which they arrived):
    the panel stamps at 1 kHz although the codec advertises 90 kHz, and
    trusting it made a 90 s call play its video in one second. The JPEG path
    only serves sessions where the encoded video could not be tapped. Audio
    starts ``audio_offset`` seconds into the video (the panel audio only
    exists after pickup).
    """
    from fractions import Fraction

    import av
    import numpy as np
    from PIL import Image

    with av.open(path, "w") as out:
        if access_units:
            # A stream from add_stream("h264") opens an x264 encoder whose own
            # SPS/PPS land in the avcC: FFmpeg still plays the file off the
            # in-band ones, browsers do not. A template stream is never opened.
            keyframe = access_units[0][0]
            with av.open(io.BytesIO(keyframe), format="h264") as probe:
                video = out.add_stream_from_template(probe.streams.video[0])
            sps, pps = _parameter_sets(keyframe)
            video.codec_context.extradata = sps + pps
            video.time_base = Fraction(1, 90000)
        else:
            first = Image.open(io.BytesIO(jpegs[0]))
            video = out.add_stream("libx264", rate=25)
            video.codec_context.width, video.codec_context.height = first.size
            video.codec_context.pix_fmt = "yuv420p"
            video.codec_context.options = {"preset": "ultrafast"}
        audio = None
        if pcm:
            audio = out.add_stream("aac", rate=rate)
            audio.codec_context.layout = "mono"

        if access_units:
            origin = access_units[0][1]
            ticks = access_units[-1][1] - origin
            scale = 90000 * video_span / ticks if video_span > 0 and ticks > 0 else 1.0
            timed: list[tuple[bytes, int]] = []
            for data, timestamp in access_units:
                pts = round((timestamp - origin) * scale)
                if timed and pts <= timed[-1][1]:
                    continue  # out of order: the muxer needs monotonic timestamps
                timed.append((data, pts))
            # Without a duration the last frame falls outside the edit list
            ends = [pts for _, pts in timed[1:]]
            ends.append(2 * timed[-1][1] - timed[-2][1] if len(timed) > 1 else 3600)
            for (data, pts), end in zip(timed, ends, strict=True):
                packet = av.Packet(data)
                packet.pts = packet.dts = pts
                packet.duration = end - pts
                packet.time_base = Fraction(1, 90000)
                packet.stream = video
                out.mux(packet)
        else:
            for index, jpeg in enumerate(jpegs):
                image = Image.open(io.BytesIO(jpeg)).convert("RGB")
                frame = av.VideoFrame.from_image(image).reformat(format="yuv420p")
                frame.pts = index
                frame.time_base = Fraction(1, 25)
                out.mux(video.encode(frame))
            out.mux(video.encode(None))

        if audio is not None:
            samples = np.frombuffer(pcm, dtype=np.int16)
            start = int(audio_offset * rate)
            for index in range(0, len(samples), 1024):
                chunk = samples[index : index + 1024]
                frame = av.AudioFrame.from_ndarray(
                    chunk.reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate = rate
                frame.pts = start + index
                frame.time_base = Fraction(1, rate)
                out.mux(audio.encode(frame))
            out.mux(audio.encode(None))


def _create_encoded_video_track(source: Any) -> Any:
    """A video track handing the panel's H264 access units on as ``av.Packet``.

    aiortc's sender packs a pre-encoded packet instead of encoding a frame,
    so a WebRTC viewer costs no x264 work: the panel already encodes. Delivery
    starts at the first keyframe; parameter sets seen before it are prepended
    when that keyframe carries none of its own.
    """
    from fractions import Fraction

    import av
    from aiortc import MediaStreamTrack

    class _Track(MediaStreamTrack):  # type: ignore[misc]
        kind = "video"

        def __init__(self) -> None:
            super().__init__()
            self._queue: asyncio.Queue[tuple[bytes, int] | None] = asyncio.Queue()
            self._started = False
            self._sps = b""
            self._pps = b""
            # The newest keyframe with its parameter sets: what a viewer keeps
            # showing once the call ends (webrtc_bridge)
            self.last_keyframe: bytes | None = None

        def push(self, data: bytes, timestamp: int) -> None:
            self._queue.put_nowait((data, timestamp))

        def end(self) -> None:
            self._queue.put_nowait(None)

        def _remember_parameter_sets(self, data: bytes, types: list[int]) -> None:
            if 7 in types or 8 in types:
                sps, pps = _parameter_sets(data)
                self._sps, self._pps = sps or self._sps, pps or self._pps

        async def recv(self) -> Any:
            from aiortc.mediastreams import MediaStreamError

            while True:
                if self.readyState != "live":
                    raise MediaStreamError
                item = await self._queue.get()
                if item is None:
                    self.stop()
                    raise MediaStreamError
                data, timestamp = item
                types = _nal_types(data)
                self._remember_parameter_sets(data, types)
                if not self._started:
                    if 5 not in types:
                        continue
                    self._started = True
                    if 7 not in types:
                        data = self._sps + self._pps + data
                if 5 in types:
                    self.last_keyframe = data if 7 in types else self._sps + self._pps + data
                packet = av.Packet(data)
                packet.pts = timestamp
                packet.time_base = Fraction(1, 90000)
                return packet

    track = _Track()
    source.add_encoded_sink(track)
    return track


def _tap_encoded_video(transport: Any, track: Any, forward: Callable[[bytes, int], bool]) -> bool:
    """Forward every assembled access unit behind ``track`` to ``forward``.

    The receiver hands complete encoded frames to its decoder thread through
    a plain queue; wrapping that queue's ``put`` sees them on the event loop
    before decoding, at no extra cost, and ``forward`` decides whether the
    decoder gets the frame at all. Reaches through pymediasoup/aiortc
    internals: on any surprise every frame is decoded and the viewer falls
    back to re-encoded frames.
    """
    try:
        receiver = next(
            t.receiver
            for t in transport._handler._pc.getTransceivers()
            if t.receiver.track is track
        )
        decoder_queue = receiver._RTCRtpReceiver__decoder_queue
        original_put = decoder_queue.put

        def _put(item: Any, *args: Any, **kwargs: Any) -> None:
            if item is None or forward(item[1].data, item[1].timestamp):
                original_put(item, *args, **kwargs)

        decoder_queue.put = _put
    except Exception:
        _LOGGER.debug("Could not tap the encoded video", exc_info=True)
        return False
    return True


def _tune_udp_sockets(transport: Any) -> None:
    """Grow the receive buffer of the UDP sockets behind a mediasoup transport
    and read them in bulk.

    Reaches through pymediasoup/aiortc/aioice/asyncio internals, so anything
    unexpected is logged and ignored: the stream still works, just with the
    kernel default buffer and one packet per wake-up.
    """
    import socket

    try:
        pc = transport._handler._pc
        seen: set[int] = set()
        for transceiver in pc.getTransceivers():
            connection = transceiver.receiver.transport.transport._connection
            for protocol in connection._protocols:
                sock = protocol.transport.get_extra_info("socket")
                if sock is None or id(sock) in seen:
                    continue
                seen.add(id(sock))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RCVBUF)
                _drain_on_wakeup(protocol.transport)
    except Exception:
        _LOGGER.debug("Could not tune the UDP media sockets", exc_info=True)


def _create_switchable_audio_track() -> Any:
    """Create a SwitchableAudioTrack that inherits from aiortc's MediaStreamTrack.

    Whatever the source delivers (a 48 kHz file, the viewer microphone as
    decoded Opus stereo, the panel audio as 8 kHz PCMA) leaves the track as
    48 kHz mono s16 frames of 20 ms on one continuous timeline. aiortc's
    encoders build their resampler from the first frame and reject any later
    change of format, so the switching has to be invisible to them. Holes in
    a live source (lost packets, silence suppression) are filled with silence
    so the audio keeps real-time pacing instead of being squeezed together.
    """
    from collections import deque
    from fractions import Fraction

    import av
    from aiortc import MediaStreamTrack

    def _silence() -> Any:
        frame = av.AudioFrame(format="s16", layout="mono", samples=AUDIO_SAMPLES)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        return frame

    class _Track(MediaStreamTrack):  # type: ignore[misc]
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self._source: Any = None
            self._pts = 0
            self._resampler: Any = None
            self._resampler_key: tuple[str, str, int] | None = None
            self._pending: deque[Any] = deque()
            # (source seconds, our position) when the current source started
            self._anchor: tuple[float, int] | None = None

        def set_source(self, player_track: Any) -> None:
            self._source = player_track
            self._anchor = None

        def _fill_gap(self, frame: Any) -> None:
            pts = getattr(frame, "pts", None)
            rate = getattr(frame, "sample_rate", None)
            if pts is None or not rate:
                return
            time_base = getattr(frame, "time_base", None)
            seconds = float(pts * time_base) if time_base else pts / rate
            position = self._pts + len(self._pending) * AUDIO_SAMPLES
            if self._anchor is None:
                self._anchor = (seconds, position)
                return
            gap = self._anchor[1] + int((seconds - self._anchor[0]) * AUDIO_RATE) - position
            if gap > MAX_GAP_FILL:
                self._anchor = (seconds, position)
                return
            while gap >= AUDIO_SAMPLES:
                self._pending.append(_silence())
                gap -= AUDIO_SAMPLES

        def _normalize(self, frame: Any) -> None:
            self._fill_gap(frame)
            key = (frame.format.name, frame.layout.name, frame.sample_rate)
            if key != self._resampler_key:
                self._resampler = av.AudioResampler(
                    format="s16", layout="mono", rate=AUDIO_RATE, frame_size=AUDIO_SAMPLES
                )
                self._resampler_key = key
            self._pending.extend(self._resampler.resample(frame))

        def _stamp(self, frame: Any) -> Any:
            frame.sample_rate = AUDIO_RATE
            frame.time_base = Fraction(1, AUDIO_RATE)
            frame.pts = self._pts
            self._pts += frame.samples
            return frame

        async def recv(self) -> Any:
            while self._source and not self._pending:
                try:
                    # A live source (viewer microphone) may go quiet without
                    # ending; keep it and fill the gap with silence
                    frame = await asyncio.wait_for(self._source.recv(), SOURCE_TIMEOUT)
                except TimeoutError:
                    break
                except Exception:
                    self._source = None
                    break
                self._normalize(frame)
            if self._pending:
                return self._stamp(self._pending.popleft())

            await asyncio.sleep(0.02)
            return self._stamp(_silence())

    return _Track()


@dataclass
class TransportData:
    """WebRTC transport parameters from mediasoup."""

    id: str
    dtls_parameters: str
    ice_candidates: str
    ice_parameters: str


@dataclass
class RoomJoinResult:
    """Result of joining a mediasoup room."""

    video_producer_id: str
    audio_producer_id: str
    router_rtp_capabilities: str
    recv_video_transport: TransportData
    recv_audio_transport: TransportData
    send_transport: TransportData
    ice_servers: str | None = None


@dataclass
class ConsumeResult:
    """Result of a transport_consume request."""

    consumer_id: str
    producer_id: str
    kind: str
    rtp_parameters: Any


class FermaxSignalingClient:
    """Socket.IO client for Fermax Blue mediasoup signaling."""

    def __init__(
        self,
        signaling_url: str = DEFAULT_SIGNALING_URL,
        oauth_token: str = "",
        fcm_token: str = "",
        send_hangup: bool = True,
    ) -> None:
        self._signaling_url = signaling_url
        self._oauth_token = oauth_token
        self._fcm_token = fcm_token
        self._send_hangup = send_hangup
        self._sio: socketio.AsyncClient | None = None
        self._connected = False
        self._room_join_result: RoomJoinResult | None = None
        self._on_end_up: Callable[[str], None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def room_join_result(self) -> RoomJoinResult | None:
        return self._room_join_result

    async def connect(self, room_id: str) -> RoomJoinResult | None:
        """Connect to signaling server and join a room."""
        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False)

        @self._sio.event
        async def connect() -> None:
            self._connected = True

        @self._sio.event
        async def disconnect() -> None:
            self._connected = False

        @self._sio.on("end_up")
        async def on_end_up(data: Any) -> None:
            code = data.get("code", "") if isinstance(data, dict) else str(data)
            _LOGGER.info("Call ended: %s", code)
            if self._on_end_up:
                self._on_end_up(code)

        try:
            await self._sio.connect(self._signaling_url, transports=["websocket"])

            response = await self._sio.call(
                "join_call",
                {
                    "roomId": room_id,
                    "appToken": self._fcm_token,
                    "fermaxOauthToken": self._oauth_token,
                    "protocolVersion": SIGNALING_VERSION,
                },
                timeout=15,
            )

            if not isinstance(response, dict) or "error" in response:
                _LOGGER.error("join_call failed: %s", response)
                return None

            result_data = response.get("result", {})
            if not result_data:
                return None

            recv_video = result_data.get("recvTransportVideo", {})
            recv_audio = result_data.get("recvTransportAudio", {})
            send = result_data.get("sendTransport", {})

            self._room_join_result = RoomJoinResult(
                video_producer_id=result_data.get("producerIdVideo", ""),
                audio_producer_id=result_data.get("producerIdAudio", ""),
                router_rtp_capabilities=json.dumps(result_data.get("routerRtpCapabilities", {})),
                recv_video_transport=self._parse_transport(recv_video),
                recv_audio_transport=self._parse_transport(recv_audio),
                send_transport=self._parse_transport(send),
                ice_servers=json.dumps(result_data.get("iceServers", [])),
            )

            _LOGGER.info(
                "Room joined: video=%s audio=%s",
                self._room_join_result.video_producer_id,
                self._room_join_result.audio_producer_id,
            )
            return self._room_join_result

        except Exception:
            _LOGGER.exception("Failed to connect to signaling server")
            return None

    @staticmethod
    def _parse_transport(data: dict) -> TransportData:
        return TransportData(
            id=data.get("id", ""),
            dtls_parameters=json.dumps(data.get("dtlsParameters", {})),
            ice_candidates=json.dumps(data.get("iceCandidates", [])),
            ice_parameters=json.dumps(data.get("iceParameters", {})),
        )

    async def consume_transport(
        self, transport_id: str, producer_id: str, rtp_capabilities: str
    ) -> ConsumeResult | None:
        """Request to consume a media track."""
        if not self._sio or not self._connected:
            return None

        try:
            # rtp_capabilities may be JSON string or dict
            caps = (
                json.loads(rtp_capabilities)
                if isinstance(rtp_capabilities, str)
                else rtp_capabilities
            )
            response = await self._sio.call(
                "transport_consume",
                {
                    "transportId": transport_id,
                    "producerId": producer_id,
                    "rtpCapabilities": caps,
                },
                timeout=10,
            )

            if not isinstance(response, dict) or "error" in response:
                _LOGGER.error("transport_consume error: %s", response)
                return None

            result = response.get("result", {})
            return ConsumeResult(
                consumer_id=result.get("id", ""),
                producer_id=result.get("producerId", ""),
                kind=result.get("kind", ""),
                rtp_parameters=result.get("rtpParameters", {}),
            )
        except Exception:
            _LOGGER.exception("Failed to consume transport")
            return None

    async def connect_transport(self, transport_id: str, dtls_parameters: str) -> bool:
        """Connect a transport with DTLS parameters."""
        if not self._sio or not self._connected:
            return False

        try:
            dtls = (
                json.loads(dtls_parameters) if isinstance(dtls_parameters, str) else dtls_parameters
            )
            response = await self._sio.call(
                "transport_connect",
                {"transportId": transport_id, "dtlsParameters": dtls},
                timeout=10,
            )
            return isinstance(response, dict) and "error" not in response
        except Exception:
            _LOGGER.exception("Failed to connect transport")
            return False

    async def pickup(
        self,
        kind: str,
        rtp_parameters: str,
        app_data: str,
        rtp_capabilities: str,
    ) -> dict | None:
        """Signal pickup — matches APK's PickupCall JSON structure.

        Returns the pickup ACK result dict with:
          - producerId: server-assigned ID for our audio producer
          - consumer.producerId: remote audio producer ID to consume
        """
        if not self._sio or not self._connected:
            return None

        try:
            caps = (
                json.loads(rtp_capabilities)
                if isinstance(rtp_capabilities, str)
                else rtp_capabilities
            )
            rtp = json.loads(rtp_parameters) if isinstance(rtp_parameters, str) else rtp_parameters
            app = json.loads(app_data) if isinstance(app_data, str) else app_data

            # APK sends: {"parameters": {kind, rtpParameters, appData}, "rtpCapabilities": ...}
            response = await self._sio.call(
                "pickup",
                {
                    "parameters": {
                        "kind": kind,
                        "rtpParameters": rtp,
                        "appData": app,
                    },
                    "rtpCapabilities": caps,
                },
                timeout=10,
            )
            _LOGGER.info(
                "Pickup response: %s",
                json.dumps(response)[:300] if response else "None",
            )

            if isinstance(response, dict) and "error" not in response:
                result: dict = response.get("result", {})
                return result
            return None
        except Exception:
            _LOGGER.debug("Pickup failed", exc_info=True)
            return None

    async def hangup(self) -> None:
        if self._sio and self._connected:
            try:
                await self._sio.emit("hang_up", {})
            except Exception:
                _LOGGER.debug("Error during hangup", exc_info=True)

    async def disconnect(self) -> None:
        if self._sio:
            try:
                # A session that never picked up leaves silently: hang_up on a
                # still-ringing call could end it for the visitor and monitor.
                if self._connected and self._send_hangup:
                    await self.hangup()
                await self._sio.disconnect()
            except Exception:
                _LOGGER.debug("Error during disconnect", exc_info=True)
            finally:
                self._sio = None
                self._connected = False
                self._room_join_result = None


class FermaxStreamSession:
    """Full streaming session: signaling + mediasoup consumer + frame grabber.

    Bridges the mediasoup SFU video to JPEG frames for the HA camera entity.
    """

    def __init__(
        self,
        signaling_url: str,
        oauth_token: str,
        fcm_token: str,
        room_id: str,
        on_end: Callable[[], None] | None = None,
        media_root: str = "/media",
        receive_only: bool = False,
        full_decode: Callable[[], bool] | None = None,
    ) -> None:
        # Enforce secure scheme for signaling URL
        if signaling_url and not signaling_url.startswith(("https://", "wss://")):
            _LOGGER.warning(
                "Signaling URL uses insecure scheme, upgrading to HTTPS: %s",
                signaling_url,
            )
            signaling_url = signaling_url.replace("http://", "https://", 1).replace(
                "ws://", "wss://", 1
            )
        self._signaling = FermaxSignalingClient(
            signaling_url=signaling_url,
            oauth_token=oauth_token,
            fcm_token=fcm_token,
            send_hangup=not receive_only,
        )
        self._receive_only = receive_only
        self._room_id = room_id
        self._on_end = on_end
        self._media_root = media_root
        self._device: Any = None
        self._recv_transport: Any = None
        self._recv_audio_transport: Any = None
        self._send_transport: Any = None
        self._audio_producer: Any = None
        self._consumer: Any = None
        self._audio_consumer: Any = None
        self._recorder: Any = None
        self._frame_task: asyncio.Task | None = None
        self._latest_frame: bytes | None = None
        self._last_raw_frame: bytes | None = None
        self._active = False
        self._room: Any = None
        self._recording_path: str | None = None
        self._audio_task: asyncio.Task | None = None
        self._relay: Any = None
        self._switchable_track: Any = None
        self._audio_sinks: list[Any] = []
        self._encoded_sinks: list[Any] = []
        self._clock_first: int | None = None
        self._clock_scale: int | None = None
        self._encoded_tapped = False
        self._stopping = False
        # Whether every frame must be decoded (an MJPEG viewer is watching);
        # otherwise only keyframes are, for the stills and the snapshot
        self._full_decode = full_decode or (lambda: True)
        self._decoding = True
        self._video_stats = [0, 0, time.monotonic()]  # access units, keyframes, since
        # Wall-clock start of the recorded video and audio, to align them in the MP4
        self._recording_video_wall: float | None = None
        self._recording_audio_wall: float | None = None
        self._device_caps: Any = None
        self._audio_tp: TransportData | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def latest_frame(self) -> bytes | None:
        """Return the latest JPEG frame, or None if no frames yet."""
        return self._latest_frame

    @property
    def latest_frame_raw(self) -> bytes | None:
        """Return the latest frame without the LIVE overlay (for still previews)."""
        return self._last_raw_frame or self._latest_frame

    @property
    def picked_up(self) -> bool:
        """Whether the call has been answered (our audio producer is published)."""
        return self._audio_producer is not None

    def subscribe_video(self) -> Any | None:
        """Return a new reader of the panel video, or None before the session is up."""
        if not self._relay or not self._consumer:
            return None
        return self._relay.subscribe(self._consumer.track)

    def subscribe_encoded_video(self) -> Any | None:
        """Return a track of the panel's H264 packets, or None before the session is up."""
        if not self._consumer or not self._encoded_tapped:
            return None
        return _create_encoded_video_track(self)

    def add_encoded_sink(self, sink: Any) -> None:
        """Register a track fed by _forward_encoded (see subscribe_encoded_video)."""
        self._encoded_sinks.append(sink)

    def _to_90khz(self, timestamp: int) -> int:
        """Rebase the panel's RTP clock onto the 90 kHz the codec advertises.

        The panels seen so far stamp at 1 kHz: consecutive frames sit a few
        dozen ticks apart instead of a few thousand, which made a 90 s call
        span one second for every consumer downstream. The clock is sniffed
        from the first gap of the session and applied from then on.
        """
        # ponytail: two clocks known (1 kHz, 90 kHz); measure the ratio if a third shows up
        if self._clock_first is None:
            self._clock_first = timestamp
        gap = timestamp - self._clock_first
        if self._clock_scale is None:
            if gap <= 0:
                return 0
            self._clock_scale = 90 if gap < 1000 else 1
        return gap * self._clock_scale

    def _forward_encoded(self, data: bytes, timestamp: int) -> bool:
        """Route one access unit: recording, WebRTC viewers, and whether to decode it."""
        timestamp = self._to_90khz(timestamp)
        keyframe = 5 in _nal_types(data)
        recording = getattr(self, "_recording_video", None)
        if recording is not None and (recording or keyframe):
            if not recording:
                self._recording_video_wall = time.monotonic()
            self._recording_video_wall_last = time.monotonic()
            recording.append((data, timestamp))
        for sink in self._encoded_sinks:
            if sink.readyState == "live":
                sink.push(data, timestamp)
        self._encoded_sinks = [s for s in self._encoded_sinks if s.readyState == "live"]

        wants_all = self._full_decode()
        if keyframe:
            self._decoding = wants_all  # the decoder (re)joins the stream at a keyframe
        elif not wants_all:
            self._decoding = False
        stats = self._video_stats
        stats[0] += 1
        stats[1] += keyframe
        if stats[0] % 500 == 0:
            _LOGGER.debug(
                "Panel video: %d access units, %d keyframes in %.1fs (decoding all: %s)",
                stats[0],
                stats[1],
                time.monotonic() - stats[2],
                self._decoding,
            )
        return keyframe or self._decoding

    def attach_audio_sink(self, sink: Any) -> None:
        """Feed the panel audio to a switchable track once the call is answered.

        The sink keeps sending silence until the panel audio exists (it only
        does after pickup).
        """
        self._audio_sinks.append(sink)
        self._attach_audio_sinks()

    def _attach_audio_sinks(self) -> None:
        if not self._audio_consumer or not self._relay:
            return
        for sink in self._audio_sinks:
            if sink._source is None and sink.readyState == "live":
                sink.set_source(self._relay.subscribe(self._audio_consumer.track))

    def set_audio_source(self, source: Any) -> None:
        """Feed a live audio source (viewer microphone) to the panel."""
        if self._switchable_track:
            self._switchable_track.set_source(source)

    async def pickup(self) -> bool:
        """Answer the call: publish our audio producer, which triggers the pickup
        signal and, on its ACK, the panel audio consumer.

        Idempotent; a receive-only session becomes a normal one.
        """
        if self._audio_producer is not None:
            return True
        if not self._send_transport:
            return False
        try:
            self._switchable_track = _create_switchable_audio_track()
            self._audio_producer = await self._send_transport.produce(
                track=self._switchable_track,
                stopTracks=False,
                appData={},
            )
        except Exception:
            if self._stopping:
                # Torn down while answering (hang-up, or a new room replacing it)
                _LOGGER.debug("Pickup interrupted: session stopped", exc_info=True)
            else:
                _LOGGER.exception("Pickup failed")
            return False
        self._receive_only = False
        self._signaling._send_hangup = True
        if self._active and self._audio_consumer and self._audio_task is None:
            self._audio_task = asyncio.create_task(self._grab_audio())
        _LOGGER.info("Audio producer started, pickup completed")
        return True

    async def start(self) -> bool:
        """Start the full streaming pipeline."""
        try:
            return await self._start_inner()
        except ImportError as err:
            # Live video needs pymediasoup + aiortc. They are manifest
            # requirements again, but may be missing on installs that upgraded
            # while the deps were optional (v0.16.8) until HA reinstalls
            # requirements. The rest of the integration (door open, calls,
            # sensors) works fine; only live streaming is unavailable.
            _LOGGER.warning(
                "Fermax Blue live video is unavailable: streaming dependencies "
                "(pymediasoup/aiortc) are not installed. Restart Home Assistant "
                "so it installs the integration requirements. Everything else "
                "works normally. (%s)",
                err,
            )
            await self.stop()
            return False
        except Exception:
            _LOGGER.exception("Failed to start stream session")
            await self.stop()
            return False

    async def _start_inner(self) -> bool:
        global _PYMEDIASOUP_PATCHED
        from pymediasoup import Device
        from pymediasoup.handlers.aiortc_handler import AiortcHandler
        from pymediasoup.models.transport import (
            DtlsParameters,
            IceCandidate,
            IceParameters,
        )
        from pymediasoup.rtp_parameters import RtpCapabilities, RtpParameters

        if not _PYMEDIASOUP_PATCHED:
            _patch_pymediasoup_audio_channels()
            _PYMEDIASOUP_PATCHED = True

        # 1. Signaling: join room
        room = await self._signaling.connect(self._room_id)
        if not room:
            _LOGGER.error("Failed to join room %s", self._room_id)
            return False

        _loop = asyncio.get_running_loop()

        def _handle_end_up(_code: str) -> None:
            _loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.stop()))

        self._signaling._on_end_up = _handle_end_up

        # 2. Create mediasoup Device (audio channels patch applied at module level)
        self._device = Device(handlerFactory=AiortcHandler.createFactory(tracks=[]))
        router_caps = json.loads(room.router_rtp_capabilities)
        await self._device.load(RtpCapabilities(**router_caps))

        # 3. Create RecvTransport for video
        video_tp = room.recv_video_transport
        ice_params = json.loads(video_tp.ice_parameters)
        ice_candidates = json.loads(video_tp.ice_candidates)
        dtls_params = json.loads(video_tp.dtls_parameters)

        self._recv_transport = self._device.createRecvTransport(
            id=video_tp.id,
            iceParameters=IceParameters(**ice_params),
            iceCandidates=[IceCandidate(**c) for c in ice_candidates],
            dtlsParameters=DtlsParameters(**dtls_params),
        )

        # Handle transport connect callback
        @self._recv_transport.on("connect")
        async def on_connect(dtls_parameters: DtlsParameters) -> None:
            await self._signaling.connect_transport(
                transport_id=video_tp.id,
                dtls_parameters=json.dumps(dtls_parameters.dict(exclude_none=True)),
            )

        # 4. Consume video from SFU
        device_caps = self._device.rtpCapabilities
        consume_result = await self._signaling.consume_transport(
            transport_id=video_tp.id,
            producer_id=room.video_producer_id,
            rtp_capabilities=json.dumps(device_caps.dict(exclude_none=True)),
        )
        if not consume_result:
            _LOGGER.error("Failed to consume video")
            return False

        self._consumer = await self._recv_transport.consume(
            id=consume_result.consumer_id,
            producerId=consume_result.producer_id,
            kind=consume_result.kind,
            rtpParameters=RtpParameters(**consume_result.rtp_parameters)
            if isinstance(consume_result.rtp_parameters, dict)
            else consume_result.rtp_parameters,
        )
        _tune_udp_sockets(self._recv_transport)
        self._encoded_tapped = _tap_encoded_video(
            self._recv_transport, self._consumer.track, self._forward_encoded
        )
        # Fan-out: the frame grabber, the recorder and any WebRTC viewer read
        # the same consumer tracks through relay proxies
        from aiortc.contrib.media import MediaRelay

        self._relay = MediaRelay()
        self._device_caps = device_caps

        # 4b. Create RecvTransport for audio (but DON'T consume yet — app does this after pickup)
        if room.audio_producer_id:
            audio_tp = room.recv_audio_transport
            self._audio_tp = audio_tp
            audio_ice = json.loads(audio_tp.ice_parameters)
            audio_candidates = json.loads(audio_tp.ice_candidates)
            audio_dtls = json.loads(audio_tp.dtls_parameters)

            self._recv_audio_transport = self._device.createRecvTransport(
                id=audio_tp.id,
                iceParameters=IceParameters(**audio_ice),
                iceCandidates=[IceCandidate(**c) for c in audio_candidates],
                dtlsParameters=DtlsParameters(**audio_dtls),
            )

            @self._recv_audio_transport.on("connect")
            async def on_audio_connect(dtls_parameters: DtlsParameters) -> None:
                await self._signaling.connect_transport(
                    transport_id=audio_tp.id,
                    dtls_parameters=json.dumps(dtls_parameters.dict(exclude_none=True)),
                )

        # 5. Create SendTransport for audio (app creates this during preview, before pickup)
        self._room = room
        send_tp = room.send_transport
        send_ice = json.loads(send_tp.ice_parameters)
        send_candidates = json.loads(send_tp.ice_candidates)
        send_dtls = json.loads(send_tp.dtls_parameters)

        self._send_transport = self._device.createSendTransport(
            id=send_tp.id,
            iceParameters=IceParameters(**send_ice),
            iceCandidates=[IceCandidate(**c) for c in send_candidates],
            dtlsParameters=DtlsParameters(**send_dtls),
            sctpParameters=None,
        )

        @self._send_transport.on("connect")
        async def on_send_connect(dtls_parameters: DtlsParameters) -> None:
            await self._signaling.connect_transport(
                transport_id=send_tp.id,
                dtls_parameters=json.dumps(dtls_parameters.dict(exclude_none=True)),
            )

        @self._send_transport.on("produce")
        async def on_produce(
            kind: str,
            rtp_parameters: Any,
            app_data: Any,
        ) -> str:
            # Pickup: send produce params to server (matching APK structure)
            rtp_json = (
                json.dumps(rtp_parameters.dict(exclude_none=True))
                if hasattr(rtp_parameters, "dict")
                else json.dumps(rtp_parameters)
            )
            pickup_result = await self._signaling.pickup(
                kind=kind,
                rtp_parameters=rtp_json,
                app_data=json.dumps(app_data) if app_data else "{}",
                rtp_capabilities=json.dumps(device_caps.dict(exclude_none=True)),
            )
            if not pickup_result:
                _LOGGER.error("Pickup failed for %s", kind)
                return ""

            # Extract our producer ID (server-assigned)
            our_producer_id = pickup_result.get("producerId", "")
            _LOGGER.info("Pickup OK: our_producer=%s", our_producer_id)

            # After pickup ACK: consume remote audio (matching APK sequence)
            remote_audio_id = pickup_result.get("consumer", {}).get("producerId", "")
            if remote_audio_id and self._recv_audio_transport and self._audio_tp:
                audio_consume = await self._signaling.consume_transport(
                    transport_id=self._audio_tp.id,
                    producer_id=remote_audio_id,
                    rtp_capabilities=json.dumps(device_caps.dict(exclude_none=True)),
                )
                if audio_consume:
                    self._audio_consumer = await self._recv_audio_transport.consume(
                        id=audio_consume.consumer_id,
                        producerId=audio_consume.producer_id,
                        kind=audio_consume.kind,
                        rtpParameters=RtpParameters(**audio_consume.rtp_parameters)
                        if isinstance(audio_consume.rtp_parameters, dict)
                        else audio_consume.rtp_parameters,
                    )
                    _LOGGER.info("Audio consumer created after pickup")
                    _tune_udp_sockets(self._recv_audio_transport)
                    self._attach_audio_sinks()

            return str(our_producer_id)

        # 6. Produce audio (48kHz like the APK) — triggers onProduce → pickup.
        # A receive-only session skips this: without pickup the call is never
        # answered, so it keeps ringing while we watch the video (like the
        # app's preview screen before attending).
        if self._receive_only:
            _LOGGER.info("Receive-only session, skipping pickup")
        elif not await self.pickup():
            return False

        # 8. Initialize recording (frames collected in _grab_frames)
        self._init_recording()

        # 9. Start frame grabber + audio recorder
        self._active = True
        self._frame_task = asyncio.create_task(self._grab_frames())
        if self._audio_consumer:
            self._audio_task = asyncio.create_task(self._grab_audio())
        _LOGGER.info("Stream session started for room %s", self._room_id)
        return True

    def _init_recording(self) -> None:
        """Initialize frame collection for recording."""
        try:
            from datetime import datetime

            recordings_dir = (
                self._media_root + "/fermax_recordings"
                if hasattr(self, "_media_root")
                else "/media/fermax_recordings"
            )
            os.makedirs(recordings_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._recording_path = f"{recordings_dir}/{timestamp}.mp4"
            self._recording_video: list[tuple[bytes, int]] = []
            self._recording_video_wall = None
            self._recording_frames: list[bytes] = []
            self._recording_audio_frames: list[bytes] = []
            self._recording_audio_wall = None
            self._recording_sent_audio: list[bytes] = []
            self._audio_sample_rate = 48000
            _LOGGER.info("Recording to %s", self._recording_path)
        except Exception:
            _LOGGER.debug("Recording not started", exc_info=True)
            self._recording_path = None

    def _mixed_audio(self) -> tuple[bytes, int]:
        """Received and sent audio summed into one mono PCM buffer at the received rate."""
        import numpy as np

        recv_rate = getattr(self, "_audio_sample_rate", 8000)
        recv_pcm = b"".join(getattr(self, "_recording_audio_frames", []))
        sent_pcm = b"".join(getattr(self, "_recording_sent_audio", []))

        # Resample sent audio (48kHz) to match received (8kHz) if needed
        if sent_pcm and recv_rate != 48000:
            sent_arr = np.frombuffer(sent_pcm, dtype=np.int16)
            ratio = recv_rate / 48000
            indices = np.arange(0, len(sent_arr), 1 / ratio).astype(int)
            indices = indices[indices < len(sent_arr)]
            sent_pcm = sent_arr[indices].tobytes()

        # Mix: pad shorter to match longer, then add
        recv_arr = np.frombuffer(recv_pcm, dtype=np.int16)
        sent_arr = np.frombuffer(sent_pcm, dtype=np.int16) if sent_pcm else np.zeros(0, np.int16)
        max_len = max(len(recv_arr), len(sent_arr))
        recv_arr = np.pad(recv_arr, (0, max_len - len(recv_arr)))
        sent_arr = np.pad(sent_arr, (0, max_len - len(sent_arr)))
        mixed = np.clip(recv_arr.astype(np.int32) + sent_arr.astype(np.int32), -32768, 32767)
        return mixed.astype(np.int16).tobytes(), recv_rate

    async def _save_recording(self) -> None:
        """Mux the collected video and audio into the MP4."""
        access_units = getattr(self, "_recording_video", [])
        jpegs = getattr(self, "_recording_frames", [])
        if not self._recording_path or not (access_units or jpegs):
            return

        pcm, rate = (b"", 0)
        if getattr(self, "_recording_audio_frames", None):
            pcm, rate = self._mixed_audio()
        video_wall = getattr(self, "_recording_video_wall", None)
        video_wall_last = getattr(self, "_recording_video_wall_last", None)
        audio_wall = getattr(self, "_recording_audio_wall", None)
        offset = span = 0.0
        if video_wall is not None and audio_wall is not None:
            offset = max(0.0, audio_wall - video_wall)
        if video_wall is not None and video_wall_last is not None:
            span = video_wall_last - video_wall
        try:
            await asyncio.to_thread(
                _write_mp4, self._recording_path, access_units, jpegs, pcm, rate, offset, span
            )
            size = await asyncio.to_thread(os.path.getsize, self._recording_path)
            _LOGGER.info(
                "Recording saved: %s (%d KB, audio=%s)",
                self._recording_path,
                size // 1024,
                bool(pcm),
            )
        except Exception:
            _LOGGER.warning("Could not save recording %s", self._recording_path, exc_info=True)
            with contextlib.suppress(OSError):
                os.unlink(self._recording_path)
        finally:
            self._recording_video = []
            self._recording_frames = []
            self._recording_audio_frames = []
            self._recording_sent_audio = []

    @staticmethod
    def _overlay_live_indicator(img: Any) -> Any:
        """Draw a LIVE indicator and timestamp on the frame."""
        try:
            from datetime import datetime

            from PIL import ImageDraw, ImageFont

            draw = ImageDraw.Draw(img)
            now = datetime.now().strftime("%H:%M:%S")
            font = _overlay_font(ImageFont)

            # Red "LIVE" badge top-left; the dot is drawn as an ellipse
            # because the default PIL font has no glyph for U+25CF, and the
            # badge width is sized to the rendered text
            text = f"LIVE {now}"
            text_w = draw.textlength(text, font=font)
            draw.rectangle([(6, 6), (26 + text_w + 8, 28)], fill=(200, 0, 0))
            draw.ellipse([(12, 12), (22, 22)], fill=(255, 255, 255))
            draw.text((26, 7), text, fill=(255, 255, 255), font=font)
        except Exception:
            pass  # Never let overlay failure break the stream

        return img

    async def _grab_audio(self) -> None:
        """Capture audio frames from the intercom for recording."""
        from aiortc.mediastreams import MediaStreamError

        source = self._audio_consumer.track
        track = self._relay.subscribe(source)
        count = 0
        first_pts: int | None = None
        started = time.monotonic()
        try:
            while self._active:
                frame = await track.recv()
                count += 1
                pts = getattr(frame, "pts", None)
                if first_pts is None:
                    first_pts = pts
                elif count % 250 == 0 and pts is not None and frame.sample_rate:
                    # Gaps show as span >> count * frame length; a wall time
                    # well above the span means frames are queueing up
                    _LOGGER.debug(
                        "Panel audio: %d frames spanning %.1fs at %d Hz in %.1fs wall"
                        " (queued: decoder=%s relay=%s)",
                        count,
                        (pts - first_pts) / frame.sample_rate,
                        frame.sample_rate,
                        time.monotonic() - started,
                        _queue_depth(source),
                        _queue_depth(track),
                    )
                if (
                    hasattr(self, "_recording_audio_frames")
                    and self._recording_audio_frames is not None
                ):
                    # Convert audio frame to raw PCM bytes
                    if not self._recording_audio_frames:
                        self._recording_audio_wall = time.monotonic()
                    self._audio_sample_rate = frame.sample_rate
                    raw = frame.to_ndarray().tobytes()
                    self._recording_audio_frames.append(raw)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception:
            _LOGGER.debug("Audio grabber error", exc_info=True)

    @classmethod
    def _encode_jpegs(cls, frame: Any) -> tuple[bytes, bytes]:
        """Return (overlay-free, LIVE-badged) JPEGs of a decoded video frame.

        Two JPEG encodes per frame at ~20 fps is the heaviest work in the
        session; it runs in a worker thread so the event loop keeps pumping
        audio and WebRTC packets meanwhile.
        """
        img = frame.to_image()
        raw_buf = io.BytesIO()
        img.save(raw_buf, format="JPEG", quality=75)
        img = cls._overlay_live_indicator(img)
        display_buf = io.BytesIO()
        img.save(display_buf, format="JPEG", quality=75)
        return raw_buf.getvalue(), display_buf.getvalue()

    async def _grab_frames(self) -> None:
        """Read video frames from the consumer track, encode as JPEG."""
        from aiortc.mediastreams import MediaStreamError

        track = self._relay.subscribe(self._consumer.track)
        _LOGGER.info("Frame grabber started, track kind=%s", track.kind)
        frame_count = 0
        try:
            while self._active:
                frame = await track.recv()
                frame_count += 1
                raw_jpeg, display_jpeg = await asyncio.to_thread(self._encode_jpegs, frame)

                # Without the encoded video, the recording is built from these frames
                if (
                    not self._encoded_tapped
                    and getattr(self, "_recording_frames", None) is not None
                ):
                    if not self._recording_frames:
                        self._recording_video_wall = time.monotonic()
                    self._recording_frames.append(raw_jpeg)
                # Keep the overlay-free frame so still previews after the
                # stream ends don't show a stale "LIVE" badge (see stop())
                self._last_raw_frame = raw_jpeg
                self._latest_frame = display_jpeg
                if frame_count == 1:
                    _LOGGER.info("First frame received: %d bytes", len(self._latest_frame))
                elif frame_count % 100 == 0:
                    _LOGGER.debug("Frame %d received", frame_count)
        except MediaStreamError:
            _LOGGER.info("Video track ended after %d frames", frame_count)
        except asyncio.CancelledError:
            _LOGGER.info("Frame grabber cancelled after %d frames", frame_count)
        except Exception:
            _LOGGER.exception("Frame grabber error after %d frames", frame_count)
        finally:
            self._active = False
            if self._on_end:
                self._on_end()

    async def stop(self) -> None:
        """Stop the streaming session and clean up (idempotent).

        The local timer and the server's hang-up push both stop the session
        within a second of each other; the second call must not save the
        recording again.
        """
        if self._stopping:
            return
        self._stopping = True
        self._active = False

        if self._frame_task and not self._frame_task.done():
            self._frame_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._frame_task

        if self._audio_task and not self._audio_task.done():
            self._audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._audio_task
        self._audio_task = None
        for sink in self._encoded_sinks:
            sink.end()
        self._encoded_sinks = []

        # Save recording from collected frames
        await self._save_recording()

        # Close in order: consumers → transports → signaling
        if self._consumer:
            with contextlib.suppress(Exception):
                await self._consumer.close()
            self._consumer = None

        if self._audio_consumer:
            with contextlib.suppress(Exception):
                await self._audio_consumer.close()
            self._audio_consumer = None

        if self._audio_producer:
            with contextlib.suppress(Exception):
                await self._audio_producer.close()
            self._audio_producer = None

        if self._recv_transport:
            with contextlib.suppress(Exception):
                await self._recv_transport.close()
            self._recv_transport = None

        if self._recv_audio_transport:
            with contextlib.suppress(Exception):
                await self._recv_audio_transport.close()
            self._recv_audio_transport = None

        if self._send_transport:
            with contextlib.suppress(Exception):
                await self._send_transport.close()
            self._send_transport = None

        await self._signaling.disconnect()

        # Give aiortc a moment to clean up internal tasks
        await asyncio.sleep(0.1)

        # Keep _latest_frame for preview after stream ends, but swap in the
        # overlay-free version - a frozen "LIVE HH:MM:SS" badge is misleading
        if self._last_raw_frame:
            self._latest_frame = self._last_raw_frame
        _LOGGER.info("Stream session stopped")

    async def send_audio(self, audio_path: str) -> bool:
        """Send an audio file to the intercom via mediasoup.

        Reads the audio file, resamples to 48kHz mono s16, and feeds frames
        to the switchable track (replacing silence with real audio).
        """
        if not self._active or not self._audio_producer:
            _LOGGER.error("Cannot send audio: no active stream/producer")
            return False

        try:
            import av

            # Read and resample audio to match the producer format (48kHz mono s16)
            container = av.open(audio_path)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

            import numpy as np

            all_samples: list[Any] = []
            for packet in container.demux(audio=0):
                for decoded in packet.decode():
                    for resampled in resampler.resample(decoded):  # type: ignore[arg-type]
                        all_samples.append(resampled.to_ndarray().flatten())
            container.close()

            if not all_samples:
                _LOGGER.error("No audio in %s", audio_path)
                return False

            # Chunk into 960-sample frames (matching silence track)
            raw = np.concatenate(all_samples)
            chunk_size = 960
            frames: list[Any] = []
            pts = 0
            for i in range(0, len(raw), chunk_size):
                chunk = raw[i : i + chunk_size]
                if len(chunk) < chunk_size:
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                frame = av.AudioFrame(format="s16", layout="mono", samples=chunk_size)
                frame.planes[0].update(chunk.astype(np.int16).tobytes())
                frame.sample_rate = 48000
                frame.pts = pts
                pts += chunk_size
                frames.append(frame)

            frame_queue: asyncio.Queue[Any] = asyncio.Queue()
            for f in frames:
                await frame_queue.put(f)

            class _FileAudioSource:
                async def recv(self) -> Any:
                    if frame_queue.empty():
                        raise StopIteration
                    return await frame_queue.get()

            # Save sent audio PCM for recording mix
            if hasattr(self, "_recording_sent_audio"):
                for i in range(0, len(raw), chunk_size):
                    chunk = raw[i : i + chunk_size]
                    if len(chunk) < chunk_size:
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                    self._recording_sent_audio.append(chunk.astype(np.int16).tobytes())

            self._switchable_track.set_source(_FileAudioSource())
            _LOGGER.info("Audio playing: %s (%d frames)", audio_path, len(frames))
            return True

        except Exception:
            _LOGGER.exception("Failed to send audio from %s", audio_path)
            return False
