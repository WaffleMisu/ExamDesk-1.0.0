import io
from decimal import Decimal
from pathlib import Path

from PIL import Image

from examdesk.db import Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.maintenance import BackupService
from examdesk.questions import AssetManager, QuestionRepository
from examdesk.security import OrganizationKeyStore


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (10, 100, 180)).save(output, format="PNG")
    return output.getvalue()


def test_full_backup_and_restore_preserves_database_and_assets(tmp_path: Path) -> None:
    database_path = tmp_path / "source" / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    asset_root = tmp_path / "source" / "assets"
    asset_manager = AssetManager(database, asset_root)
    asset = asset_manager.ingest_bytes(image_bytes(), "题图.png")
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="备份测试题",
        basis="依据",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        question_asset_ids=[asset.id],
        score=Decimal("1"),
    )
    QuestionRepository(database).create(question, actor_id="admin")
    key_store = OrganizationKeyStore(database)
    keys = key_store.ensure_initialized()
    package_path = tmp_path / "backups" / "manual.exambackup"
    BackupService(database, asset_root).create(
        package_path,
        password="RECOVERY-CODE",
        key_store=key_store,
        software_version="2.0.0",
    )

    target_database = tmp_path / "restored" / "data.sqlite3"
    target_assets = tmp_path / "restored" / "assets"
    restored = BackupService.restore(
        package_path,
        password="RECOVERY-CODE",
        trusted_signers={keys.signing.id: keys.signing.public_key},
        target_database=target_database,
        target_asset_root=target_assets,
    )

    loaded = QuestionRepository(Database(target_database)).get(question.id)
    assert loaded.stem == "备份测试题"
    assert restored.asset_count == 1
    with Database(target_database).connect() as connection:
        relative_path = connection.execute("SELECT relative_path FROM assets").fetchone()[0]
    assert (target_assets / relative_path).is_file()
    restored_keys = OrganizationKeyStore(Database(target_database)).load()
    assert restored_keys.signing.id == keys.signing.id
    assert restored_keys.result_recipient.id == keys.result_recipient.id


def test_full_backup_requires_a_password(tmp_path: Path) -> None:
    database_path = tmp_path / "source" / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    key_store = OrganizationKeyStore(database)
    key_store.ensure_initialized()

    try:
        BackupService(database, tmp_path / "assets").create(
            tmp_path / "open.exambackup",
            password="",
            key_store=key_store,
            software_version="1.0.0",
        )
    except ValueError as exc:
        assert "至少需要8个字符" in str(exc)
    else:
        raise AssertionError("backup creation accepted an empty password")
