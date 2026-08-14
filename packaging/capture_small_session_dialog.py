from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from examdesk.domain.enums import QuestionType
from examdesk.ui.session_management import CreateSessionDialog


class AvailabilityService:
    def available_question_counts(self, _session_filter):
        return {
            QuestionType.SINGLE: 36,
            QuestionType.MULTIPLE: 12,
            QuestionType.JUDGE: 20,
            QuestionType.FILL: 6,
        }


def main(destination: Path) -> None:
    application = QApplication.instance() or QApplication(sys.argv)
    dialog = CreateSessionDialog(AvailabilityService(), "admin")
    dialog.resize(640, 480)
    dialog.show()
    application.processEvents()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not dialog.grab().save(str(destination)):
        raise RuntimeError("unable to save small-screen dialog screenshot")
    dialog.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
