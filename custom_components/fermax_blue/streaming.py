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


def _create_switchable_audio_track() -> Any:
    """Create a SwitchableAudioTrack that inherits from aiortc's MediaStreamTrack."""
    from aiortc import MediaStreamTrack

    class _Track(MediaStreamTrack):  # type: ignore[misc]
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self._source: Any = None
            self._pts = 0

        def set_source(self, player_track: Any) -> None:
            self._source = player_track

        async def recv(self) -> Any:
            if self._source:
                try:
                    return await self._source.recv()
                except Exception:
                    self._source = None

            import av

            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            for p in frame.planes:
                p.update(bytes(p.buffer_size))
            frame.sample_rate = 48000
            frame.pts = self._pts
            self._pts += 960
            await asyncio.sleep(0.02)
            return frame

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
            rtp = (
                json.loads(rtp_parameters)
                if isinstance(rtp_parameters, str)
                else rtp_parameters
            )
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
        self._recv_audio_transport: Any = None
        self._send_transport: Any = None
        self._audio_producer: Any = None
        self._consumer: Any = None
        self._audio_consumer: Any = None
        self._recorder: Any = None
        self._frame_task: asyncio.Task | None = None
        self._latest_frame: bytes | None = None
        self._active = False
        self._room: Any = None
        self._recording_path: str | None = None

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

        # 4b. Create RecvTransport for audio (but DON'T consume yet — app does this after pickup)
        if room.audio_producer_id:
            audio_tp = room.recv_audio_transport
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
            if remote_audio_id and self._recv_audio_transport:
                audio_consume = await self._signaling.consume_transport(
                    transport_id=audio_tp.id,
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

            return str(our_producer_id)

        # 6. Produce audio (48kHz like the APK) — triggers onProduce → pickup
        self._switchable_track = _create_switchable_audio_track()
        self._audio_producer = await self._send_transport.produce(
            track=self._switchable_track,
            stopTracks=False,
            appData={},
        )
        _LOGGER.info("Audio producer started, pickup completed")

        # 8. Initialize recording (frames collected in _grab_frames)
        self._init_recording()

        # 9. Start frame grabber + audio recorder
        self._active = True
        self._frame_task = asyncio.create_task(self._grab_frames())
        if self._audio_consumer:
            self._audio_task: asyncio.Task | None = asyncio.create_task(
                self._grab_audio()
            )
        else:
            self._audio_task = None
        _LOGGER.info("Stream session started for room %s", self._room_id)
        return True

    def _init_recording(self) -> None:
        """Initialize frame collection for recording."""
        try:
            from datetime import datetime

            recordings_dir = "/media/fermax_recordings"
            os.makedirs(recordings_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._recording_path = f"{recordings_dir}/{timestamp}.mp4"
            self._recording_frames: list[bytes] = []
            self._recording_audio_frames: list[bytes] = []
            self._recording_sent_audio: list[bytes] = []
            self._audio_sample_rate = 48000
            _LOGGER.info("Recording to %s", self._recording_path)
        except Exception:
            _LOGGER.debug("Recording not started", exc_info=True)
            self._recording_path = None

    async def _save_recording(self) -> None:
        """Convert collected video + audio frames to MP4 via ffmpeg."""
        video_frames = getattr(self, "_recording_frames", [])
        audio_frames = getattr(self, "_recording_audio_frames", [])
        if not self._recording_path or not video_frames:
            return

        import subprocess
        import tempfile

        mjpeg_fd, mjpeg_path = tempfile.mkstemp(suffix=".mjpeg")
        os.close(mjpeg_fd)
        pcm_fd, pcm_path = tempfile.mkstemp(suffix=".pcm")
        os.close(pcm_fd)
        has_audio = bool(audio_frames)
        try:
            await asyncio.to_thread(
                lambda: open(mjpeg_path, "wb").write(b"".join(video_frames))  # noqa: SIM115
            )
            if has_audio:
                import numpy as np

                # Mix received audio with sent audio for full recording
                sent_frames = getattr(self, "_recording_sent_audio", [])
                recv_pcm = b"".join(audio_frames)
                sent_pcm = b"".join(sent_frames) if sent_frames else b""

                # Resample sent audio (48kHz) to match received (8kHz) if needed
                recv_rate = getattr(self, "_audio_sample_rate", 8000)
                if sent_pcm and recv_rate != 48000:
                    sent_arr = np.frombuffer(sent_pcm, dtype=np.int16)
                    ratio = recv_rate / 48000
                    indices = np.arange(0, len(sent_arr), 1 / ratio).astype(int)
                    indices = indices[indices < len(sent_arr)]
                    sent_arr = sent_arr[indices]
                    sent_pcm = sent_arr.tobytes()

                # Mix: pad shorter to match longer, then add
                recv_arr = np.frombuffer(recv_pcm, dtype=np.int16)
                sent_arr = (
                    np.frombuffer(sent_pcm, dtype=np.int16)
                    if sent_pcm
                    else np.zeros(0, dtype=np.int16)
                )
                max_len = max(len(recv_arr), len(sent_arr))
                if len(recv_arr) < max_len:
                    recv_arr = np.pad(recv_arr, (0, max_len - len(recv_arr)))
                if len(sent_arr) < max_len:
                    sent_arr = np.pad(sent_arr, (0, max_len - len(sent_arr)))
                mixed = np.clip(
                    recv_arr.astype(np.int32) + sent_arr.astype(np.int32), -32768, 32767
                ).astype(np.int16)

                await asyncio.to_thread(
                    lambda: open(pcm_path, "wb").write(mixed.tobytes())  # noqa: SIM115
                )

            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "mjpeg",
                "-framerate",
                "25",
                "-i",
                mjpeg_path,
            ]
            if has_audio:
                sr = str(getattr(self, "_audio_sample_rate", 48000))
                cmd += [
                    "-f",
                    "s16le",
                    "-ar",
                    sr,
                    "-ac",
                    "1",
                    "-i",
                    pcm_path,
                ]
            cmd += [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
            ]
            if has_audio:
                cmd += ["-c:a", "aac", "-b:a", "64k"]
            cmd += ["-shortest", self._recording_path]

            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                timeout=60,
            )
            if proc.returncode == 0:
                size = await asyncio.to_thread(os.path.getsize, self._recording_path)
                _LOGGER.info(
                    "Recording saved: %s (%d KB, audio=%s)",
                    self._recording_path,
                    size // 1024,
                    has_audio,
                )
            else:
                _LOGGER.warning(
                    "ffmpeg exited with %d: %s", proc.returncode, proc.stderr[-200:]
                )
        except FileNotFoundError:
            _LOGGER.debug("ffmpeg not available, saving raw MJPEG")
            dest = self._recording_path.replace(".mp4", ".mjpeg")
            await asyncio.to_thread(
                lambda: open(dest, "wb").write(b"".join(video_frames))  # noqa: SIM115
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(mjpeg_path)
            with contextlib.suppress(OSError):
                os.unlink(pcm_path)
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
            font = ImageFont.load_default(size=16)

            # Red "● LIVE" badge top-left
            draw.rectangle([(6, 6), (130, 28)], fill=(200, 0, 0))
            draw.text((10, 7), f"\u25cf LIVE {now}", fill=(255, 255, 255), font=font)
        except Exception:
            pass  # Never let overlay failure break the stream

        return img

    async def _grab_audio(self) -> None:
        """Capture audio frames from the intercom for recording."""
        from aiortc.mediastreams import MediaStreamError

        track = self._audio_consumer.track
        try:
            while self._active:
                frame = await track.recv()
                if (
                    hasattr(self, "_recording_audio_frames")
                    and self._recording_audio_frames is not None
                ):
                    # Convert audio frame to raw PCM bytes
                    self._audio_sample_rate = frame.sample_rate
                    raw = frame.to_ndarray().tobytes()
                    self._recording_audio_frames.append(raw)
        except (MediaStreamError, asyncio.CancelledError):
            pass
        except Exception:
            _LOGGER.debug("Audio grabber error", exc_info=True)

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

                # Save raw frame for recording (without LIVE overlay)
                raw_buf = io.BytesIO()
                img.save(raw_buf, format="JPEG", quality=75)
                raw_jpeg = raw_buf.getvalue()
                if (
                    hasattr(self, "_recording_frames")
                    and self._recording_frames is not None
                ):
                    self._recording_frames.append(raw_jpeg)

                # Add LIVE overlay for display
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

        audio_task = getattr(self, "_audio_task", None)
        if audio_task and not audio_task.done():
            audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await audio_task

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

        # Keep _latest_frame for preview after stream ends
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
