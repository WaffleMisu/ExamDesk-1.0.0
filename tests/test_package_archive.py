import hashlib
import io
import zipfile

import pytest

from examdesk.packages import ArchiveError, build_archive, read_archive


def test_package_archive_round_trip_and_asset_hash_validation() -> None:
    asset = b"image-bytes"
    digest = hashlib.sha256(asset).hexdigest()
    encoded = build_archive({"kind": "bank_work"}, {digest: asset})
    decoded = read_archive(encoded)

    assert decoded.manifest == {"kind": "bank_work"}
    assert decoded.assets[digest] == asset


def test_package_archive_rejects_unsafe_path() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("../escape", "bad")

    with pytest.raises(ArchiveError, match="unsafe"):
        read_archive(output.getvalue())

