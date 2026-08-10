"""Tests for the streaming module."""

import asyncio
import contextlib
import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from aiortc.mediastreams import MediaStreamError
from PIL import Image

from custom_components.fermax_blue.streaming import (
    SIGNALING_VERSION,
    ConsumeResult,
    FermaxSignalingClient,
    FermaxStreamSession,
    RoomJoinResult,
    TransportData,
    _create_switchable_audio_track,
    _patch_pymediasoup_audio_channels,
    streaming_deps_available,
)


class TestStreamingDepsAvailable:
    """Detection of the optional live-video dependencies."""

    def test_true_when_pymediasoup_installed(self):
        streaming_deps_available.cache_clear()
        assert streaming_deps_available() is True
        streaming_deps_available.cache_clear()

    def test_false_when_pymediasoup_missing(self):
        streaming_deps_available.cache_clear()
        with patch(
            "custom_components.fermax_blue.streaming.find_spec",
            return_value=None,
        ):
            assert streaming_deps_available() is False
        streaming_deps_available.cache_clear()


class RecordingTransport:
    """Fake mediasoup transport that stores event handlers like the real one."""

    def __init__(self) -> None:
        self.handlers: dict = {}
        self.close = AsyncMock()
        self.consume = AsyncMock()
        self.produce = AsyncMock()

    def on(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register


def _mock_transport() -> RecordingTransport:
    return RecordingTransport()


def _mocked_session(tmp_path, receive_only):
    """Build a session with the signaling client and mediasoup device mocked out."""
    session = FermaxStreamSession(
        signaling_url="https://signaling-pro-duoxme.fermax.io",
        oauth_token="oauth",
        fcm_token="fcm",
        room_id="room1",
        media_root=str(tmp_path),
        receive_only=receive_only,
    )

    transport_data = TransportData(
        id="t1", dtls_parameters="{}", ice_candidates="[]", ice_parameters="{}"
    )
    signaling = MagicMock()
    signaling.connect = AsyncMock(
        return_value=RoomJoinResult(
            video_producer_id="vp",
            audio_producer_id="ap",
            router_rtp_capabilities="{}",
            recv_video_transport=transport_data,
            recv_audio_transport=transport_data,
            send_transport=transport_data,
        )
    )
    signaling.consume_transport = AsyncMock(
        return_value=ConsumeResult(
            consumer_id="c1", producer_id="vp", kind="video", rtp_parameters=MagicMock()
        )
    )
    signaling.connect_transport = AsyncMock(return_value=True)
    signaling.pickup = AsyncMock()
    signaling.disconnect = AsyncMock()
    session._signaling = signaling

    consumer = MagicMock()
    consumer.track.recv = AsyncMock(side_effect=MediaStreamError)
    consumer.close = AsyncMock()
    recv_transport = _mock_transport()
    recv_transport.consume = AsyncMock(return_value=consumer)

    producer = MagicMock()
    producer.close = AsyncMock()
    send_transport = _mock_transport()
    send_transport.produce = AsyncMock(return_value=producer)

    device = MagicMock()
    device.load = AsyncMock()
    device.createRecvTransport = MagicMock(return_value=recv_transport)
    device.createSendTransport = MagicMock(return_value=send_transport)
    device.rtpCapabilities.dict = MagicMock(return_value={})

    patches = (
        patch("pymediasoup.Device", return_value=device),
        patch("pymediasoup.rtp_parameters.RtpCapabilities", MagicMock()),
        patch("pymediasoup.models.transport.DtlsParameters", MagicMock()),
        patch("pymediasoup.models.transport.IceCandidate", MagicMock()),
        patch("pymediasoup.models.transport.IceParameters", MagicMock()),
    )
    return session, send_transport, patches


class TestReceiveOnlySession:
    """Receive-only sessions consume video but never signal pickup."""

    async def test_receive_only_skips_pickup(self, tmp_path):
        session, send_transport, patches = _mocked_session(tmp_path, receive_only=True)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            assert await session.start() is True

        send_transport.produce.assert_not_awaited()
        session._signaling.pickup.assert_not_awaited()
        assert session._audio_producer is None
        await session.stop()

    async def test_normal_session_produces_audio(self, tmp_path):
        session, send_transport, patches = _mocked_session(tmp_path, receive_only=False)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            assert await session.start() is True

        send_transport.produce.assert_awaited_once()
        await session.stop()

    def test_receive_only_session_disables_hangup(self, tmp_path):
        session = FermaxStreamSession(
            signaling_url="https://signaling-pro-duoxme.fermax.io",
            oauth_token="",
            fcm_token="",
            room_id="r1",
            media_root=str(tmp_path),
            receive_only=True,
        )
        assert session._signaling._send_hangup is False


class TestSignalingHangup:
    """Hangup is only sent for sessions that could have picked up."""

    async def test_disconnect_hangs_up_by_default(self):
        client = FermaxSignalingClient()
        sio = AsyncMock()
        client._sio = sio
        client._connected = True

        await client.disconnect()

        sio.emit.assert_awaited_once_with("hang_up", {})

    async def test_receive_only_disconnect_skips_hangup(self):
        client = FermaxSignalingClient(send_hangup=False)
        sio = AsyncMock()
        client._sio = sio
        client._connected = True

        await client.disconnect()

        sio.emit.assert_not_awaited()


class TestPreviewFrames:
    """LIVE overlay rendering and the overlay-free preview frame."""

    def _session(self) -> FermaxStreamSession:
        return FermaxStreamSession(
            signaling_url="https://signaling-pro-duoxme.fermax.io",
            oauth_token="oauth",
            fcm_token="fcm",
            room_id="room1",
        )

    def test_overlay_live_indicator_draws_badge(self):
        from PIL import Image

        img = Image.new("RGB", (368, 288), (0, 0, 0))
        out = FermaxStreamSession._overlay_live_indicator(img)

        # Red badge background with the white dot drawn as an ellipse
        assert out.getpixel((8, 8)) == (200, 0, 0)
        assert out.getpixel((17, 17)) == (255, 255, 255)

    def test_latest_frame_raw_prefers_overlay_free_frame(self):
        session = self._session()
        session._latest_frame = b"with-overlay"
        session._last_raw_frame = b"raw"

        assert session.latest_frame_raw == b"raw"

    def test_latest_frame_raw_falls_back_to_latest_frame(self):
        session = self._session()
        session._latest_frame = b"with-overlay"

        assert session.latest_frame_raw == b"with-overlay"

    def test_overlay_failure_returns_input_unchanged(self):
        # Anything that is not a PIL image must pass through untouched
        assert FermaxStreamSession._overlay_live_indicator("not an image") == "not an image"


# ---------------------------------------------------------------------------
# Fakes with real behavior for signaling / media pipeline tests
# ---------------------------------------------------------------------------


class FakeSioClient:
    """In-memory socket.io client: registers handlers, records requests, replays responses."""

    def __init__(self, responses=None, connect_error=None):
        self.handlers = {}
        self.calls = []
        self.emitted = []
        self.responses = responses or {}
        self.connect_error = connect_error
        self.connected = False
        self.connect_args = None

    def event(self, handler):
        self.handlers[handler.__name__] = handler
        return handler

    def on(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register

    async def connect(self, url, transports=None):
        if self.connect_error is not None:
            raise self.connect_error
        self.connect_args = (url, transports)
        self.connected = True
        await self.handlers["connect"]()

    # timeout mirrors python-socketio's AsyncClient.call signature
    async def call(self, event, data=None, timeout=None):  # noqa: ASYNC109
        self.calls.append((event, data, timeout))
        response = self.responses.get(event)
        if isinstance(response, Exception):
            raise response
        return response

    async def emit(self, event, data=None):
        self.emitted.append((event, data))

    async def disconnect(self):
        self.connected = False
        if "disconnect" in self.handlers:
            await self.handlers["disconnect"]()


def _patched_sio(fake):
    """Patch socketio.AsyncClient so the signaling client picks up our fake."""
    return patch(
        "custom_components.fermax_blue.streaming.socketio.AsyncClient",
        lambda **kwargs: fake,
    )


def _transport_payload(transport_id):
    return {
        "id": transport_id,
        "dtlsParameters": {"role": "auto"},
        "iceCandidates": [{"foundation": "f1"}],
        "iceParameters": {"usernameFragment": "ufrag"},
    }


def _join_response():
    return {
        "result": {
            "producerIdVideo": "vp1",
            "producerIdAudio": "ap1",
            "routerRtpCapabilities": {"codecs": []},
            "recvTransportVideo": _transport_payload("tv"),
            "recvTransportAudio": _transport_payload("ta"),
            "sendTransport": _transport_payload("ts"),
            "iceServers": [{"urls": "turn:relay.example"}],
        }
    }


def _connected_client(responses):
    """Signaling client wired to an already-connected fake socket."""
    client = FermaxSignalingClient()
    fake = FakeSioClient(responses)
    client._sio = fake
    client._connected = True
    return client, fake


def _bare_session(media_root=None):
    kwargs = {"media_root": media_root} if media_root else {}
    return FermaxStreamSession(
        signaling_url="https://sig.example",
        oauth_token="oauth",
        fcm_token="fcm",
        room_id="room1",
        **kwargs,
    )


class ScriptedTrack:
    """Track that replays queued frames, then raises the final error."""

    kind = "video"

    def __init__(self, frames, final=MediaStreamError):
        self._frames = list(frames)
        self._final = final

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        raise self._final


class BlockingTrack:
    """Track whose recv() blocks until cancelled, keeping the session active."""

    def __init__(self, kind="video"):
        self.kind = kind

    async def recv(self):
        await asyncio.Event().wait()


class FakeVideoFrame:
    """Video frame that decodes to a real PIL image."""

    def to_image(self):
        return Image.new("RGB", (64, 48), (10, 130, 200))


class FakeAudioFrame:
    """Audio frame backed by a real numpy PCM buffer."""

    def __init__(self, sample_rate=8000, value=7):
        self.sample_rate = sample_rate
        self._value = value

    def to_ndarray(self):
        return np.full(160, self._value, dtype=np.int16)


class TestSignalingConnect:
    """join_call flow over a fake socket.io client."""

    async def test_join_success_parses_room(self):
        fake = FakeSioClient(responses={"join_call": _join_response()})
        client = FermaxSignalingClient(
            signaling_url="https://sig.example", oauth_token="oauth", fcm_token="fcm"
        )

        with _patched_sio(fake):
            result = await client.connect("room-1")

        assert client.is_connected is True
        assert result is client.room_join_result
        assert fake.connect_args == ("https://sig.example", ["websocket"])
        event, payload, _timeout = fake.calls[0]
        assert event == "join_call"
        assert payload == {
            "roomId": "room-1",
            "appToken": "fcm",
            "fermaxOauthToken": "oauth",
            "protocolVersion": SIGNALING_VERSION,
        }
        assert result.video_producer_id == "vp1"
        assert result.audio_producer_id == "ap1"
        assert json.loads(result.router_rtp_capabilities) == {"codecs": []}
        assert result.recv_video_transport.id == "tv"
        assert json.loads(result.recv_video_transport.dtls_parameters) == {"role": "auto"}
        assert json.loads(result.recv_audio_transport.ice_candidates) == [{"foundation": "f1"}]
        assert result.send_transport.id == "ts"
        assert json.loads(result.ice_servers) == [{"urls": "turn:relay.example"}]

    async def test_join_error_response_returns_none(self):
        fake = FakeSioClient(responses={"join_call": {"error": "denied"}})
        client = FermaxSignalingClient()

        with _patched_sio(fake):
            assert await client.connect("room-1") is None

    async def test_join_non_dict_response_returns_none(self):
        fake = FakeSioClient(responses={"join_call": "nope"})
        client = FermaxSignalingClient()

        with _patched_sio(fake):
            assert await client.connect("room-1") is None

    async def test_join_empty_result_returns_none(self):
        fake = FakeSioClient(responses={"join_call": {"result": {}}})
        client = FermaxSignalingClient()

        with _patched_sio(fake):
            assert await client.connect("room-1") is None

    async def test_connect_failure_returns_none(self):
        fake = FakeSioClient(connect_error=ConnectionError("refused"))
        client = FermaxSignalingClient()

        with _patched_sio(fake):
            assert await client.connect("room-1") is None
        assert client.is_connected is False

    async def test_end_up_event_invokes_callback(self):
        fake = FakeSioClient(responses={"join_call": _join_response()})
        client = FermaxSignalingClient()

        with _patched_sio(fake):
            await client.connect("room-1")

        # Without a registered callback the event must not raise
        await fake.handlers["end_up"]({"code": "IGNORED"})

        codes = []
        client._on_end_up = codes.append
        await fake.handlers["end_up"]({"code": "BYE"})
        await fake.handlers["end_up"]("raw-code")
        assert codes == ["BYE", "raw-code"]

        await fake.handlers["disconnect"]()
        assert client.is_connected is False

    def test_parse_transport_defaults(self):
        parsed = FermaxSignalingClient._parse_transport({})

        assert parsed == TransportData(
            id="", dtls_parameters="{}", ice_candidates="[]", ice_parameters="{}"
        )


class TestSignalingCalls:
    """transport_consume / transport_connect / pickup request-response handling."""

    async def test_consume_transport_success_with_json_caps(self):
        client, fake = _connected_client(
            {
                "transport_consume": {
                    "result": {
                        "id": "c1",
                        "producerId": "p1",
                        "kind": "video",
                        "rtpParameters": {"codecs": [{"mimeType": "video/H264"}]},
                    }
                }
            }
        )

        result = await client.consume_transport("t1", "p1", '{"codecs": []}')

        assert result == ConsumeResult(
            consumer_id="c1",
            producer_id="p1",
            kind="video",
            rtp_parameters={"codecs": [{"mimeType": "video/H264"}]},
        )
        event, payload, _timeout = fake.calls[0]
        assert event == "transport_consume"
        assert payload == {
            "transportId": "t1",
            "producerId": "p1",
            "rtpCapabilities": {"codecs": []},
        }

    async def test_consume_transport_accepts_dict_caps(self):
        client, fake = _connected_client({"transport_consume": {"result": {}}})

        result = await client.consume_transport("t1", "p1", {"codecs": []})

        assert result is not None
        assert result.consumer_id == ""
        assert fake.calls[0][1]["rtpCapabilities"] == {"codecs": []}

    async def test_consume_transport_requires_connection(self):
        client = FermaxSignalingClient()

        assert await client.consume_transport("t1", "p1", "{}") is None

    async def test_consume_transport_error_response(self):
        client, _fake = _connected_client({"transport_consume": {"error": "no producer"}})

        assert await client.consume_transport("t1", "p1", "{}") is None

    async def test_consume_transport_call_failure(self):
        client, _fake = _connected_client({"transport_consume": RuntimeError("timeout")})

        assert await client.consume_transport("t1", "p1", "{}") is None

    async def test_connect_transport_success(self):
        client, fake = _connected_client({"transport_connect": {}})

        assert await client.connect_transport("t1", '{"role": "client"}') is True
        assert fake.calls[0] == (
            "transport_connect",
            {"transportId": "t1", "dtlsParameters": {"role": "client"}},
            10,
        )

    async def test_connect_transport_error_response(self):
        client, _fake = _connected_client({"transport_connect": {"error": "bad dtls"}})

        assert await client.connect_transport("t1", "{}") is False

    async def test_connect_transport_requires_connection(self):
        client = FermaxSignalingClient()

        assert await client.connect_transport("t1", "{}") is False

    async def test_connect_transport_call_failure(self):
        client, _fake = _connected_client({"transport_connect": RuntimeError("timeout")})

        assert await client.connect_transport("t1", "{}") is False

    async def test_pickup_success_returns_result(self):
        client, fake = _connected_client(
            {"pickup": {"result": {"producerId": "sp1", "consumer": {"producerId": "rp1"}}}}
        )

        result = await client.pickup(
            kind="audio",
            rtp_parameters='{"codecs": [1]}',
            app_data="{}",
            rtp_capabilities='{"codecs": []}',
        )

        assert result == {"producerId": "sp1", "consumer": {"producerId": "rp1"}}
        event, payload, _timeout = fake.calls[0]
        assert event == "pickup"
        # Matches the APK's PickupCall JSON structure
        assert payload == {
            "parameters": {"kind": "audio", "rtpParameters": {"codecs": [1]}, "appData": {}},
            "rtpCapabilities": {"codecs": []},
        }

    async def test_pickup_error_response_returns_none(self):
        client, _fake = _connected_client({"pickup": {"error": "not ringing"}})

        assert await client.pickup("audio", "{}", "{}", "{}") is None

    async def test_pickup_requires_connection(self):
        client = FermaxSignalingClient()

        assert await client.pickup("audio", "{}", "{}", "{}") is None

    async def test_pickup_invalid_json_returns_none(self):
        client, _fake = _connected_client({"pickup": {"result": {}}})

        assert await client.pickup("audio", "{invalid", "{}", "{}") is None

    async def test_hangup_emits_event(self):
        client, fake = _connected_client({})

        await client.hangup()

        assert fake.emitted == [("hang_up", {})]

    async def test_hangup_swallows_emit_failure(self):
        class ExplodingEmit(FakeSioClient):
            async def emit(self, event, data=None):
                raise RuntimeError("socket gone")

        client = FermaxSignalingClient()
        client._sio = ExplodingEmit()
        client._connected = True

        await client.hangup()  # must not raise

    async def test_disconnect_resets_state_even_when_transport_fails(self):
        class ExplodingDisconnect(FakeSioClient):
            async def disconnect(self):
                raise RuntimeError("socket gone")

        client = FermaxSignalingClient(send_hangup=False)
        client._sio = ExplodingDisconnect()
        client._connected = True
        client._room_join_result = MagicMock()

        await client.disconnect()

        assert client._sio is None
        assert client.is_connected is False
        assert client.room_join_result is None


class TestSwitchableAudioTrack:
    """The silence-generating track that can switch to a real audio source."""

    async def test_generates_silence_without_source(self):
        track = _create_switchable_audio_track()

        first = await track.recv()
        second = await track.recv()

        assert first.sample_rate == 48000
        assert first.samples == 960
        assert first.pts == 0
        assert second.pts == 960
        assert not first.to_ndarray().any()

    async def test_uses_source_when_set(self):
        class Source:
            async def recv(self):
                return "frame-from-source"

        track = _create_switchable_audio_track()
        track.set_source(Source())

        assert await track.recv() == "frame-from-source"

    async def test_failing_source_falls_back_to_silence(self):
        class BrokenSource:
            async def recv(self):
                raise RuntimeError("player died")

        track = _create_switchable_audio_track()
        track.set_source(BrokenSource())

        frame = await track.recv()

        assert frame.samples == 960
        assert track._source is None


class TestSignalingUrlScheme:
    """Insecure signaling URLs are upgraded before connecting."""

    def _session(self, url):
        return FermaxStreamSession(signaling_url=url, oauth_token="o", fcm_token="f", room_id="r")

    def test_http_upgraded_to_https(self):
        session = self._session("http://sig.example")

        assert session._signaling._signaling_url == "https://sig.example"

    def test_ws_upgraded_to_wss(self):
        session = self._session("ws://sig.example")

        assert session._signaling._signaling_url == "wss://sig.example"

    def test_secure_url_untouched(self):
        session = self._session("wss://sig.example")

        assert session._signaling._signaling_url == "wss://sig.example"


class TestStartFailures:
    """start() degrades gracefully when the pipeline cannot be built."""

    async def test_missing_deps_stops_session(self, tmp_path):
        session = _bare_session(str(tmp_path))
        session._signaling = MagicMock(
            connect=AsyncMock(side_effect=ImportError("No module named 'pymediasoup'")),
            disconnect=AsyncMock(),
        )

        assert await session.start() is False
        session._signaling.disconnect.assert_awaited_once()

    async def test_unexpected_error_stops_session(self, tmp_path):
        session = _bare_session(str(tmp_path))
        session._signaling = MagicMock(
            connect=AsyncMock(side_effect=RuntimeError("boom")),
            disconnect=AsyncMock(),
        )

        assert await session.start() is False
        session._signaling.disconnect.assert_awaited_once()

    async def test_room_join_failure_returns_false(self, tmp_path):
        session = _bare_session(str(tmp_path))
        session._signaling = MagicMock(connect=AsyncMock(return_value=None), disconnect=AsyncMock())

        assert await session.start() is False

    async def test_video_consume_failure_returns_false(self, tmp_path):
        session, _send_transport, patches = _mocked_session(tmp_path, receive_only=True)
        session._signaling.consume_transport = AsyncMock(return_value=None)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            assert await session.start() is False

        await session.stop()


def _pickup_session(tmp_path):
    """Session whose fake produce() fires the registered handler like real pymediasoup."""
    session = _bare_session(str(tmp_path))

    transport_data = TransportData(
        id="t1", dtls_parameters="{}", ice_candidates="[]", ice_parameters="{}"
    )
    signaling = MagicMock()
    signaling.connect = AsyncMock(
        return_value=RoomJoinResult(
            video_producer_id="vp",
            audio_producer_id="ap",
            router_rtp_capabilities="{}",
            recv_video_transport=transport_data,
            recv_audio_transport=transport_data,
            send_transport=transport_data,
        )
    )
    video_consume = ConsumeResult(
        consumer_id="c-video", producer_id="vp", kind="video", rtp_parameters=MagicMock()
    )
    audio_consume = ConsumeResult(
        consumer_id="c-audio", producer_id="remote-audio", kind="audio", rtp_parameters=MagicMock()
    )
    signaling.consume_transport = AsyncMock(side_effect=[video_consume, audio_consume])
    signaling.connect_transport = AsyncMock(return_value=True)
    signaling.pickup = AsyncMock(
        return_value={"producerId": "our-prod", "consumer": {"producerId": "remote-audio"}}
    )
    signaling.disconnect = AsyncMock()
    session._signaling = signaling

    video_consumer = MagicMock()
    video_consumer.track = BlockingTrack("video")
    video_consumer.close = AsyncMock()
    audio_consumer = MagicMock()
    audio_consumer.track = BlockingTrack("audio")
    audio_consumer.close = AsyncMock()

    video_transport = RecordingTransport()
    video_transport.consume = AsyncMock(return_value=video_consumer)
    audio_transport = RecordingTransport()
    audio_transport.consume = AsyncMock(return_value=audio_consumer)

    send_transport = RecordingTransport()
    producer = MagicMock()
    producer.close = AsyncMock()

    async def _produce(track=None, stopTracks=None, appData=None):  # noqa: N803
        handler = send_transport.handlers["produce"]
        producer.remote_id = await handler(
            kind="audio", rtp_parameters={"codecs": []}, app_data=appData or {}
        )
        return producer

    send_transport.produce = AsyncMock(side_effect=_produce)

    device = MagicMock()
    device.load = AsyncMock()
    device.createRecvTransport = MagicMock(side_effect=[video_transport, audio_transport])
    device.createSendTransport = MagicMock(return_value=send_transport)
    device.rtpCapabilities.dict = MagicMock(return_value={"codecs": []})

    patches = (
        patch("pymediasoup.Device", return_value=device),
        patch("pymediasoup.rtp_parameters.RtpCapabilities", MagicMock()),
        patch("pymediasoup.models.transport.DtlsParameters", MagicMock()),
        patch("pymediasoup.models.transport.IceCandidate", MagicMock()),
        patch("pymediasoup.models.transport.IceParameters", MagicMock()),
    )
    return SimpleNamespace(
        session=session,
        signaling=signaling,
        producer=producer,
        audio_consumer=audio_consumer,
        video_transport=video_transport,
        audio_transport=audio_transport,
        send_transport=send_transport,
        patches=patches,
    )


class TestPickupFlow:
    """produce → pickup → remote audio consume wiring (APK call-answer sequence)."""

    async def test_pickup_creates_audio_consumer_and_stop_cleans_up(self, tmp_path):
        env = _pickup_session(tmp_path)

        with contextlib.ExitStack() as stack:
            for p in env.patches:
                stack.enter_context(p)
            assert await env.session.start() is True

        # Yield so both grabber tasks actually start and block on their tracks
        await asyncio.sleep(0.01)

        env.signaling.pickup.assert_awaited_once()
        pickup_kwargs = env.signaling.pickup.await_args.kwargs
        assert pickup_kwargs["kind"] == "audio"
        assert json.loads(pickup_kwargs["rtp_parameters"]) == {"codecs": []}
        assert json.loads(pickup_kwargs["rtp_capabilities"]) == {"codecs": []}
        assert env.producer.remote_id == "our-prod"

        # Remote audio was consumed on the audio transport after the pickup ACK
        assert env.signaling.consume_transport.await_count == 2
        audio_call = env.signaling.consume_transport.await_args_list[1].kwargs
        assert audio_call["producer_id"] == "remote-audio"
        assert env.session._audio_consumer is env.audio_consumer

        # Both grabber tasks stay alive on the blocking tracks
        assert env.session.is_active is True
        assert not env.session._frame_task.done()
        assert not env.session._audio_task.done()

        # Transport connect callbacks forward DTLS params to signaling
        dtls = SimpleNamespace(dict=lambda exclude_none=True: {"role": "client"})
        await env.video_transport.handlers["connect"](dtls)
        await env.audio_transport.handlers["connect"](dtls)
        await env.send_transport.handlers["connect"](dtls)
        assert env.signaling.connect_transport.await_count == 3

        await env.session.stop()

        env.audio_consumer.close.assert_awaited_once()
        assert env.session._audio_consumer is None
        assert env.session.is_active is False

    async def test_produce_handler_pickup_failure_returns_empty_id(self, tmp_path):
        session, send_transport, patches = _mocked_session(tmp_path, receive_only=True)
        session._signaling.pickup = AsyncMock(return_value=None)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            assert await session.start() is True

        handler = send_transport.handlers["produce"]
        rtp = SimpleNamespace(dict=lambda exclude_none=True: {"codecs": []})

        assert await handler(kind="audio", rtp_parameters=rtp, app_data=None) == ""

        await session.stop()


class TestEndUpStopsSession:
    """The server end_up signal schedules a full session stop."""

    async def test_end_up_signal_schedules_stop(self, tmp_path):
        session, _send_transport, patches = _mocked_session(tmp_path, receive_only=True)

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            assert await session.start() is True

        # Fire the handler wired in _start_inner, as the signaling client would
        session._signaling._on_end_up("BYE")
        for _ in range(50):
            await asyncio.sleep(0.02)
            if session._signaling.disconnect.await_count:
                break

        session._signaling.disconnect.assert_awaited()


class TestGrabFrames:
    """RTP frame → JPEG conversion loop."""

    def _session(self):
        session = _bare_session()
        session._active = True
        session._recording_frames = []
        return session

    async def test_frames_encoded_with_and_without_overlay(self):
        session = self._session()
        ended = []

        def _on_end():
            ended.append(True)

        session._on_end = _on_end
        # 100 frames also exercises the periodic frame-count logging
        session._consumer = SimpleNamespace(
            track=ScriptedTrack([FakeVideoFrame() for _ in range(100)])
        )

        await session._grab_frames()

        assert session.latest_frame.startswith(b"\xff\xd8")
        assert session.latest_frame_raw.startswith(b"\xff\xd8")
        # The display copy carries the LIVE overlay, the raw copy does not
        assert session._last_raw_frame != session._latest_frame
        assert len(session._recording_frames) == 100
        assert session._recording_frames[-1] == session._last_raw_frame
        decoded = Image.open(io.BytesIO(session._latest_frame))
        assert decoded.size == (64, 48)
        badge = decoded.getpixel((8, 8))
        assert badge[0] > 150  # red LIVE badge drawn on the display copy
        assert badge[1] < 80
        assert session.is_active is False
        assert ended == [True]

    async def test_unexpected_error_ends_session(self):
        class BrokenFrame:
            def to_image(self):
                raise ValueError("corrupt frame")

        session = self._session()
        ended = []

        def _on_end():
            ended.append(True)

        session._on_end = _on_end
        session._consumer = SimpleNamespace(track=ScriptedTrack([BrokenFrame()]))

        await session._grab_frames()

        assert session.is_active is False
        assert ended == [True]

    async def test_cancellation_is_handled(self):
        session = self._session()
        session._consumer = SimpleNamespace(track=ScriptedTrack([], final=asyncio.CancelledError))

        await session._grab_frames()

        assert session.is_active is False


class TestGrabAudio:
    """Received audio frames are collected as raw PCM for the recording."""

    def _session(self):
        session = _bare_session()
        session._active = True
        session._recording_audio_frames = []
        return session

    async def test_collects_pcm_until_track_ends(self):
        session = self._session()
        track = ScriptedTrack([FakeAudioFrame(value=7), FakeAudioFrame(value=9)])
        session._audio_consumer = SimpleNamespace(track=track)

        await session._grab_audio()

        assert session._audio_sample_rate == 8000
        assert len(session._recording_audio_frames) == 2
        assert session._recording_audio_frames[0] == np.full(160, 7, dtype=np.int16).tobytes()
        assert session._recording_audio_frames[1] == np.full(160, 9, dtype=np.int16).tobytes()

    async def test_unexpected_error_is_swallowed(self):
        session = self._session()
        track = ScriptedTrack([], final=RuntimeError("rtp desync"))
        session._audio_consumer = SimpleNamespace(track=track)

        await session._grab_audio()  # must not raise


def _read_bytes(path):
    """Sync file read helper (pathlib methods are linted inside async tests)."""
    return Path(path).read_bytes()


def _exists(path):
    """Sync existence check helper (pathlib methods are linted inside async tests)."""
    return Path(path).exists()


def _recording_session(tmp_path, video_frames, audio_frames=None, sent=None, rate=48000):
    session = _bare_session(str(tmp_path))
    session._recording_path = str(tmp_path / "rec.mp4")
    session._recording_frames = list(video_frames)
    session._recording_audio_frames = list(audio_frames or [])
    session._recording_sent_audio = list(sent or [])
    session._audio_sample_rate = rate
    return session


class TestRecording:
    """Recording init and MP4 finalization via (faked) ffmpeg."""

    def test_init_recording_prepares_buffers(self, tmp_path):
        session = _bare_session(str(tmp_path))

        session._init_recording()

        assert (tmp_path / "fermax_recordings").is_dir()
        assert session._recording_path.endswith(".mp4")
        assert session._recording_frames == []
        assert session._recording_audio_frames == []
        assert session._recording_sent_audio == []
        assert session._audio_sample_rate == 48000

    def test_init_recording_failure_disables_recording(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        session = _bare_session(str(blocker))

        session._init_recording()

        assert session._recording_path is None

    async def test_save_video_only_builds_ffmpeg_command(self, tmp_path):
        session = _recording_session(tmp_path, [b"\xff\xd8AA", b"\xff\xd8BB"])
        captured = {}

        def fake_run(cmd, capture_output=None, timeout=None, shell=None):
            inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
            captured["cmd"] = cmd
            captured["mjpeg"] = Path(inputs[0]).read_bytes()
            Path(cmd[-1]).write_bytes(b"mp4-output")
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch("subprocess.run", fake_run):
            await session._save_recording()

        cmd = captured["cmd"]
        assert cmd[0] == "ffmpeg"
        assert cmd[-1] == str(tmp_path / "rec.mp4")
        assert "-c:a" not in cmd
        assert captured["mjpeg"] == b"\xff\xd8AA\xff\xd8BB"
        assert _read_bytes(session._recording_path) == b"mp4-output"
        assert session._recording_frames == []

    async def test_save_mixes_received_and_sent_audio(self, tmp_path):
        recv = np.full(400, 1000, dtype=np.int16).tobytes()  # 0.05 s at 8 kHz
        sent = np.full(4800, 500, dtype=np.int16).tobytes()  # 0.1 s at 48 kHz
        session = _recording_session(
            tmp_path, [b"\xff\xd8AA"], audio_frames=[recv], sent=[sent], rate=8000
        )
        captured = {}

        def fake_run(cmd, capture_output=None, timeout=None, shell=None):
            inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
            captured["cmd"] = cmd
            captured["pcm"] = Path(inputs[1]).read_bytes()
            Path(cmd[-1]).write_bytes(b"mp4-output")
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch("subprocess.run", fake_run):
            await session._save_recording()

        cmd = captured["cmd"]
        assert cmd[cmd.index("-ar") + 1] == "8000"
        assert "aac" in cmd
        mixed = np.frombuffer(captured["pcm"], dtype=np.int16)
        # Sent 48 kHz audio is resampled 6:1 to the 8 kHz received rate, the
        # shorter received signal is zero-padded, then both are summed
        assert len(mixed) == 800
        assert set(mixed[:400].tolist()) == {1500}
        assert set(mixed[400:].tolist()) == {500}
        assert session._recording_audio_frames == []
        assert session._recording_sent_audio == []

    async def test_save_recv_only_audio_at_48k(self, tmp_path):
        recv = np.full(960, 250, dtype=np.int16).tobytes()
        session = _recording_session(tmp_path, [b"\xff\xd8AA"], audio_frames=[recv], rate=48000)
        captured = {}

        def fake_run(cmd, capture_output=None, timeout=None, shell=None):
            inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
            captured["cmd"] = cmd
            captured["pcm"] = Path(inputs[1]).read_bytes()
            Path(cmd[-1]).write_bytes(b"mp4-output")
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch("subprocess.run", fake_run):
            await session._save_recording()

        assert captured["cmd"][captured["cmd"].index("-ar") + 1] == "48000"
        mixed = np.frombuffer(captured["pcm"], dtype=np.int16)
        assert len(mixed) == 960
        assert set(mixed.tolist()) == {250}

    async def test_save_logs_ffmpeg_failure(self, tmp_path):
        session = _recording_session(tmp_path, [b"\xff\xd8AA"])

        def fake_run(cmd, capture_output=None, timeout=None, shell=None):
            return SimpleNamespace(returncode=1, stderr=b"encoder exploded")

        with patch("subprocess.run", fake_run):
            await session._save_recording()  # must not raise

        assert not _exists(session._recording_path)
        assert session._recording_frames == []

    async def test_save_falls_back_to_mjpeg_without_ffmpeg(self, tmp_path):
        session = _recording_session(tmp_path, [b"\xff\xd8AA", b"\xff\xd8BB"])

        with patch("subprocess.run", side_effect=FileNotFoundError):
            await session._save_recording()

        assert (tmp_path / "rec.mjpeg").read_bytes() == b"\xff\xd8AA\xff\xd8BB"


class TestStopPreviewFrame:
    """stop() swaps the frozen LIVE frame for the overlay-free copy."""

    async def test_stop_swaps_in_overlay_free_frame(self):
        session = _bare_session()
        session._latest_frame = b"jpeg-with-live-badge"
        session._last_raw_frame = b"jpeg-raw"

        await session.stop()

        assert session.latest_frame == b"jpeg-raw"


def _write_wav(path, num_samples, rate=8000, value=1000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(np.full(num_samples, value, dtype=np.int16).tobytes())


class TestSendAudio:
    """Audio file playback through the switchable producer track."""

    def _live_session(self):
        session = _bare_session()
        session._active = True
        session._audio_producer = MagicMock()
        session._switchable_track = _create_switchable_audio_track()
        session._recording_sent_audio = []
        return session

    async def test_sends_resampled_chunks(self, tmp_path):
        clip = tmp_path / "clip.wav"
        _write_wav(clip, num_samples=800, rate=8000)  # 0.1 s → ~4800 samples at 48 kHz
        session = self._live_session()

        assert await session.send_audio(str(clip)) is True

        # Sent PCM is chunked into 960-sample (1920-byte) frames for the recording mix
        assert session._recording_sent_audio
        assert all(len(chunk) == 1920 for chunk in session._recording_sent_audio)

        frame = await session._switchable_track.recv()
        assert frame.sample_rate == 48000
        assert frame.samples == 960

    async def test_source_exhaustion_falls_back_to_silence(self, tmp_path):
        clip = tmp_path / "clip.wav"
        _write_wav(clip, num_samples=100, rate=8000)  # short clip: a single padded chunk
        session = self._live_session()
        assert await session.send_audio(str(clip)) is True

        for _ in range(10):
            await session._switchable_track.recv()
            if session._switchable_track._source is None:
                break

        assert session._switchable_track._source is None

    async def test_requires_active_producer(self, tmp_path):
        session = _bare_session()

        assert await session.send_audio(str(tmp_path / "clip.wav")) is False

    async def test_unreadable_file_returns_false(self, tmp_path):
        session = self._live_session()

        assert await session.send_audio(str(tmp_path / "missing.wav")) is False

    async def test_empty_clip_returns_false(self, tmp_path):
        clip = tmp_path / "empty.wav"
        _write_wav(clip, num_samples=0)
        session = self._live_session()

        assert await session.send_audio(str(clip)) is False


class TestPymediasoupChannelsPatch:
    """The mono-channels normalization applied over pymediasoup's handler."""

    async def test_patched_capabilities_normalize_mono_audio(self):
        from pymediasoup.handlers.aiortc_handler import AiortcHandler

        saved = AiortcHandler.getNativeRtpCapabilities
        try:
            caps = SimpleNamespace(
                codecs=[
                    SimpleNamespace(kind="audio", channels=None),
                    SimpleNamespace(kind="audio", channels=2),
                    SimpleNamespace(kind="video", channels=None),
                ]
            )

            async def fake_get(self):
                return caps

            AiortcHandler.getNativeRtpCapabilities = fake_get
            _patch_pymediasoup_audio_channels()

            patched = await AiortcHandler.getNativeRtpCapabilities(MagicMock())

            assert patched.codecs[0].channels == 1  # mono audio normalized
            assert patched.codecs[1].channels == 2  # stereo untouched
            assert patched.codecs[2].channels is None  # video untouched
        finally:
            AiortcHandler.getNativeRtpCapabilities = saved
