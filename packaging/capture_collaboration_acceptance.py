from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from examdesk.db.admin_repository import AdminRepository
from examdesk.paths import AppPaths
from examdesk.security.passwords import hash_secret
from examdesk.ui import ApplicationContext, MainWindow
from examdesk.ui.admin_workspace import AdminWorkspace


def capture(root: Path) -> None:
    application = QApplication.instance() or QApplication(sys.argv)

    candidate_context = ApplicationContext.create(AppPaths.from_root(root / "candidate-data"))
    candidate = MainWindow(candidate_context, admin_enabled=False)
    candidate.show()
    application.processEvents()
    candidate.grab().save(str(root / "candidate-collaboration-home.png"))
    candidate.close()

    admin_context = ApplicationContext.create(AppPaths.from_root(root / "admin-data"))
    admins = AdminRepository(admin_context.database)
    supervisor = admins.create_first_admin(
        "验收主管理员",
        "acceptance-password",
        hash_secret("ACCEPTANCE-RECOVERY").encode(),
    )
    admins.add_admin(supervisor.id, "协作管理员", "unused-local-secret")
    keys = admin_context.organization_keys.ensure_initialized()
    workspace = AdminWorkspace(
        admin_context.database,
        supervisor,
        admin_context.paths.assets,
        admin_context.organization_keys,
    )
    workspace.resize(1280, 780)
    workspace.show()
    workspace.pages.setCurrentIndex(4)
    application.processEvents()
    workspace.grab().save(str(root / "supervisor-collaboration-management.png"))
    workspace.close()
    assert keys.signing.id


if __name__ == "__main__":
    destination = Path(sys.argv[1]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    capture(destination)
