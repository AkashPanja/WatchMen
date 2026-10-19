"""Enrolled face gallery for Watchmen.

Photos are stored under ``<config>/media/watchmen/faces/<slug>/`` so they can
be attached to AI task calls with ``media-source://`` URIs. Multiple photos
per person improve matching quality.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import slugify
from PIL import Image

from .const import DOMAIN, MAX_PHOTOS_PER_PERSON, MEDIA_ROOT_SUBDIR

_LOGGER = logging.getLogger(__name__)

_META_FILE = "meta.json"


def media_uri(relative_path: str) -> str:
    """Build a media-source URI for a path relative to <config>/media."""
    return f"media-source://media_source/local/{relative_path}"


class FaceStore:
    """File-backed store of enrolled faces (one folder per person)."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store under the local media directory."""
        self.hass = hass
        # Stored under <config>/media so media-source URIs can reach them.
        self.root = Path(hass.config.path("media", *MEDIA_ROOT_SUBDIR))

    # ------------------------------------------------------------------
    # Synchronous file helpers (run in the executor)
    # ------------------------------------------------------------------
    def _validate_image(self, data: bytes) -> None:
        """Raise if the payload is not a decodable image."""
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
        except Exception as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_image",
                translation_placeholders={"error": str(err)},
            ) from err

    def _person_dir(self, name: str) -> Path:
        """Return (creating if needed) the folder for a person's photos."""
        folder = slugify(name) or "person"
        person_dir = self.root / folder
        person_dir.mkdir(parents=True, exist_ok=True)
        meta_path = person_dir / _META_FILE
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps({"name": name, "slug": folder}), encoding="utf-8"
            )
        return person_dir

    def _add_sync(self, name: str, data: bytes) -> int:
        """Validate and store one photo; return the new photo count."""
        self._validate_image(data)
        person_dir = self._person_dir(name)
        photos = sorted(
            p for p in person_dir.iterdir() if p.suffix.lower() in {".jpg", ".png"}
        )
        while len(photos) >= MAX_PHOTOS_PER_PERSON:
            oldest = photos.pop(0)
            oldest.unlink(missing_ok=True)
            _LOGGER.debug("Replaced oldest photo for %s: %s", name, oldest.name)
        index = int(self._existing_count(person_dir)) + 1
        photo = person_dir / f"photo_{index:03d}.jpg"
        while photo.exists():
            index += 1
            photo = person_dir / f"photo_{index:03d}.jpg"
        photo.write_bytes(data)
        return len(photos) + 1

    @staticmethod
    def _existing_count(person_dir: Path) -> int:
        """Count photos in a person folder."""
        return sum(
            1
            for p in person_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".png"}
        )

    def _list_sync(self) -> dict[str, dict[str, Any]]:
        """Return {display name: {slug, photos: [relative media paths]}}."""
        result: dict[str, dict[str, Any]] = {}
        if not self.root.exists():
            return result
        for person_dir in sorted(self.root.iterdir()):
            if not person_dir.is_dir():
                continue
            name = person_dir.name
            meta_path = person_dir / _META_FILE
            if meta_path.exists():
                try:
                    name = json.loads(meta_path.read_text(encoding="utf-8"))["name"]
                except (OSError, ValueError, KeyError):
                    pass
            photos = sorted(
                str(Path(*MEDIA_ROOT_SUBDIR, person_dir.name, p.name).as_posix())
                for p in person_dir.iterdir()
                if p.suffix.lower() in {".jpg", ".png"}
            )
            result[name] = {
                "slug": person_dir.name,
                "photos": photos,
            }
        return result

    def _remove_sync(self, name: str) -> bool:
        """Delete a person's folder; return whether anything was removed."""
        folder = slugify(name) or "person"
        person_dir = self.root / folder
        if not person_dir.is_dir():
            # Also match by display name when the slug differs.
            for candidate in self.root.iterdir() if self.root.exists() else []:
                if candidate.is_dir() and self._read_name(candidate) == name:
                    person_dir = candidate
                    break
            else:
                return False
        for file in list(person_dir.iterdir()):
            file.unlink(missing_ok=True)
        person_dir.rmdir()
        return True

    @staticmethod
    def _read_name(person_dir: Path) -> str:
        """Read the display name of a person folder."""
        meta_path = person_dir / _META_FILE
        if meta_path.exists():
            try:
                return str(json.loads(meta_path.read_text(encoding="utf-8"))["name"])
            except (OSError, ValueError, KeyError):
                pass
        return person_dir.name

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------
    async def async_add(self, name: str, data: bytes) -> int:
        """Enroll one photo for a person; return the photo count."""
        return await self.hass.async_add_executor_job(self._add_sync, name, data)

    async def async_list(self) -> dict[str, dict[str, Any]]:
        """Return all enrolled people and their photos."""
        return await self.hass.async_add_executor_job(self._list_sync)

    async def async_remove(self, name: str) -> bool:
        """Remove a person and all their photos."""
        return await self.hass.async_add_executor_job(self._remove_sync, name)
