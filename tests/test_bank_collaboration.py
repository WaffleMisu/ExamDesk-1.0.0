import io
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from examdesk.db import Database, initialize_database
from examdesk.db.admin_repository import AdminRepository
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.packages import SigningKeyPair, X25519KeyPair
from examdesk.packages.codec import PackageError
from examdesk.questions import (
    AssetManager,
    BankCollaborationService,
    QuestionRepository,
)
from examdesk.security.passwords import hash_secret


def make_service(root: Path):
    database_path = root / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    questions = QuestionRepository(database)
    assets = AssetManager(database, root / "assets")
    admins = AdminRepository(database)
    return BankCollaborationService(database, questions, assets, admins), questions, admins


def make_question() -> QuestionDraft:
    return QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="临时用地复垦后如何认定？",
        basis="原依据",
        display_number="001",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"B"},
        score=Decimal("1"),
    )


def make_image_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (640, 360), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def prepare_master(tmp_path: Path):
    service, questions, admins = make_service(tmp_path / "master")
    supervisor = admins.create_first_admin(
        "主管理员",
        "supervisor-pass",
        hash_secret("RECOVERY").encode(),
    )
    ordinary = admins.add_admin(supervisor.id, "录题员", "worker-pass")
    question = make_question()
    questions.create(question, supervisor.id)
    return service, questions, supervisor, ordinary, question


def test_work_package_and_patch_round_trip(tmp_path: Path) -> None:
    master, master_questions, supervisor, ordinary, question = prepare_master(tmp_path)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()
    work_package = master.issue_work_package(
        admin_id=ordinary.id,
        package_password="worker-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="2.0.0",
    )

    worker, worker_questions, worker_admins = make_service(tmp_path / "worker")
    installed = worker.install_work_package(
        work_package,
        package_password="worker-pass",
        local_login_password="local-worker-pass",
        trusted_signers={signer.id: signer.public_key},
    )
    worker_question = worker_questions.get(question.id)
    worker_question.basis = "普通管理员补充后的依据"
    worker_questions.update(worker_question, ordinary.id, expected_version=1)
    patch = worker.export_patch()

    imported = master.import_patch(
        patch,
        master_recipient=recipient,
        imported_by=supervisor.id,
        source_path="录题员.bankpatch",
    )
    replayed = master.import_patch(
        patch,
        master_recipient=recipient,
        imported_by=supervisor.id,
    )

    assert installed.question_count == 1
    assert installed.admin_name == "录题员"
    assert worker.installed_work_package() == installed
    assert worker_admins.authenticate("录题员", "worker-pass") is None
    assert worker_admins.authenticate("录题员", "local-worker-pass") is not None
    assert imported.applied == [question.id]
    assert master_questions.get(question.id).basis == "普通管理员补充后的依据"
    assert replayed.replayed


def test_work_package_preserves_question_and_option_images(tmp_path: Path) -> None:
    master, master_questions, supervisor, ordinary, question = prepare_master(tmp_path)
    question_image = master.assets.ingest_bytes(make_image_bytes((190, 45, 55)), "题图.png")
    option_image = master.assets.ingest_bytes(make_image_bytes((45, 95, 190)), "A图.png")
    draft = master_questions.get(question.id)
    draft.question_asset_ids = [question_image.id]
    draft.options = [
        QuestionOption("A", "甲", (option_image.id,)),
        QuestionOption("B", "乙"),
    ]
    master_questions.update(draft, supervisor.id, expected_version=1)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()

    work_package = master.issue_work_package(
        admin_id=ordinary.id,
        package_password="worker-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="2.2.6",
    )
    worker, worker_questions, _ = make_service(tmp_path / "worker-images")
    worker.install_work_package(
        work_package,
        package_password="worker-pass",
        local_login_password="local-worker-pass",
        trusted_signers={signer.id: signer.public_key},
    )

    imported = worker_questions.get(question.id)
    imported_question_image = worker.assets.get(imported.question_asset_ids[0])
    imported_option_image = worker.assets.get(imported.options[0].asset_ids[0])
    assert imported_question_image.sha256 == question_image.sha256
    assert imported_option_image.sha256 == option_image.sha256
    assert worker.assets.absolute_path(imported_question_image).read_bytes() == master.assets.absolute_path(
        question_image
    ).read_bytes()
    assert worker.assets.absolute_path(imported_option_image).read_bytes() == master.assets.absolute_path(
        option_image
    ).read_bytes()


