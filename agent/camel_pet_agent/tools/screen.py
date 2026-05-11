"""Screen capture tool.

Captures the primary monitor and returns a base64-encoded PNG image,
optionally resized for efficient LLM vision analysis.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image

log = logging.getLogger("camel-pet.screen")

# Max dimension for the resized screenshot sent to vision models
_MAX_DIM = int(os.environ.get("CAMEL_PET_SCREENSHOT_MAX_DIM", "512"))


def _screenshots_dir() -> Path:
    """Return the local directory for saving captured screenshots.

    Reads CAMEL_PET_SCREENSHOTS_DIR from env; falls back to the platform default.
    Creates the directory if it does not exist.
    """
    env_dir = os.environ.get("CAMEL_PET_SCREENSHOTS_DIR")
    if env_dir:
        d = Path(env_dir)
    else:
        if os.name == "nt":
            root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        elif os.sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
        else:
            root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        d = root / "CamelPet" / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def capture_screenshot(max_dim: int = _MAX_DIM, save_dir: Path | None = None) -> str:
    """Capture the primary monitor and return a base64-encoded PNG.

    The image is resized so that the longest side is at most `max_dim` pixels,
    keeping the aspect ratio. This reduces token cost when sent to vision APIs.
    The resized image is also saved to disk for local monitoring.

    Args:
        max_dim: Maximum pixel dimension for the longest side.
        save_dir: Directory to save the screenshot. Defaults to _screenshots_dir().
    """
    image_format = "JPEG"  # PNG is lossless but larger;
    quality = 100 #JPEG with quality=50 is a good tradeoff for vision models
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # primary monitor
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.rgb)

    # Resize to reduce API cost
    w, h = img.size
    if max(w, h) > max_dim:
        ratio = max_dim / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)

    # Save to disk for local monitoring
    dest = save_dir or _screenshots_dir()
    dest.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    save_path = dest / f"{timestamp}.png"
    try:
        img.save(save_path, format=image_format, quality=quality)
        log.debug("screenshot saved: %s", save_path)
    except Exception as e:
        log.warning("failed to save screenshot to disk: %s", e)

    buf = io.BytesIO()
    img.save(buf, format=image_format, quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")
