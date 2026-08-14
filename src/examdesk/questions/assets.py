from __future__ import annotations

import contextlib
import hashlib
import io
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_DIMENSION = 2200
JPEG_QUALITY = 88
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class AssetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: str
    sha256: str
    perceptual_hash: str
    media_type: str
    relative_path: str
    width: int
    height: int
    byte_size: int


class AssetManager:
    def __init__(self, database, root: Path) -> None:
        self.database = database
        self.root = root.resolve()

    def ingest_bytes(self, data: bytes, original_name: str = "image") -> AssetRecord:
        if not data:
            raise AssetError("图片内容为空")
        if len(data) > MAX_SOURCE_BYTES:
            raise AssetError("图片文件超过50MB")

        normalized, media_type, extension, width, height, perceptual_hash = _normalize_image(data)
        digest = hashlib.sha256(normalized).hexdigest()
        return self._store_asset(
            normalized,
            digest,
            media_type,
            extension,
            width,
            height,
            perceptual_hash,
        )

    def ingest_archive_bytes(self, data: bytes, expected_sha256: str) -> AssetRecord:
        if not data:
            raise AssetError("图片内容为空")
        if len(data) > MAX_SOURCE_BYTES:
            raise AssetError("图片文件超过50MB")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise AssetError("资源文件校验失败")
        media_type, extension, width, height, perceptual_hash = _inspect_archived_image(data)
        return self._store_asset(
            data,
            digest,
            media_type,
            extension,
            width,
            height,
            perceptual_hash,
        )

    def _store_asset(
        self,
        data: bytes,
        digest: str,
        media_type: str,
        extension: str,
        width: int,
        height: int,
        perceptual_hash: str,
    ) -> AssetRecord:
        existing = self._find_by_sha256(digest)
        if existing is not None:
            return existing

        relative_path = Path(digest[:2]) / (digest + extension)
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination, data)
        record = AssetRecord(
            id=str(uuid4()),
            sha256=digest,
            perceptual_hash=perceptual_hash,
            media_type=media_type,
            relative_path=relative_path.as_posix(),
            width=width,
            height=height,
            byte_size=len(data),
        )
        try:
            with self.database.transaction(immediate=True) as connection:
                row = connection.execute("SELECT * FROM assets WHERE sha256 = ?", (digest,)).fetchone()
                if row is not None:
                    return _asset_from_row(row)
                connection.execute(
                    """
                    INSERT INTO assets(
                        id, sha256, perceptual_hash, media_type, relative_path,
                        width, height, byte_size, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.sha256,
                        record.perceptual_hash,
                        record.media_type,
                        record.relative_path,
                        record.width,
                        record.height,
                        record.byte_size,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except Exception:
            if destination.exists() and self._find_by_sha256(digest) is None:
                destination.unlink()
            raise
        return record

    def find_visually_similar(self, perceptual_hash: str, max_distance: int = 5) -> list[AssetRecord]:
        if len(perceptual_hash) != 16:
            raise ValueError("perceptual hash must contain 16 hexadecimal characters")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assets WHERE perceptual_hash IS NOT NULL"
            ).fetchall()
        matches = [
            (_hash_distance(perceptual_hash, row["perceptual_hash"]), _asset_from_row(row))
            for row in rows
        ]
        return [record for distance, record in sorted(matches, key=lambda item: item[0]) if distance <= max_distance]

    def absolute_path(self, record: AssetRecord) -> Path:
        path = (self.root / Path(record.relative_path)).resolve()
        if self.root != path and self.root not in path.parents:
            raise AssetError("图片路径超出资源目录")
        return path

    def get(self, asset_id: str) -> AssetRecord:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return _asset_from_row(row)

    def _find_by_sha256(self, digest: str) -> AssetRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM assets WHERE sha256 = ?", (digest,)).fetchone()
        return _asset_from_row(row) if row is not None else None


def _normalize_image(data: bytes) -> tuple[bytes, str, str, int, int, str]:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            width, height = source.size
            if width <= 0 or height <= 0:
                raise AssetError("图片尺寸无效")
            perceptual_hash = _difference_hash(source)
            if source.format == "GIF" and getattr(source, "is_animated", False):
                return data, "image/gif", ".gif", width, height, perceptual_hash

            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
            width, height = image.size
            output = io.BytesIO()
            if _has_alpha(image):
                image.convert("RGBA").save(output, format="PNG", optimize=True)
                return output.getvalue(), "image/png", ".png", width, height, perceptual_hash
            image.convert("RGB").save(
                output,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return output.getvalue(), "image/jpeg", ".jpg", width, height, perceptual_hash
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise AssetError("无法识别或读取图片") from exc


def _inspect_archived_image(data: bytes) -> tuple[str, str, int, int, str]:
    formats = {
        "GIF": ("image/gif", ".gif"),
        "JPEG": ("image/jpeg", ".jpg"),
        "PNG": ("image/png", ".png"),
    }
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            width, height = source.size
            if width <= 0 or height <= 0 or source.format not in formats:
                raise AssetError("图片格式或尺寸无效")
            media_type, extension = formats[source.format]
            return media_type, extension, width, height, _difference_hash(source)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise AssetError("无法识别或读取图片") from exc


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )


def _difference_hash(image: Image.Image) -> str:
    sample = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = sample.tobytes()
    bits = []
    for row in range(8):
        offset = row * 9
        bits.extend(pixels[offset + column] > pixels[offset + column + 1] for column in range(8))
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _atomic_write(path: Path, data: bytes) -> None:
    file_descriptor, temporary_name = tempfile.mkstemp(prefix="asset-", dir=path.parent)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def _asset_from_row(row) -> AssetRecord:
    return AssetRecord(
        id=row["id"],
        sha256=row["sha256"],
        perceptual_hash=row["perceptual_hash"] or "",
        media_type=row["media_type"],
        relative_path=row["relative_path"],
        width=row["width"] or 0,
        height=row["height"] or 0,
        byte_size=row["byte_size"],
    )