def test_patch_reports_conflict_when_master_changed_after_work_was_issued(tmp_path: Path) -> None:
    master, master_questions, supervisor, ordinary, question = prepare_master(tmp_path)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()
    work_package = master.issue_work_package(
        admin_id=ordinary.id,
        package_password="worker-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="2.0.0",
    )
    worker, worker_questions, _ = make_service(tmp_path / "worker-conflict")
    worker.install_work_package(
        work_package,
        package_password="worker-pass",
        local_login_password="local-worker-pass",
        trusted_signers={signer.id: signer.public_key},
    )

    master_edit = master_questions.get(question.id)
    master_edit.basis = "主管理员先修改"
    master_questions.update(master_edit, supervisor.id, expected_version=1)
    worker_edit = worker_questions.get(question.id)
    worker_edit.basis = "普通管理员也修改"
    worker_questions.update(worker_edit, ordinary.id, expected_version=1)

    imported = master.import_patch(
        worker.export_patch(),
        master_recipient=recipient,
        imported_by=supervisor.id,
    )

    assert imported.applied == []
    assert imported.conflicts[0].reason == "主题库题目已被其他人修改"
    assert master_questions.get(question.id).basis == "主管理员先修改"


def test_reissuing_work_package_revokes_old_patch(tmp_path: Path) -> None:
    master, _, supervisor, ordinary, question = prepare_master(tmp_path)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()
    old_package = master.issue_work_package(
        admin_id=ordinary.id,
        package_password="first-package-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="2.2.6",
    )
    worker, worker_questions, _ = make_service(tmp_path / "worker-revoked")
    worker.install_work_package(
        old_package,
        package_password="first-package-pass",
        local_login_password="local-worker-pass",
        trusted_signers={signer.id: signer.public_key},
    )
    changed = worker_questions.get(question.id)
    changed.basis = "旧授权修改"
    worker_questions.update(changed, ordinary.id, expected_version=1)
    old_patch = worker.export_patch()

    master.issue_work_package(
        admin_id=ordinary.id,
        package_password="second-package-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="2.2.6",
    )

    with pytest.raises(PackageError, match="invalid or has been revoked"):
        master.import_patch(
            old_patch,
            master_recipient=recipient,
            imported_by=supervisor.id,
        )


def test_patch_reports_conflict_for_new_question_with_same_surface_but_different_answer(
    tmp_path: Path,
) -> None:
    master, _master_questions, supervisor, ordinary, question = prepare_master(tmp_path)
    signer = SigningKeyPair.generate()
    recipient = X25519KeyPair.generate()
    work_package = master.issue_work_package(
        admin_id=ordinary.id,
        package_password="worker-pass",
        signer=signer,
        master_recipient=recipient,
        minimum_software_version="1.0.0",
    )
    worker, worker_questions, _ = make_service(tmp_path / "worker-answer-conflict")
    worker.install_work_package(
        work_package,
        package_password="worker-pass",
        local_login_password="local-worker-pass",
        trusted_signers={signer.id: signer.public_key},
    )
    conflicting = make_question()
    conflicting.correct_option_keys = {"A"}
    worker_questions.create(conflicting, ordinary.id)

    imported = master.import_patch(
        worker.export_patch(),
        master_recipient=recipient,
        imported_by=supervisor.id,
    )

    assert imported.applied == []
    assert imported.conflicts[0].question_id == conflicting.id
    assert "答案或分值" in imported.conflicts[0].reason
    assert question.id != conflicting.id
