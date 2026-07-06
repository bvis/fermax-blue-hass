"""Tests for the streaming module."""

from unittest.mock import patch

from custom_components.fermax_blue.streaming import streaming_deps_available


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
