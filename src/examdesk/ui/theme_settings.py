from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

SETTINGS_VERSION = 1
DEFAULT_THEME_ID = "clean_blue"
THEME_IDS = frozenset(
    {
        "clean_blue",
        "classic_green",
        "minimal_light",
        "coral",
        "graphite_dark",
        "bright_teal",
    }
)
BACKGROUND_SCOPES = frozenset({"none", "home", "all"})
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_BACKGROUND_PIXELS = 50_000_000
MAX_BACKGROUND_DIMENSION = 3840
Image.MAX_IMAGE_PIXELS = MAX_BACKGROUND_PIXELS


@dataclass(frozen=True, slots=True)
class ThemeSettings:
    version: int = SETTINGS_VERSION
    theme_id: str = DEFAULT_THEME_ID
    accent_color: str | None = None
    background_scope: str = "none"
    background_file: str = ""

    def normalized(self) -> ThemeSettings:
        theme_id = self.theme_id if self.theme_id in THEME_IDS else DEFAULT_THEME_ID
        accent = (
            self.accent_color.upper()
            if self.accent_color and COLOR_PATTERN.fullmatch(self.accent_color)
            else None
        )
        background_file = Path(self.background_file).name if self.background_file else ""
        scope = self.background_scope if self.background_scope in BACKGROUND_SCOPES else "none"
        if not background_file:
            scope = "none"
        return ThemeSettings(
            version=SETTINGS_VERSION,
            theme_id=theme_id,
            accent_color=accent,
            background_scope=scope,
            background_file=background_file,
        )


class ThemeSettingsStore:
    def __init__(self, app_directory: Path) -> None:
        self.app_directory = app_directory.resolve()
        self.settings_path = self.app_directory / "theme.json"
        self.theme_directory = self.app_directory / "theme"

    def load(self) -> ThemeSettings:
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
            settings = ThemeSettings(
                version=int(payload.get("version", SETTINGS_VERSION)),
                theme_id=str(payload.get("theme_id", DEFAULT_THEME_ID)),
                accent_color=payload.get("accent_color"),
                background_scope=str(payload.get("background_scope", "none")),
                background_file=str(payload.get("background_file", "")),
            ).normalized()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return ThemeSettings()
        if settings.background_file and self.background_path(settings) is None:
            return replace(settings, background_scope="none", background_file="")
        return settings

    def save(self, settings: ThemeSettings) -> ThemeSettings:
        normalized = settings.normalized()
        self.app_directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(normalized), ensure_ascii=False, indent=2) + "\n"
        _atomic_write(self.settings_path, payload.encode("utf-8"))
        return normalized

    def install_background(self, source: Path) -> str:
        try:
            with Image.open(source) as opened:
                opened.load()
                image = ImageOps.exif_transpose(opened)
                image.thumbnail(
                    (MAX_BACKGROUND_DIMENSION, MAX_BACKGROUND_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                if _has_alpha(image):
                    image.convert("RGBA").save(output, format="PNG", optimize=True)
                    extension = ".png"
                else:
                    image.convert("RGB").save(
                        output,
                        format="JPEG",
                        quality=90,
                        optimize=True,
                        progressive=True,
                    )
                    extension = ".jpg"
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ValueError("无法读取所选背景图片") from exc
        data = output.getvalue()
        name = f"background-{hashlib.sha256(data).hexdigest()[:16]}{extension}"
        destination = self.theme_directory / name
        self.theme_directory.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            _atomic_write(destination, data)
        return name

    def background_path(self, settings: ThemeSettings) -> Path | None:
        if not settings.background_file:
            return None
        path = (self.theme_directory / settings.background_file).resolve()
        if self.theme_directory != path.parent or not path.is_file():
            return None
        return path

    def remove_unused_backgrounds(self, keep_file: str = "") -> None:
        if not self.theme_directory.is_dir():
            return
        for path in self.theme_directory.glob("background-*"):
            if path.is_file() and path.name != keep_file:
                with contextlib.suppress(OSError):
                    path.unlink()


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise
