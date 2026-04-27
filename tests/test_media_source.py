"""Tests for Fermax Blue media source."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from custom_components.fermax_blue.media_source import FermaxMediaSource


@pytest.fixture
def media_source():
    mock_hass = MagicMock()
    mock_hass.config.media_dirs = {"local": "/media"}
    return FermaxMediaSource(mock_hass)


class TestMediaSource:
    def test_base_path(self, media_source):
        assert media_source._base_path() == Path("/media/fermax_recordings")

    def test_base_path_custom_media_dir(self):
        mock_hass = MagicMock()
        mock_hass.config.media_dirs = {"local": "/custom/media"}
        ms = FermaxMediaSource(mock_hass)
        assert ms._base_path() == Path("/custom/media/fermax_recordings")

    @pytest.mark.asyncio
    async def test_resolve_media_no_identifier(self, media_source):
        from homeassistant.components.media_source import Unresolvable

        item = MagicMock()
        item.identifier = ""
        with pytest.raises(Unresolvable):
            await media_source.async_resolve_media(item)

    @pytest.mark.asyncio
    async def test_resolve_media_path_traversal(self, media_source):
        from homeassistant.components.media_source import Unresolvable

        # Mock async_add_executor_job to run the function directly
        media_source.hass.async_add_executor_job = lambda fn: (
            asyncio.get_event_loop().run_in_executor(None, fn)
        )

        item = MagicMock()
        item.identifier = "../../etc/passwd"
        with pytest.raises(Unresolvable):
            await media_source.async_resolve_media(item)

    @pytest.mark.asyncio
    async def test_resolve_media_photo(self, media_source, tmp_path):
        photo = tmp_path / "fermax_recordings" / "2026-04-12_14-30-45_photo.jpg"
        photo.parent.mkdir(parents=True)
        photo.write_bytes(b"fake jpg")

        # Mock async_add_executor_job to run the function directly
        media_source.hass.async_add_executor_job = lambda fn: (
            asyncio.get_event_loop().run_in_executor(None, fn)
        )

        with patch.object(media_source, "_base_path", return_value=photo.parent):
            item = MagicMock()
            item.identifier = "2026-04-12_14-30-45_photo.jpg"
            result = await media_source.async_resolve_media(item)
            assert result.mime_type == "image/jpeg"
            assert "fermax_recordings" in result.url

    def test_browse_empty_dir(self, media_source, tmp_path):
        recordings = tmp_path / "fermax_recordings"
        recordings.mkdir()
        with patch.object(media_source, "_base_path", return_value=recordings):
            result = media_source._browse(None)
            assert result.title == "Fermax Blue"
            assert result.children == []

    def test_browse_with_photos(self, media_source, tmp_path):
        recordings = tmp_path / "fermax_recordings"
        recordings.mkdir()
        (recordings / "2026-04-12_14-30-45_photo.jpg").write_bytes(b"jpg1")
        (recordings / "2026-04-12_15-00-00_photo.jpg").write_bytes(b"jpg2")

        with patch.object(media_source, "_base_path", return_value=recordings):
            result = media_source._browse(None)
            assert len(result.children) == 2
            assert "2 photos" in result.title
            # Newest first
            assert "15:00:00" in result.children[0].title

    def test_browse_nonexistent_dir(self, media_source, tmp_path):
        with patch.object(media_source, "_base_path", return_value=tmp_path / "nope"):
            result = media_source._browse(None)
            assert result.children == []
