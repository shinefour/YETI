"""Image storage — persistent file storage for captured images."""

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_DIR = Path("/data/yeti/images")


def _ensure_dir() -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    return IMAGE_DIR


def save_image(
    image_bytes: bytes,
    extension: str = "jpg",
) -> str:
    """Save an image to disk and return its ID."""
    _ensure_dir()
    image_id = str(uuid.uuid4())
    path = IMAGE_DIR / f"{image_id}.{extension}"
    path.write_bytes(image_bytes)
    logger.info("Saved image: %s (%d bytes)", image_id, len(image_bytes))
    return image_id


def get_image_path(image_id: str) -> Path | None:
    """Resolve an image ID to its file path. Returns None if not found."""
    _ensure_dir()
    # Try common extensions
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = IMAGE_DIR / f"{image_id}.{ext}"
        if path.exists():
            return path
    return None


def get_image_bytes(image_id: str) -> bytes | None:
    path = get_image_path(image_id)
    if not path:
        return None
    return path.read_bytes()
