"""Media source for browsing Fermax Blue doorbell photos."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, RECORDINGS_DIR


async def async_get_media_source(hass: HomeAssistant) -> FermaxMediaSource:
    """Set up Fermax Blue media source."""
    return FermaxMediaSource(hass)


class FermaxMediaSource(MediaSource):
    """Provide Fermax Blue doorbell photos as a media source."""

    name = "Fermax Blue"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _base_path(self) -> Path:
        """Return the base path for recordings/photos."""
        media_root = self.hass.config.media_dirs.get("local", "/media")
        return Path(media_root) / RECORDINGS_DIR

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to a playable URL."""
        identifier = item.identifier
        if not identifier:
            raise Unresolvable("No identifier provided")

        base = self._base_path()
        file_path = base / identifier

        # Prevent path traversal
        try:
            file_path.resolve().relative_to(base.resolve())
        except ValueError as err:
            raise Unresolvable("Invalid media path") from err

        if not file_path.is_file():
            raise Unresolvable(f"File not found: {identifier}")

        mime = "image/jpeg" if file_path.suffix == ".jpg" else "video/mp4"
        return PlayMedia(
            url=f"/media/local/{RECORDINGS_DIR}/{identifier}",
            mime_type=mime,
        )

    async def async_browse_media(
        self,
        item: MediaSourceItem,
    ) -> BrowseMediaSource:
        """Browse available media."""
        return await self.hass.async_add_executor_job(self._browse, item.identifier)

    def _browse(self, identifier: str | None) -> BrowseMediaSource:
        """Build the browse tree (runs in executor)."""
        base = self._base_path()

        children: list[BrowseMediaSource] = []
        if base.is_dir():
            # List photos (jpg) and recordings (mp4), newest first (by filename, which encodes timestamp)
            files = sorted(
                (f for f in base.iterdir() if f.is_file() and f.suffix in (".jpg", ".mp4")),
                key=lambda f: f.name,
                reverse=True,
            )
            for f in files:
                is_photo = f.suffix == ".jpg"
                # Format: "2026-04-12_14-30-45_photo.jpg" -> "2026-04-12 14:30:45 (photo)"
                parts = f.stem.replace("_photo", "").split("_")
                if len(parts) >= 2:
                    date_part = parts[0]
                    time_part = parts[1].replace("-", ":")
                    suffix = " (photo)" if is_photo else " (video)"
                    title = f"{date_part} {time_part}{suffix}"
                else:
                    title = f.name

                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f.name,
                        media_class=MediaClass.IMAGE if is_photo else MediaClass.VIDEO,
                        media_content_type=MediaType.IMAGE if is_photo else MediaType.VIDEO,
                        title=title,
                        can_play=True,
                        can_expand=False,
                        thumbnail=None,
                    )
                )

        photo_count = sum(1 for c in children if c.media_class == MediaClass.IMAGE)
        video_count = sum(1 for c in children if c.media_class == MediaClass.VIDEO)
        title_parts = []
        if photo_count:
            title_parts.append(f"{photo_count} photos")
        if video_count:
            title_parts.append(f"{video_count} videos")

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=f"Fermax Blue ({', '.join(title_parts)})" if title_parts else "Fermax Blue",
            can_play=False,
            can_expand=True,
            children=children,
        )
