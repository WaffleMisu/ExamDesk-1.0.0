import pytest
from PySide6.QtWidgets import QApplication

from examdesk.scoring import SimilarityLevel
from examdesk.ui.similarity_settings import SimilaritySettingsControl


@pytest.fixture(scope="module")
def qt_application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    yield application


def test_similarity_settings_control_defaults_to_standard_and_enables_custom(
    qt_application: QApplication,
) -> None:
    control = SimilaritySettingsControl()

    assert control.level is SimilarityLevel.STANDARD
    assert control.custom_threshold is None
    assert not control.threshold_spin.isEnabled()

    control.level_combo.setCurrentIndex(3)
    control.threshold_spin.setValue(66.0)
    qt_application.processEvents()

    assert control.level is SimilarityLevel.CUSTOM
    assert control.custom_threshold == 66.0
    assert control.threshold_spin.isEnabled()
