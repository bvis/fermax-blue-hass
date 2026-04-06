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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import socketio

_LOGGER = logging.getLogger(__name__)

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


_patch_pymediasoup_audio_channels()
DEFAULT_SIGNALING_URL = "http://signaling-pro-duoxme.fermax.io"


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
    ) -> None:
        self._signaling_url = signaling_url
        self._oauth_token = oauth_token
        self._fcm_token = fcm_token
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
                router_rtp_capabilities=json.dumps(
                    result_data.get("routerRtpCapabilities", {})
                ),
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
                json.loads(dtls_parameters)
                if isinstance(dtls_parameters, str)
                else dtls_parameters
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
    ) -> bool:
        """Signal pickup to keep the call alive."""
        if not self._sio or not self._connected:
            return False

        try:
            caps = (
                json.loads(rtp_capabilities)
                if isinstance(rtp_capabilities, str)
                else rtp_capabilities
            )
            rtp = (
                json.loads(rtp_parameters)
                if isinstance(rtp_parameters, str)
                else rtp_parameters
            )
            app = json.loads(app_data) if isinstance(app_data, str) else app_data
            response = await self._sio.call(
                "pickup",
                {
                    "kind": kind,
                    "rtpParameters": rtp,
                    "appData": app,
                    "rtpCapabilities": caps,
                },
                timeout=10,
            )
            _LOGGER.info("Pickup sent: %s", "OK" if response else "no response")
            return isinstance(response, dict) and "error" not in response
        except Exception:
            _LOGGER.debug("Pickup failed", exc_info=True)
            return False

    async def hangup(self) -> None:
        if self._sio and self._connected:
            try:
                await self._sio.emit("hang_up", {})
            except Exception:
                _LOGGER.debug("Error during hangup", exc_info=True)

    async def disconnect(self) -> None:
        if self._sio:
            try:
                if self._connected:
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
    ) -> None:
        self._signaling = FermaxSignalingClient(
            signaling_url=signaling_url,
            oauth_token=oauth_token,
            fcm_token=fcm_token,
        )
        self._room_id = room_id
        self._on_end = on_end
        self._device: Any = None
        self._recv_transport: Any = None
        self._send_transport: Any = None
        self._audio_producer: Any = None
        self._consumer: Any = None
        self._frame_task: asyncio.Task | None = None
        self._latest_frame: bytes | None = None
        self._active = False
        self._room: Any = None

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def latest_frame(self) -> bytes | None:
        """Return the latest JPEG frame, or None if no frames yet."""
        return self._latest_frame

    async def start(self) -> bool:
        """Start the full streaming pipeline."""
        try:
            return await self._start_inner()
        except Exception:
            _LOGGER.exception("Failed to start stream session")
            await self.stop()
            return False

    async def _start_inner(self) -> bool:
        from pymediasoup import Device
        from pymediasoup.handlers.aiortc_handler import AiortcHandler
        from pymediasoup.models.transport import (
            DtlsParameters,
            IceCandidate,
            IceParameters,
        )
        from pymediasoup.rtp_parameters import RtpCapabilities, RtpParameters

        # 1. Signaling: join room
        room = await self._signaling.connect(self._room_id)
        if not room:
            _LOGGER.error("Failed to join room %s", self._room_id)
            return False

        def _handle_end_up(_code: str) -> None:
            asyncio.get_event_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.stop())
            )

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

        # 5. Create SendTransport for audio
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
            # Server-side producer creation via signaling
            result = await self._signaling.pickup(
                kind=kind,
                rtp_parameters=json.dumps(rtp_parameters.dict(exclude_none=True))
                if hasattr(rtp_parameters, "dict")
                else json.dumps(rtp_parameters),
                app_data=json.dumps(app_data) if app_data else "{}",
                rtp_capabilities=json.dumps(device_caps.dict(exclude_none=True)),
            )
            _LOGGER.info("Pickup/produce for %s: %s", kind, result)
            # Return producer ID from pickup response (or empty)
            return room.audio_producer_id or ""

        # 6. Start frame grabber
        self._active = True
        self._frame_task = asyncio.create_task(self._grab_frames())
        _LOGGER.info("Stream session started for room %s", self._room_id)
        return True

    @staticmethod
    def _overlay_live_indicator(img: Any) -> Any:
        """Draw a LIVE indicator and timestamp on the frame."""
        try:
            from datetime import datetime

            from PIL import ImageDraw, ImageFont

            draw = ImageDraw.Draw(img)
            now = datetime.now().strftime("%H:%M:%S")
            font = ImageFont.load_default(size=16)

            # Red "● LIVE" badge top-left
            draw.rectangle([(6, 6), (130, 28)], fill=(200, 0, 0))
            draw.text((10, 7), f"\u25cf LIVE {now}", fill=(255, 255, 255), font=font)
        except Exception:
            pass  # Never let overlay failure break the stream

        return img

    async def _grab_frames(self) -> None:
        """Read video frames from the consumer track, encode as JPEG."""
        from aiortc.mediastreams import MediaStreamError

        track = self._consumer.track
        _LOGGER.info("Frame grabber started, track kind=%s", track.kind)
        frame_count = 0
        try:
            while self._active:
                frame = await track.recv()
                frame_count += 1
                img = frame.to_image()
                img = self._overlay_live_indicator(img)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75)
                self._latest_frame = buf.getvalue()
                if frame_count == 1:
                    _LOGGER.info(
                        "First frame received: %d bytes", len(self._latest_frame)
                    )
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
        """Stop the streaming session and clean up."""
        self._active = False

        if self._frame_task and not self._frame_task.done():
            self._frame_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._frame_task

        # Close in order: consumer → transport → signaling
        # Suppress aiortc internal errors during teardown
        if self._consumer:
            with contextlib.suppress(Exception):
                await self._consumer.close()
            self._consumer = None

        if self._audio_producer:
            with contextlib.suppress(Exception):
                await self._audio_producer.close()
            self._audio_producer = None

        if self._recv_transport:
            with contextlib.suppress(Exception):
                await self._recv_transport.close()
            self._recv_transport = None

        if self._send_transport:
            with contextlib.suppress(Exception):
                await self._send_transport.close()
            self._send_transport = None

        await self._signaling.disconnect()

        # Give aiortc a moment to clean up internal tasks
        await asyncio.sleep(0.1)

        # Keep _latest_frame for preview after stream ends
        _LOGGER.info("Stream session stopped")

    async def send_audio(self, audio_path: str) -> bool:
        """Send an audio file to the intercom via mediasoup.

        The audio file (WAV, MP3, OGG) is played through the sendTransport
        as an audio producer. The intercom speaker will play it.
        """
        if not self._active or not self._send_transport:
            _LOGGER.error("Cannot send audio: no active stream session")
            return False

        try:
            from aiortc.contrib.media import MediaPlayer

            player = MediaPlayer(audio_path)
            if not player.audio:
                _LOGGER.error("No audio track in file: %s", audio_path)
                return False

            self._audio_producer = await self._send_transport.produce(
                track=player.audio,
                stopTracks=False,
                appData={},
            )

            _LOGGER.info("Audio producer started from %s", audio_path)
            return True

        except Exception:
            _LOGGER.exception("Failed to send audio from %s", audio_path)
            return False
