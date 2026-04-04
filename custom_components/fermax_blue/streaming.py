"""Mediasoup signaling client for Fermax Blue video streaming.

Connects to the Fermax signaling server via Socket.IO and negotiates
mediasoup transport parameters for receiving video/audio streams.

The Fermax Blue streaming architecture:
1. Auto-on or doorbell ring triggers a call via the Fermax API
2. A push notification arrives with roomId and signalingServerUrl
3. This client connects via Socket.IO to the signaling server
4. join_call returns mediasoup router capabilities and transport data
5. transport_consume sets up the receive pipeline for video/audio
6. pickup starts the actual media flow

Signaling events (Socket.IO):
  Client → Server:
    - join_call: {roomId, pushToken, oauthToken, version}
    - transport_consume: {transportId, producerId, rtpCapabilities}
    - transport_connect: {transportId, dtlsParameters}
    - pickup: {kind, rtpParameters, appData, rtpCapabilities}
    - hang_up: {}

  Server → Client:
    - join_call ACK: {result: {producerIdVideo, producerIdAudio,
        routerRtpCapabilities, recvTransportVideo, recvTransportAudio,
        sendTransport}}
    - transport_consume ACK: {result: {id, producerId, kind, rtpParameters}}
    - end_up: {code}
    - error: {code}
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import socketio

_LOGGER = logging.getLogger(__name__)

SIGNALING_VERSION = "0.8.2"
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
    rtp_parameters: str


class FermaxSignalingClient:
    """Socket.IO client for Fermax Blue mediasoup signaling.

    Handles the signaling protocol to set up mediasoup transports
    for receiving video and audio from the intercom.
    """

    def __init__(
        self,
        signaling_url: str = DEFAULT_SIGNALING_URL,
        oauth_token: str = "",
        fcm_token: str = "",
        on_room_joined: Callable[[RoomJoinResult], None] | None = None,
        on_consume: Callable[[ConsumeResult], None] | None = None,
        on_end_up: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._signaling_url = signaling_url
        self._oauth_token = oauth_token
        self._fcm_token = fcm_token
        self._on_room_joined = on_room_joined
        self._on_consume = on_consume
        self._on_end_up = on_end_up
        self._on_error = on_error
        self._sio: socketio.AsyncClient | None = None
        self._connected = False
        self._room_join_result: RoomJoinResult | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if connected to signaling server."""
        return self._connected

    @property
    def room_join_result(self) -> RoomJoinResult | None:
        """Return the last room join result."""
        return self._room_join_result

    async def connect(self, room_id: str) -> RoomJoinResult | None:
        """Connect to signaling server and join a room.

        Returns the room join result with transport parameters,
        or None if the connection failed.
        """
        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        join_future: asyncio.Future[RoomJoinResult | None] = asyncio.Future()

        @self._sio.event
        async def connect() -> None:
            _LOGGER.info("Connected to signaling server")
            self._connected = True

        @self._sio.event
        async def disconnect() -> None:
            _LOGGER.info("Disconnected from signaling server")
            self._connected = False

        @self._sio.event
        async def connect_error(data: Any) -> None:
            _LOGGER.error("Signaling connection error: %s", data)
            if not join_future.done():
                join_future.set_result(None)

        @self._sio.on("end_up")
        async def on_end_up(data: Any) -> None:
            code = data.get("code", "") if isinstance(data, dict) else str(data)
            _LOGGER.info("Call ended: %s", code)
            if self._on_end_up:
                self._on_end_up(code)

        @self._sio.on("error")
        async def on_error(data: Any) -> None:
            code = data.get("code", "") if isinstance(data, dict) else str(data)
            _LOGGER.error("Signaling error: %s", code)
            if self._on_error:
                self._on_error(code)

        try:
            _LOGGER.info("Connecting to %s", self._signaling_url)
            await self._sio.connect(
                self._signaling_url,
                transports=["websocket"],
            )

            # Emit join_call and wait for ACK
            join_data = {
                "roomId": room_id,
                "pushToken": self._fcm_token,
                "oauthToken": self._oauth_token,
                "version": SIGNALING_VERSION,
            }

            _LOGGER.debug("Emitting join_call for room %s", room_id)
            response = await self._sio.call("join_call", join_data, timeout=15)

            if not isinstance(response, dict):
                _LOGGER.error("Unexpected join_call response: %s", response)
                return None

            if "error" in response:
                _LOGGER.error("join_call error: %s", response["error"])
                return None

            result_data = response.get("result", {})
            if not result_data:
                _LOGGER.error("join_call returned empty result")
                return None

            # Parse transport data
            recv_video = result_data.get("recvTransportVideo", {})
            recv_audio = result_data.get("recvTransportAudio", {})
            send = result_data.get("sendTransport", {})

            self._room_join_result = RoomJoinResult(
                video_producer_id=result_data.get("producerIdVideo", ""),
                audio_producer_id=result_data.get("producerIdAudio", ""),
                router_rtp_capabilities=json.dumps(
                    result_data.get("routerRtpCapabilities", {})
                ),
                recv_video_transport=TransportData(
                    id=recv_video.get("id", ""),
                    dtls_parameters=json.dumps(
                        recv_video.get("dtlsParameters", {})
                    ),
                    ice_candidates=json.dumps(
                        recv_video.get("iceCandidates", [])
                    ),
                    ice_parameters=json.dumps(
                        recv_video.get("iceParameters", {})
                    ),
                ),
                recv_audio_transport=TransportData(
                    id=recv_audio.get("id", ""),
                    dtls_parameters=json.dumps(
                        recv_audio.get("dtlsParameters", {})
                    ),
                    ice_candidates=json.dumps(
                        recv_audio.get("iceCandidates", [])
                    ),
                    ice_parameters=json.dumps(
                        recv_audio.get("iceParameters", {})
                    ),
                ),
                send_transport=TransportData(
                    id=send.get("id", ""),
                    dtls_parameters=json.dumps(
                        send.get("dtlsParameters", {})
                    ),
                    ice_candidates=json.dumps(
                        send.get("iceCandidates", [])
                    ),
                    ice_parameters=json.dumps(
                        send.get("iceParameters", {})
                    ),
                ),
                ice_servers=result_data.get("iceServers"),
            )

            _LOGGER.info(
                "Room joined: video=%s audio=%s",
                self._room_join_result.video_producer_id,
                self._room_join_result.audio_producer_id,
            )

            if self._on_room_joined:
                self._on_room_joined(self._room_join_result)

            return self._room_join_result

        except Exception:
            _LOGGER.exception("Failed to connect to signaling server")
            return None

    async def consume_transport(
        self,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: str,
    ) -> ConsumeResult | None:
        """Request to consume a media track from the server."""
        if not self._sio or not self._connected:
            return None

        consume_data = {
            "transportId": transport_id,
            "producerId": producer_id,
            "rtpCapabilities": rtp_capabilities,
        }

        try:
            response = await self._sio.call(
                "transport_consume", consume_data, timeout=10
            )

            if not isinstance(response, dict) or "error" in response:
                _LOGGER.error("transport_consume error: %s", response)
                return None

            result = response.get("result", {})
            consume_result = ConsumeResult(
                consumer_id=result.get("id", ""),
                producer_id=result.get("producerId", ""),
                kind=result.get("kind", ""),
                rtp_parameters=result.get("rtpParameters", ""),
            )

            _LOGGER.info(
                "Transport consume: %s (%s)",
                consume_result.consumer_id,
                consume_result.kind,
            )

            if self._on_consume:
                self._on_consume(consume_result)

            return consume_result

        except Exception:
            _LOGGER.exception("Failed to consume transport")
            return None

    async def connect_transport(
        self, transport_id: str, dtls_parameters: str
    ) -> bool:
        """Connect a transport with DTLS parameters."""
        if not self._sio or not self._connected:
            return False

        try:
            response = await self._sio.call(
                "transport_connect",
                {"transportId": transport_id, "dtlsParameters": dtls_parameters},
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
        """Answer the call (start sending audio to the intercom)."""
        if not self._sio or not self._connected:
            return False

        try:
            response = await self._sio.call(
                "pickup",
                {
                    "kind": kind,
                    "rtpParameters": rtp_parameters,
                    "appData": app_data,
                    "rtpCapabilities": rtp_capabilities,
                },
                timeout=10,
            )
            return isinstance(response, dict) and "error" not in response
        except Exception:
            _LOGGER.exception("Failed to pickup call")
            return False

    async def hangup(self) -> None:
        """Hang up the call."""
        if self._sio and self._connected:
            try:
                await self._sio.emit("hang_up", {})
            except Exception:
                _LOGGER.debug("Error during hangup", exc_info=True)

    async def disconnect(self) -> None:
        """Disconnect from the signaling server."""
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
