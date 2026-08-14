from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.questions import AssetManager, BankCollaborationService, QuestionRepository
from examdesk.security import OrganizationKeyStore
from examdesk.security.passwords import hash_secret
from examdesk.version import __version__


def service(root: Path):
    database_path = root / "data.sqlite3"
    initialize_database(database_path)
    database = Database(database_path)
    questions = QuestionRepository(database)
    admins = AdminRepository(database)
    collaboration = BankCollaborationService(
        database,
        questions,
        AssetManager(database, root / "assets"),
        admins,
    )
    return database, questions, admins, collaboration


def run(root: Path) -> None:
    master_db, master_questions, master_admins, master = service(root / "master")
    supervisor = master_admins.create_first_admin(
        "主管理员",
        "supervisor-password",
        hash_secret("RECOVERY").encode(),
    )
    worker = master_admins.add_admin(supervisor.id, "协作管理员", "unused-secret")
    question = QuestionDraft(
        question_type=QuestionType.SINGLE,
        stem="协作流程验收题",
        basis="原依据",
        display_number="SMOKE-001",
        status=QuestionStatus.ENABLED,
        options=[QuestionOption("A", "甲"), QuestionOption("B", "乙")],
        correct_option_keys={"A"},
        score=Decimal("1"),
    )
    master_questions.create(question, supervisor.id)
    keys = OrganizationKeyStore(master_db).ensure_initialized()
    work_package = master.issue_work_package(
        admin_id=worker.id,
        package_password="package-password",
        signer=keys.signing,
        master_recipient=keys.result_recipient,
        minimum_software_version=__version__,
    )

    worker_db, worker_questions, worker_admins, worker_service = service(root / "worker")
    worker_keys = OrganizationKeyStore(worker_db)
    worker_keys.import_trust_certificate(
        OrganizationKeyStore(master_db).export_trust_certificate(),
        "smoke.examtrust",
    )
    installed = worker_service.install_work_package(
        work_package,
        package_password="package-password",
        local_login_password="local-login-password",
        trusted_signers=worker_keys.trusted_public_keys(),
    )
    assert worker_admins.authenticate("协作管理员", "local-login-password") is not None
    changed = worker_questions.get(question.id)
    changed.basis = "协作管理员修改后的依据"
    worker_questions.update(changed, worker.id, expected_version=1)
    patch = worker_service.export_patch()
    result = master.import_patch(
        patch,
        master_recipient=keys.result_recipient,
        imported_by=supervisor.id,
        source_path="smoke.bankpatch",
    )
    assert result.applied == [question.id]
    assert master_questions.get(question.id).basis == "协作管理员修改后的依据"
    print(
        f"OK version={__version__} admin={installed.admin_name} "
        f"questions={installed.question_count} applied={len(result.applied)}"
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve())
