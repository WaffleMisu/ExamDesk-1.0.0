from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from examdesk.paths import AppPaths
from examdesk.ui import ApplicationContext, MainWindow


def main(destination: Path) -> None:
    application = QApplication.instance() or QApplication(sys.argv)
    destination.mkdir(parents=True, exist_ok=True)
    for name, admin_enabled in (("admin", True), ("candidate", False)):
        context = ApplicationContext.create(
            AppPaths.from_root(destination / f"{name}-data")
        )
        window = MainWindow(context, admin_enabled=admin_enabled)
        window.resize(1280, 780)
        window.show()
        application.processEvents()
        if not window.grab().save(str(destination / f"{name}-home.png")):
            raise RuntimeError(f"unable to save {name} home screenshot")
        window.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
