"""Tests for the FCM notification listener."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.fermax_blue.notification import FermaxNotificationListener


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

    new_client = MagicMock()
    new_client.is_started = MagicMock(return_value=False)
    new_client.start = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ):
        assert await listener.ensure_running() is False


async def test_start_passes_resilient_config(listener):
    new_client = MagicMock()
    new_client.start = AsyncMock()
    new_client.is_started = MagicMock(return_value=True)

    with patch(
        "custom_components.fermax_blue.notification.FcmPushClient",
        return_value=new_client,
    ) as fcm_cls:
        await listener.start()

    kwargs = fcm_cls.call_args.kwargs
    assert kwargs["config"].abort_on_sequential_error_count is None


async def test_concurrent_ensure_running_creates_one_client(listener):
    """Overlapping watchdog ticks must not spawn parallel FcmPushClient instances."""
    dead_client = MagicMock()
    dead_client.is_started = MagicMock(return_value=False)
    dead_client.stop = AsyncMock()
    listener._push_client = dead_client

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
