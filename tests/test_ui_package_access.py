from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from examdesk.packages import PasswordPackageCodec, SigningKeyPair
from examdesk.results import SubmittedReviewStore
from examdesk.ui.exam_access import ExamAccessDialog, MonitoringConsentDialog
from examdesk.ui.practice_access import PracticeAccessDialog


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def _package(path: Path, *, kind: str, password: str) -> tuple[Path, SigningKeyPair]:
    signer = SigningKeyPair.generate()
    path.write_bytes(
        PasswordPackageCodec.encode(
            b"test payload",
            package_kind=kind,
            password=password,
            signer=signer,
            minimum_software_version="2.2.2",
        )
    )
    return path, signer


def test_open_exam_package_hides_password_for_open_mode(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    path, signer = _package(tmp_path / "open.exampack", kind="exam", password="")
    dialog = ExamAccessDialog(
        path,
        {signer.id: signer.public_key},
        SubmittedReviewStore(tmp_path / "reviews"),
    )

    assert dialog.password_required is False
    assert dialog.password_edit.isHidden()
    assert any(button.text() == "进入考试" for button in dialog.findChildren(QPushButton))


def test_open_exam_package_keeps_password_for_protected_mode(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    path, signer = _package(tmp_path / "protected.exampack", kind="exam", password="600000")
    dialog = ExamAccessDialog(
        path,
        {signer.id: signer.public_key},
        SubmittedReviewStore(tmp_path / "reviews"),
    )

    assert dialog.password_required is True
    assert not dialog.password_edit.isHidden()


def test_open_practice_package_hides_password_for_open_mode(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    path, signer = _package(tmp_path / "open.practicepack", kind="practice", password="")
    dialog = PracticeAccessDialog(path, {signer.id: signer.public_key})

    assert dialog.password_required is False
    assert dialog.password_edit.isHidden()


def test_monitoring_consent_discloses_collected_fields(
    qt_application: QApplication,
) -> None:
    dialog = MonitoringConsentDialog("公开测试")
    text = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert "软件名称和进程名称" in text
    assert "窗口标题" in text
    assert "切出和返回时间及持续时长" in text
    assert dialog.result() == QDialog.DialogCode.Rejected

    consent = dialog.findChild(QPushButton, "monitoringConsentButton")
    consent.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
