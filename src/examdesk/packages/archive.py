from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass

MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PackageArchive:
    manifest: dict
    assets: dict[str, bytes]


def build_archive(manifest: dict, assets: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        for digest, data in sorted(assets.items()):
            if hashlib.sha256(data).hexdigest() != digest:
                raise ArchiveError("asset hash does not match content")
            archive.writestr(f"assets/{digest}", data)
    return output.getvalue()


def read_archive(data: bytes) -> PackageArchive:
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise ArchiveError("archive contains too many files")
            total_size = sum(entry.file_size for entry in entries)
            if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ArchiveError("archive expands beyond the size limit")
            names = {entry.filename for entry in entries}
            if "manifest.json" not in names:
                raise ArchiveError("archive manifest is missing")
            if any(_unsafe_name(name) for name in names):
                raise ArchiveError("archive contains an unsafe path")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ArchiveError("archive manifest must be an object")
            assets = {}
            for name in names:
                if not name.startswith("assets/"):
                    continue
                digest = name.removeprefix("assets/")
                if len(digest) != 64:
                    raise ArchiveError("archive asset name is invalid")
                content = archive.read(name)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise ArchiveError("archive asset hash does not match")
                assets[digest] = content
            return PackageArchive(manifest, assets)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ArchiveError("package archive is damaged") from exc


def _unsafe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return normalized.startswith("/") or "../" in normalized or normalized.endswith("/..")

