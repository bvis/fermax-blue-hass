"""Tests for the go2rtc WebRTC bridge."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiortc.mediastreams import MediaStreamError
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.fermax_blue import webrtc_bridge
from custom_components.fermax_blue.webrtc_bridge import (
    FermaxWebRtcView,
    WebRtcPeer,
    webrtc_stream_source,
)

OFFER_SDP = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
CANDIDATE = "candidate:1 1 udp 2130706431 127.0.0.1 5000 typ host"


class FakePC:
    """Records what the bridge does to the peer connection."""

    def __init__(self, config=None):
        self.config = config
        self.handlers = {}
        self.tracks = []
        self.connectionState = "new"
        self.setRemoteDescription = AsyncMock()
        self.createAnswer = AsyncMock(return_value="answer")
        self.setLocalDescription = AsyncMock()
        self.localDescription = SimpleNamespace(sdp="answer-sdp")
        self.addIceCandidate = AsyncMock()
        self.close = AsyncMock()

    def on(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register

    def addTrack(self, track):  # noqa: N802 - aiortc API name
        self.tracks.append(track)


class FakeWs:
    """Async-iterable WebSocket stand-in."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _text(payload):
    return SimpleNamespace(type=web.WSMsgType.TEXT, json=lambda: payload)


def _jpeg_bytes(size=(8, 8)):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _session(video=True, active=True, encoded=True):
    session = MagicMock()
    session.subscribe_encoded_video = MagicMock(
        return_value=MagicMock() if video and encoded else None
    )
    session.subscribe_video = MagicMock(return_value=MagicMock() if video else None)
    session.is_active = active
    return session


def _coordinator(session=None, pickup=True, last_photo=None):
    coordinator = MagicMock()
    coordinator.last_photo = last_photo
    coordinator.ensure_stream = AsyncMock(return_value=session)
    coordinator.pickup = AsyncMock(return_value=pickup)
    return coordinator


async def _run(peer, messages):
    with patch("aiortc.RTCPeerConnection", FakePC):
        await peer.run(FakeWs(messages))
    return peer._pc


def _offer():
    return _text({"type": "webrtc/offer", "value": OFFER_SDP})


async def _never_returns():
    await asyncio.sleep(60)


class TestStreamSource:
    """The go2rtc source string points at HA's own HTTP server."""

    def test_plain_http(self):
        hass = MagicMock()
        hass.config.api = SimpleNamespace(use_ssl=False, port=8123)
        assert (
            webrtc_stream_source(hass, "tok")
            == "webrtc:ws://127.0.0.1:8123/api/fermax_blue/webrtc/tok"
        )

    def test_ssl_uses_wss_and_the_certificate_hostname(self):
        hass = MagicMock()
        hass.config.api = SimpleNamespace(use_ssl=True, port=8443)
        with patch(
            "custom_components.fermax_blue.webrtc_bridge.get_url",
            return_value="https://ha.example.com",
        ) as get_url:
            assert webrtc_stream_source(hass, "t").startswith("webrtc:wss://ha.example.com:8443/")
        get_url.assert_called_once_with(
            hass, allow_ip=False, allow_cloud=False, prefer_external=False
        )

    def test_ssl_without_hostname_falls_back_to_loopback(self):
        hass = MagicMock()
        hass.config.api = SimpleNamespace(use_ssl=True, port=8443)
        with patch(
            "custom_components.fermax_blue.webrtc_bridge.get_url",
            side_effect=NoURLAvailableError,
        ):
            assert webrtc_stream_source(hass, "t").startswith("webrtc:wss://127.0.0.1:8443/")

    def test_missing_api_config_defaults(self):
        hass = MagicMock()
        hass.config.api = None
        assert webrtc_stream_source(hass, "t").startswith("webrtc:ws://127.0.0.1:8123/")


