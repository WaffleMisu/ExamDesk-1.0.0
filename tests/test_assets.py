import io
from pathlib import Path

from PIL import Image

from examdesk.db import Database, initialize_database
from examdesk.questions import AssetError, AssetManager


def make_image_bytes(color: tuple[int, int, int], size: tuple[int, int] = (3200, 1200)) -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def make_manager(tmp_path: Path) -> AssetManager:
    database_path = tmp_path / "assets.sqlite3"
    initialize_database(database_path)
    return AssetManager(Database(database_path), tmp_path / "asset-files")


def test_asset_ingest_compresses_large_image_and_deduplicates(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    source = make_image_bytes((20, 140, 80))

    first = manager.ingest_bytes(source, "截图.png")
    second = manager.ingest_bytes(source, "另一个名字.png")

    assert first == second
    assert max(first.width, first.height) == 2200
    assert first.media_type == "image/jpeg"
    assert manager.absolute_path(first).is_file()


def test_asset_similarity_finds_same_visual_content(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    first = manager.ingest_bytes(make_image_bytes((20, 140, 80), (500, 300)))
    manager.ingest_bytes(make_image_bytes((20, 140, 81), (500, 300)))

    matches = manager.find_visually_similar(first.perceptual_hash, max_distance=1)
    assert first.id in {match.id for match in matches}


def test_asset_rejects_non_image_and_empty_content(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    for value in (b"", b"not-an-image"):
        try:
            manager.ingest_bytes(value)
        except AssetError:
            pass
        else:
            raise AssertionError("expected invalid image error")

