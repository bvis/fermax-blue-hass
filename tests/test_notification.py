"""Tests for the FCM notification listener."""

import asyncio
import binascii
import inspect
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from unittest.mock import AsyncMock, MagicMock, patch

import http_ece
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from firebase_messaging.fcmpushclient import FcmPushClient

from custom_components.fermax_blue.notification import (
    FCM_ABORT_SEQUENTIAL_ERROR_COUNT,
    FCM_EXC_LOG_LIMIT,
    FCM_EXC_LOG_WINDOW,
    FCM_RESTART_BACKOFF_INITIAL,
    FCM_RESTART_BACKOFF_MAX,
    FCM_UPSTREAM_LOGGER,
    FermaxNotificationListener,
    _b64_pad,
    _decrypt_push,
    _FcmExcInfoRateLimitFilter,
)


class _FakeClock:
    """Deterministic stand-in for the time module (monotonic only)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


def _make_listener(*, with_credentials: bool = True) -> FermaxNotificationListener:
    listener = FermaxNotificationListener(
        hass=MagicMock(),
        notification_callback=lambda *a, **kw: None,
        firebase_api_key="key",
        firebase_sender_id=1,
        firebase_app_id="app",
        firebase_project_id="proj",
        firebase_package_name="com.fermax.blue.app",
    )
    if with_credentials:
        listener._credentials = {"fcm": {"registration": {"token": "tok"}}}
    return listener


@pytest.fixture
def listener():
    return _make_listener()


async def test_ensure_running_noop_when_already_started(listener):
    push_client = MagicMock()
    push_client.is_started = MagicMock(return_value=True)
    push_client.start = AsyncMock()
    push_client.stop = AsyncMock()
    listener._push_client = push_client

    assert await listener.ensure_running() is True
    push_client.start.assert_not_called()
    push_client.stop.assert_not_called()


async def test_ensure_running_revives_dead_listener(listener):
    dead_client = MagicMock()
    dead_client.is_started = MagicMock(return_value=False)
    dead_client.stop = AsyncMock()
    listener._push_client = dead_client
    listener._restart_at = 0.0  # backoff deadline already elapsed

    new_client = MagicMock()
    new_client.is_started = MagicMock(return_value=True)
    new_client.start = AsyncMock()

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ) as fcm_cls:
        ok = await listener.ensure_running()

    assert ok is True
    dead_client.stop.assert_awaited_once()
    new_client.start.assert_awaited_once()
    fcm_cls.assert_called_once()


async def test_ensure_running_returns_false_without_credentials():
    listener = _make_listener(with_credentials=False)
    assert await listener.ensure_running() is False


async def test_ensure_running_swallows_stop_errors(listener):
    dead_client = MagicMock()
    dead_client.is_started = MagicMock(return_value=False)
    dead_client.stop = AsyncMock(side_effect=RuntimeError("already gone"))
    listener._push_client = dead_client
    listener._restart_at = 0.0  # backoff deadline already elapsed

    new_client = MagicMock()
    new_client.is_started = MagicMock(return_value=True)
    new_client.start = AsyncMock()

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ):
        assert await listener.ensure_running() is True

    new_client.start.assert_awaited_once()


async def test_ensure_running_returns_false_when_restart_fails(listener):
    dead_client = MagicMock()
    dead_client.is_started = MagicMock(return_value=False)
    dead_client.stop = AsyncMock()
    listener._push_client = dead_client
    listener._restart_at = 0.0  # backoff deadline already elapsed

    new_client = MagicMock()
    new_client.is_started = MagicMock(return_value=False)
    new_client.start = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ):
        assert await listener.ensure_running() is False


async def test_start_passes_bounded_abort_config(listener):
    """The client must abort after a few sequential errors instead of spinning forever.

    Unbounded retries inside firebase_messaging's _listen loop caused an HA
    event-loop CPU exhaustion (issue #12); the watchdog restarts the client
    with delayed backoff instead.
    """
    new_client = MagicMock()
    new_client.start = AsyncMock()
    new_client.is_started = MagicMock(return_value=True)

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ) as fcm_cls:
        await listener.start()

    kwargs = fcm_cls.call_args.kwargs
    assert kwargs["config"].abort_on_sequential_error_count == FCM_ABORT_SEQUENTIAL_ERROR_COUNT


async def test_concurrent_ensure_running_creates_one_client(listener):
    """Overlapping watchdog ticks must not spawn parallel FcmPushClient instances."""
    dead_client = MagicMock()
    dead_client.is_started = MagicMock(return_value=False)
    dead_client.stop = AsyncMock()
    listener._push_client = dead_client
    listener._restart_at = 0.0  # backoff deadline already elapsed

    started = asyncio.Event()
    release = asyncio.Event()
    instances: list[MagicMock] = []

    def _factory(*args, **kwargs):
        client = MagicMock()
        client.is_started = MagicMock(side_effect=lambda: release.is_set())
        client.stop = AsyncMock()

        async def _slow_start():
            started.set()
            await release.wait()

        client.start = AsyncMock(side_effect=_slow_start)
        instances.append(client)
        return client

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        side_effect=_factory,
    ):
        first = asyncio.create_task(listener.ensure_running())
        await started.wait()
        second = asyncio.create_task(listener.ensure_running())
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)

    assert results == [True, True]
    assert len(instances) == 1


def _dead_client() -> MagicMock:
    client = MagicMock()
    client.is_started = MagicMock(return_value=False)
    client.stop = AsyncMock()
    return client


def _healthy_client() -> MagicMock:
    client = MagicMock()
    client.is_started = MagicMock(return_value=True)
    client.start = AsyncMock()
    client.stop = AsyncMock()
    return client


async def test_ensure_running_delays_restart_until_backoff_elapses(listener):
    """First death schedules a delayed restart instead of restarting immediately."""
    listener._push_client = _dead_client()
    clock = _FakeClock()

    with (
        patch("custom_components.fermax_blue.notification.time", clock),
        patch(
            "custom_components.fermax_blue.notification.FcmPushClient",
            return_value=_healthy_client(),
        ) as fcm_cls,
    ):
        assert await listener.ensure_running() is False
        fcm_cls.assert_not_called()

        clock.now += FCM_RESTART_BACKOFF_INITIAL - 1
        assert await listener.ensure_running() is False
        fcm_cls.assert_not_called()

        clock.now += 2
        assert await listener.ensure_running() is True
        fcm_cls.assert_called_once()


async def test_ensure_running_backoff_escalates_and_caps(listener):
    """Each failed restart doubles the delay up to the maximum."""
    listener._push_client = _dead_client()
    clock = _FakeClock()

    def _failing_factory(*args, **kwargs):
        client = _dead_client()
        client.start = AsyncMock(side_effect=RuntimeError("still broken"))
        return client

    with (
        patch("custom_components.fermax_blue.notification.time", clock),
        patch(
            "custom_components.fermax_blue.notification.FcmPushClient",
            side_effect=_failing_factory,
        ) as fcm_cls,
    ):
        # Schedule, then first attempt after the initial delay.
        assert await listener.ensure_running() is False
        clock.now += FCM_RESTART_BACKOFF_INITIAL + 1
        assert await listener.ensure_running() is False
        assert fcm_cls.call_count == 1

        # Re-schedule with doubled delay: not yet at 2x-1, attempt at 2x+1.
        assert await listener.ensure_running() is False
        clock.now += FCM_RESTART_BACKOFF_INITIAL * 2 - 1
        assert await listener.ensure_running() is False
        assert fcm_cls.call_count == 1
        clock.now += 2
        assert await listener.ensure_running() is False
        assert fcm_cls.call_count == 2

        # Delay is capped at the maximum.
        assert listener._restart_backoff == FCM_RESTART_BACKOFF_MAX


async def test_healthy_observation_resets_backoff(listener):
    """A healthy listener resets the escalated backoff to the initial delay."""
    listener._push_client = _healthy_client()
    listener._restart_backoff = FCM_RESTART_BACKOFF_MAX
    listener._restart_at = 12345.0

    assert await listener.ensure_running() is True
    assert listener._restart_backoff == FCM_RESTART_BACKOFF_INITIAL
    assert listener._restart_at is None


async def test_transient_reset_window_emits_no_warning(listener, caplog):
    """A tick landing inside a routine reset window must not alarm the operator.

    ``is_started()`` is also False during seconds-long transient states
    (RESETTING, STARTING_*); scheduling the restart must stay below WARNING,
    and the next healthy tick must clear the schedule without restarting.
    """
    client = _dead_client()
    listener._push_client = client
    clock = _FakeClock()

    with (
        patch("custom_components.fermax_blue.notification.time", clock),
        patch("custom_components.fermax_blue.notification.FcmPushClient") as fcm_cls,
        caplog.at_level(logging.DEBUG, logger="custom_components.fermax_blue.notification"),
    ):
        assert await listener.ensure_running() is False  # tick inside the reset window
        client.is_started.return_value = True  # client recovered on its own
        assert await listener.ensure_running() is True

    fcm_cls.assert_not_called()
    assert listener._restart_at is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_ensure_running_logs_unexpected_restart_errors(listener, caplog):
    """Restart failures outside the connection-error family must not vanish.

    The watchdog gathers with ``return_exceptions=True`` and discards results,
    so anything escaping this catch is swallowed with no log line at all.
    """
    listener._push_client = _dead_client()
    listener._restart_at = 0.0  # backoff deadline already elapsed

    new_client = _dead_client()
    new_client.start = AsyncMock(side_effect=ValueError("bad registration payload"))

    with (
        patch(
            "custom_components.fermax_blue.notification.FcmPushClient",
            return_value=new_client,
        ),
        caplog.at_level(logging.ERROR, logger="custom_components.fermax_blue.notification"),
    ):
        assert await listener.ensure_running() is False

    assert any(
        r.exc_info and "Failed to restart FCM listener" in r.getMessage() for r in caplog.records
    )


async def test_ensure_running_returns_true_while_restarted_client_connects(listener):
    """A successful restart returns True even though the client is still in STARTING_*."""
    listener._push_client = _dead_client()
    listener._restart_at = 0.0  # backoff deadline already elapsed

    connecting_client = _dead_client()  # is_started stays False while connecting
    connecting_client.start = AsyncMock()

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=connecting_client,
    ):
        assert await listener.ensure_running() is True

    connecting_client.start.assert_awaited_once()


async def test_start_installs_exc_rate_limit_filter_once(listener):
    """start() attaches the rate-limit filter to the upstream logger exactly once."""
    upstream = logging.getLogger(FCM_UPSTREAM_LOGGER)
    for existing in list(upstream.filters):
        if isinstance(existing, _FcmExcInfoRateLimitFilter):
            upstream.removeFilter(existing)

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=_healthy_client(),
    ):
        await listener.start()
        await listener.start()

    installed = [f for f in upstream.filters if isinstance(f, _FcmExcInfoRateLimitFilter)]
    assert len(installed) == 1


def _exc_record(msg: str = "Unexpected exception during read") -> logging.LogRecord:
    try:
        raise ConnectionResetError("Connection lost")
    except ConnectionResetError:
        import sys

        exc_info = sys.exc_info()
    return logging.LogRecord(FCM_UPSTREAM_LOGGER, logging.ERROR, __file__, 1, msg, None, exc_info)


def test_exc_filter_strips_tracebacks_after_limit():
    """Only the first N tracebacks in a window are kept; later ones become one-liners."""
    clock = _FakeClock()
    with patch("custom_components.fermax_blue.notification.time", clock):
        log_filter = _FcmExcInfoRateLimitFilter()
        records = [_exc_record() for _ in range(FCM_EXC_LOG_LIMIT + 2)]
        results = [log_filter.filter(record) for record in records]

    assert all(results)  # records are never dropped, only stripped
    assert all(record.exc_info for record in records[:FCM_EXC_LOG_LIMIT])
    for record in records[FCM_EXC_LOG_LIMIT:]:
        assert record.exc_info is None
        assert "suppressed" in record.getMessage()


def test_exc_filter_allows_tracebacks_again_after_window():
    """Once the window expires, full tracebacks are logged again."""
    clock = _FakeClock()
    with patch("custom_components.fermax_blue.notification.time", clock):
        log_filter = _FcmExcInfoRateLimitFilter()
        for _ in range(FCM_EXC_LOG_LIMIT + 1):
            log_filter.filter(_exc_record())

        clock.now += FCM_EXC_LOG_WINDOW + 1
        record = _exc_record()
        assert log_filter.filter(record) is True
        assert record.exc_info is not None


def test_exc_filter_ignores_none_exc_info_tuple():
    """exc_info=(None, None, None) carries no traceback and must pass untouched.

    ``logging`` produces this truthy tuple for ``exc_info=True`` outside an
    ``except`` block; it must not consume throttle budget or gain the
    "suppressed" suffix.
    """
    clock = _FakeClock()
    with patch("custom_components.fermax_blue.notification.time", clock):
        log_filter = _FcmExcInfoRateLimitFilter()
        record = logging.LogRecord(
            FCM_UPSTREAM_LOGGER,
            logging.ERROR,
            __file__,
            1,
            "no traceback",
            None,
            (None, None, None),
        )
        assert log_filter.filter(record) is True
        assert record.getMessage() == "no traceback"

        # The full traceback budget must still be available afterwards.
        real_records = [_exc_record() for _ in range(FCM_EXC_LOG_LIMIT)]
        assert all(log_filter.filter(r) for r in real_records)
        assert all(r.exc_info for r in real_records)


def test_exc_filter_ignores_records_without_exc_info():
    """Plain records pass through untouched regardless of the budget state."""
    clock = _FakeClock()
    with patch("custom_components.fermax_blue.notification.time", clock):
        log_filter = _FcmExcInfoRateLimitFilter()
        for _ in range(FCM_EXC_LOG_LIMIT + 5):
            log_filter.filter(_exc_record())

        record = logging.LogRecord(
            FCM_UPSTREAM_LOGGER, logging.INFO, __file__, 1, "plain message", None, None
        )
        assert log_filter.filter(record) is True
        assert record.getMessage() == "plain message"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("A" * 86, "A" * 86 + "=="),  # len % 4 == 2
        ("A" * 87, "A" * 87 + "="),  # len % 4 == 3
        ("A" * 88, "A" * 88),  # already a multiple of 4 -> unchanged
        ("", ""),
    ],
)
def test_b64_pad_normalises_length_to_multiple_of_four(value, expected):
    padded = _b64_pad(value)
    assert padded == expected
    assert len(padded) % 4 == 0


def test_b64_pad_makes_incorrect_padding_value_decodable():
    """An 'Incorrect padding' base64 value becomes decodable after padding (issue #21)."""
    unpadded = "A" * 86  # len % 4 == 2 -> urlsafe_b64decode raises Incorrect padding
    with pytest.raises(binascii.Error):
        urlsafe_b64decode(unpadded.encode("ascii"))
    # Must not raise after padding.
    urlsafe_b64decode(_b64_pad(unpadded).encode("ascii"))


def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.fixture
def signed_push():
    """Credentials plus one push encrypted the way the sender does (aesgcm)."""
    receiver = ec.generate_private_key(ec.SECP256R1())
    sender = ec.generate_private_key(ec.SECP256R1())
    secret, salt = os.urandom(16), os.urandom(16)
    point = serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    raw = http_ece.encrypt(
        b'{"data": "ring"}',
        salt=salt,
        private_key=sender,
        dh=receiver.public_key().public_bytes(*point),
        version="aesgcm",
        auth_secret=secret,
    )
    der = receiver.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credentials = {"keys": {"private": _b64(der), "secret": _b64(secret)}}
    return credentials, _b64(sender.public_key().public_bytes(*point)), _b64(salt), raw


@pytest.mark.parametrize(
    "crypto_key",
    [
        "{dh}",  # unpadded, as upstream hands it over after slicing "dh=" (#21)
        "{dh}; p256ecdsa={vapid}",  # signed push: upstream keeps the VAPID key (#117)
        "6ecdsa={vapid};dh={dh}",  # signed, other order: "p25" sliced off instead
    ],
)
def test_decrypt_push_decrypts_signed_and_unpadded_headers(signed_push, crypto_key):
    credentials, dh, salt, raw = signed_push
    header = crypto_key.format(dh=dh, vapid="B" * 87)

    assert _decrypt_push(credentials, header, salt, raw) == b'{"data": "ring"}'


def test_decrypt_push_returns_empty_bytes_and_warns_on_failure(signed_push, caplog):
    """A bad push is skipped (b"" makes upstream ack it) instead of killing the
    client on every redelivery (#25)."""
    credentials, _dh, salt, raw = signed_push

    with caplog.at_level(logging.WARNING):
        assert _decrypt_push(credentials, "A" * 87, salt, raw) == b""

    assert "Skipping an undecryptable FCM push" in caplog.text


async def test_start_installs_decrypt_on_its_own_client_only(listener):
    """Our decrypt goes on our instance; the shared class stays untouched, so
    another integration's pushes and patches never cross with ours."""
    class_decrypt = inspect.getattr_static(FcmPushClient, "_decrypt_raw_data")
    new_client = MagicMock()
    new_client.start = AsyncMock()

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ):
        await listener.start()

    assert new_client._decrypt_raw_data is _decrypt_push
    assert inspect.getattr_static(FcmPushClient, "_decrypt_raw_data") is class_decrypt


async def test_ensure_running_leaves_a_stopped_listener_alone(listener, caplog):
    """A deliberate stop (switch off, HA shutdown) is not a crash to revive."""
    listener._push_client = MagicMock(stop=AsyncMock(), writer=None)
    await listener.stop()
    listener._restart_at = 0.0

    with (
        caplog.at_level(logging.INFO),
        patch("custom_components.fermax_blue.notification.FcmPushClient") as fcm_cls,
    ):
        assert await listener.ensure_running() is False

    fcm_cls.assert_not_called()
    assert "restart scheduled" not in caplog.text


async def test_start_after_stop_rearms_the_watchdog(listener):
    listener._push_client = MagicMock(stop=AsyncMock(), writer=None)
    await listener.stop()

    client = MagicMock(start=AsyncMock())
    client.is_started = MagicMock(return_value=False)
    with patch("custom_components.fermax_blue.notification.FcmPushClient", return_value=client):
        await listener.start()
        assert await listener.ensure_running() is False

    assert listener._restart_at is not None


async def test_stop_aborts_the_connection_before_stopping_the_client(listener):
    """The client cancels its reader without waiting, and the reader's cleanup
    waits for a TLS close the server may take tens of seconds to send."""
    calls = []
    writer = MagicMock()
    writer.transport.abort = lambda: calls.append("abort")
    client = MagicMock(writer=writer, tasks=[])
    client.stop = AsyncMock(side_effect=lambda: calls.append("stop"))
    listener._push_client = client

    await listener.stop()

    assert calls == ["abort", "stop"]
    assert listener._push_client is None


async def test_stop_waits_for_the_client_tasks(listener):
    async def reader():
        try:
            await asyncio.sleep(3600)
        finally:
            await asyncio.sleep(0)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)

    async def stop():
        task.cancel()

    listener._push_client = MagicMock(writer=None, tasks=[task], stop=stop)

    await listener.stop()

    assert task.done()


async def test_start_keeps_a_running_client(listener):
    """Turning the switch on while it is on must not open a second connection."""
    running = MagicMock(stop=AsyncMock())
    running.is_started = MagicMock(return_value=True)
    listener._push_client = running

    with patch("custom_components.fermax_blue.notification.FcmPushClient") as fcm_cls:
        await listener.start()

    fcm_cls.assert_not_called()
    running.stop.assert_not_called()
    assert listener._push_client is running


async def test_start_closes_a_dead_client_first(listener):
    dead = MagicMock(stop=AsyncMock(), writer=None, tasks=[])
    dead.is_started = MagicMock(return_value=False)
    listener._push_client = dead
    new_client = MagicMock(start=AsyncMock())

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient", return_value=new_client
    ):
        await listener.start()

    dead.stop.assert_awaited_once()
    assert listener._push_client is new_client