class TestPlaceholderVideo:
    """Video shown while the intercom wakes up."""

    def test_snapshot_becomes_even_sized_yuv_frame(self):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (65, 47), (200, 30, 30)).save(buf, format="JPEG")

        frame = webrtc_bridge._placeholder_frame(buf.getvalue())

        assert frame.format.name == "yuv420p"
        assert (frame.width, frame.height) == (64, 46)

    def test_missing_or_broken_snapshot_gives_black_frame(self):
        for jpeg in (None, b"not-a-jpeg"):
            frame = webrtc_bridge._placeholder_frame(jpeg)
            assert (frame.width, frame.height) == webrtc_bridge.PLACEHOLDER_SIZE
            assert frame.to_ndarray()[0, 0] == 16  # black luma

    async def test_track_switches_from_placeholder_to_source(self):
        class Live:
            async def recv(self):
                return "live-frame"

        with patch.object(webrtc_bridge, "PLACEHOLDER_FPS", 1000):
            track = webrtc_bridge._create_switchable_video_track(lambda: None)
            first = await track.recv()
            first_pts = first.pts
            second = await track.recv()
            assert first.width == webrtc_bridge.PLACEHOLDER_SIZE[0]
            assert second.pts == first_pts + 1

            track.set_source(Live())
            assert await track.recv() == "live-frame"

    async def test_ended_source_falls_back_to_placeholder(self):
        class Dead:
            async def recv(self):
                raise MediaStreamError

        with patch.object(webrtc_bridge, "PLACEHOLDER_FPS", 1000):
            track = webrtc_bridge._create_switchable_video_track(lambda: None)
            track.set_source(Dead())
            frame = await track.recv()
            assert frame.width == webrtc_bridge.PLACEHOLDER_SIZE[0]
            assert track._source is None


class TestPeerSignaling:
    """Offer/answer and candidate handling against go2rtc's message format."""

    async def test_offer_is_answered_at_once_with_placeholder_tracks(self):
        coordinator = _coordinator(session=None)
        coordinator.ensure_stream = AsyncMock(side_effect=_never_returns)
        peer = WebRtcPeer(coordinator)

        pc = await _run(peer, [_offer()])

        pc.setRemoteDescription.assert_awaited_once()
        assert pc.setRemoteDescription.call_args.args[0].sdp == OFFER_SDP
        assert [t.kind for t in pc.tracks] == ["video", "audio"]
        assert pc.config.iceServers == []
        assert pc.config.bundlePolicy.value == "max-bundle"
        # The intercom is woken up after answering, not before
        await asyncio.sleep(0)
        coordinator.ensure_stream.assert_awaited_once()
        await peer.close()

    async def test_answer_sent_in_go2rtc_format(self):
        peer = WebRtcPeer(_coordinator(_session()))
        ws = FakeWs([_offer()])

        with patch("aiortc.RTCPeerConnection", FakePC):
            await peer.run(ws)

        assert ws.sent == [{"type": "webrtc/answer", "value": "answer-sdp"}]
        await peer.close()

    async def test_candidate_added_on_primary_mid(self):
        peer = WebRtcPeer(_coordinator())

        pc = await _run(peer, [_text({"type": "webrtc/candidate", "value": CANDIDATE})])

        candidate = pc.addIceCandidate.call_args.args[0]
        assert candidate.sdpMid == "0"
        assert candidate.port == 5000

    async def test_empty_candidate_and_binary_frames_ignored(self):
        peer = WebRtcPeer(_coordinator())
        binary = SimpleNamespace(type=web.WSMsgType.BINARY, json=dict)

        pc = await _run(peer, [binary, _text({"type": "webrtc/candidate", "value": None})])

        pc.addIceCandidate.assert_not_awaited()

    async def test_signaling_error_closes_peer(self):
        peer = WebRtcPeer(_coordinator())

        class FailingPC(FakePC):
            def __init__(self, config=None):
                super().__init__(config)
                self.setRemoteDescription = AsyncMock(side_effect=ValueError("bad sdp"))

        with patch("aiortc.RTCPeerConnection", FailingPC):
            await peer.run(FakeWs([_offer()]))

        assert peer._closed is True
        peer._pc.close.assert_awaited_once()


class TestSessionAttachment:
    """The live session replaces the placeholders once the intercom streams."""

    async def _attached(self, session, coordinator=None):
        coordinator = coordinator or _coordinator(session)
        peer = WebRtcPeer(coordinator)
        await _run(peer, [_offer()])
        with (
            patch.object(webrtc_bridge, "SESSION_POLL_SECONDS", 0),
            patch.object(webrtc_bridge, "WAKE_RETRY_DELAY", 0),
        ):
            await asyncio.sleep(0)  # let _attach_session run past ensure_stream
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        return peer, coordinator

    async def test_live_tracks_replace_placeholders(self):
        session = _session()
        peer, _ = await self._attached(session)

        assert peer._video._source is session.subscribe_encoded_video.return_value
        session.attach_audio_sink.assert_called_once_with(peer._audio)
        await peer.close()

    async def test_decoded_frames_when_the_encoded_video_is_unavailable(self):
        session = _session(encoded=False)
        peer, _ = await self._attached(session)

        assert peer._video._source is session.subscribe_video.return_value
        await peer.close()

    async def test_video_kept_on_placeholder_when_session_has_none(self):
        session = _session(video=False)
        peer, _ = await self._attached(session)

        assert peer._video._source is None
        await peer.close()

    async def test_no_session_closes_peer(self):
        peer, _ = await self._attached(None)
        await asyncio.sleep(0)
        assert peer._closed is True

    async def test_failed_wake_up_closes_peer_instead_of_hanging(self, caplog):
        coordinator = _coordinator(None)
        coordinator.ensure_stream = AsyncMock(side_effect=RuntimeError("cloud says no"))
        peer, _ = await self._attached(None, coordinator)
        await asyncio.sleep(0)
        assert peer._closed is True
        assert "Could not start the intercom" in caplog.text

    async def test_session_end_keeps_the_peer_on_the_last_snapshot(self):
        session = _session(active=False)
        peer, coordinator = await self._attached(session)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert peer._closed is False  # no hang-up: go2rtc would redial and wake the intercom
        assert peer._session is None
        # the still tracks whatever the coordinator holds as the latest snapshot
        coordinator.last_photo = _jpeg_bytes()
        with patch.object(webrtc_bridge.asyncio, "sleep", AsyncMock()):
            peer._video.set_source(None)
            frame = await peer._video.recv()
        assert frame.width == 8  # dimensions of the test snapshot, not the black default
        await peer.close()


class TestMicrophone:
    """The viewer microphone answers the call and reaches the panel."""

    async def test_mic_before_session_is_routed_after_attach(self):
        session = _session()
        coordinator = _coordinator(session)
        peer = WebRtcPeer(coordinator)
        track = MagicMock(kind="audio")
        track.recv = AsyncMock(return_value="frame")
        peer._mic_track = track

        await peer._pump_microphone(track)  # session not there yet
        coordinator.pickup.assert_not_awaited()

        peer._session = session
        await peer._route_microphone()

        coordinator.pickup.assert_awaited_once()
        session.set_audio_source.assert_called_once_with(track)
        # Routed once, even if the session is (re)attached
        await peer._route_microphone()
        coordinator.pickup.assert_awaited_once()

    async def test_mic_after_session_is_routed_at_once(self):
        session = _session()
        coordinator = _coordinator(session)
        peer = WebRtcPeer(coordinator)
        peer._session = session
        pc = await _run(peer, [])
        track = MagicMock(kind="audio")
        track.recv = AsyncMock(return_value="frame")

        pc.handlers["track"](track)
        pc.handlers["track"](MagicMock(kind="video"))
        await asyncio.gather(*peer._tasks)

        assert len(peer._tasks) == 1
        coordinator.pickup.assert_awaited_once()
        session.set_audio_source.assert_called_once_with(track)

    async def test_failed_pickup_leaves_panel_silent(self):
        session = _session()
        peer = WebRtcPeer(_coordinator(session, pickup=False))
        peer._session = session
        peer._mic_track = MagicMock()
        peer._mic_ready = True

        await peer._route_microphone()

        session.set_audio_source.assert_not_called()

    async def test_ended_track_never_answers(self):
        coordinator = _coordinator(_session())
        peer = WebRtcPeer(coordinator)
        peer._session = coordinator.ensure_stream.return_value
        track = MagicMock()
        track.recv = AsyncMock(side_effect=MediaStreamError)

        await peer._pump_microphone(track)

        coordinator.pickup.assert_not_awaited()


class TestPeerLifecycle:
    """Connection state and teardown."""

    async def test_connection_failure_closes_peer(self):
        peer = WebRtcPeer(_coordinator())
        pc = await _run(peer, [])

        pc.connectionState = "connected"
        await pc.handlers["connectionstatechange"]()
        assert peer._closed is False

        pc.connectionState = "failed"
        await pc.handlers["connectionstatechange"]()
        assert peer._closed is True

    async def test_close_stops_tracks_and_is_idempotent(self):
        coordinator = _coordinator(None)
        coordinator.ensure_stream = AsyncMock(side_effect=_never_returns)
        peer = WebRtcPeer(coordinator)
        pc = await _run(peer, [_offer()])
        video, audio = pc.tracks

        await peer.close()
        await peer.close()

        assert video.readyState == "ended"
        assert audio.readyState == "ended"
        pc.close.assert_awaited_once()
        await asyncio.sleep(0)  # let the cancelled attach task unwind
        assert all(task.done() for task in peer._tasks)


class TestView:
    """The signaling endpoint gates on the per-intercom token."""

    async def test_unknown_token_is_not_found(self):
        view = FermaxWebRtcView({})
        with pytest.raises(web.HTTPNotFound):
            await view.get(MagicMock(), "nope")

    async def test_known_token_is_bridged(self):
        coordinator = MagicMock()
        view = FermaxWebRtcView({"tok": coordinator})
        ws = MagicMock(prepare=AsyncMock(), close=AsyncMock())
        peer = MagicMock(run=AsyncMock())

        with (
            patch.object(webrtc_bridge.web, "WebSocketResponse", return_value=ws),
            patch.object(webrtc_bridge, "WebRtcPeer", return_value=peer) as peer_cls,
        ):
            assert await view.get(MagicMock(), "tok") is ws

        peer_cls.assert_called_once_with(coordinator)
        peer.run.assert_awaited_once_with(ws)
        assert view.requires_auth is False


class TestFastH264:
    """aiortc's H264 encoder is switched to x264's ultrafast preset once."""

    def test_context_carries_preset(self):
        codec = webrtc_bridge._make_h264_context(64, 48, 1_000_000)
        assert codec.options == {"level": "31", "tune": "zerolatency", "preset": "ultrafast"}
        assert (codec.width, codec.height, codec.bit_rate) == (64, 48, 1_000_000)

    def test_encoder_uses_our_context_and_survives_resizes(self):
        import av
        from aiortc import codecs

        real_make = webrtc_bridge._make_h264_context
        created = []

        def _spy(*args):
            codec = real_make(*args)
            created.append(codec)
            return codec

        with (
            patch.object(webrtc_bridge, "_FAST_H264_APPLIED", False),
            patch.object(webrtc_bridge, "_make_h264_context", side_effect=_spy),
        ):
            webrtc_bridge._prefer_fast_h264()
            encoder = codecs.H264Encoder()
            assert type(encoder).__name__ == "_FastH264Encoder"

            frame = av.VideoFrame(64, 48, "yuv420p")
            frame.pts = 0
            packets = list(encoder._encode_frame(frame, force_keyframe=True))
            frame = av.VideoFrame(64, 48, "yuv420p")
            frame.pts = 1
            list(encoder._encode_frame(frame, force_keyframe=False))
            assert len(created) == 1
            assert encoder.codec is created[0]

            # A resolution change rebuilds the context through us as well
            frame = av.VideoFrame(32, 32, "yuv420p")
            frame.pts = 2
            list(encoder._encode_frame(frame, force_keyframe=True))
            assert len(created) == 2
            assert encoder.codec is created[1]
            assert encoder.codec.width == 32
        assert packets  # the keyframe produced output

    def test_applied_once(self):
        from aiortc import codecs

        with patch.object(webrtc_bridge, "_FAST_H264_APPLIED", False):
            webrtc_bridge._prefer_fast_h264()
            first = codecs.H264Encoder
            webrtc_bridge._prefer_fast_h264()
            assert codecs.H264Encoder is first

    def test_failure_keeps_defaults(self):
        import sys

        with (
            patch.object(webrtc_bridge, "_FAST_H264_APPLIED", False),
            patch.dict(sys.modules, {"aiortc.codecs.h264": None}),
        ):
            webrtc_bridge._prefer_fast_h264()  # ImportError swallowed
        assert webrtc_bridge._FAST_H264_APPLIED is True


class TestPacketPassThrough:
    """Encoded packets from the live source continue the placeholder timeline."""

    async def test_packets_are_restamped_after_the_placeholder(self):
        import av

        from custom_components.fermax_blue.webrtc_bridge import (
            PLACEHOLDER_FPS,
            _create_switchable_video_track,
        )

        track = _create_switchable_video_track(lambda: None)
        with patch.object(webrtc_bridge.asyncio, "sleep", AsyncMock()):
            for _ in range(3):
                await track.recv()  # placeholder pts 0, 1, 2 at 1/PLACEHOLDER_FPS

        packets = []
        for pts in (900_000, 903_000):
            packet = av.Packet(b"\x00\x00\x00\x01\x65")
            packet.pts = pts
            packets.append(packet)
        source = MagicMock()
        source.recv = AsyncMock(side_effect=packets)
        track.set_source(source)

        first = await track.recv()
        second = await track.recv()
        assert first.pts == 3 * 90000 // PLACEHOLDER_FPS
        assert second.pts == first.pts + 3_000
        track.stop()
