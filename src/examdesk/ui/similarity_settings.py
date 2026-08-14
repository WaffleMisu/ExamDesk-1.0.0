from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QWidget

from examdesk.scoring import SimilarityLevel

SIMILARITY_OPTIONS = (
    ("严格（90%）", SimilarityLevel.STRICT),
    ("标准（85%）", SimilarityLevel.STANDARD),
    ("宽松（80%）", SimilarityLevel.LOOSE),
    ("自定义", SimilarityLevel.CUSTOM),
)


class SimilaritySettingsControl(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.level_combo = QComboBox()
        for label, level in SIMILARITY_OPTIONS:
            self.level_combo.addItem(label, level.value)
        self.level_combo.setCurrentIndex(1)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(50.0, 100.0)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setSingleStep(1.0)
        self.threshold_spin.setValue(85.0)
        self.threshold_spin.setSuffix(" %")
        self.threshold_spin.setEnabled(False)
        self.level_combo.currentIndexChanged.connect(self._level_changed)
        layout.addWidget(self.level_combo)
        layout.addWidget(self.threshold_spin)
        layout.addStretch(1)

    @property
    def level(self) -> SimilarityLevel:
        return SimilarityLevel(str(self.level_combo.currentData()))

    @property
    def custom_threshold(self) -> float | None:
        return self.threshold_spin.value() if self.level is SimilarityLevel.CUSTOM else None

    def _level_changed(self) -> None:
        self.threshold_spin.setEnabled(self.level is SimilarityLevel.CUSTOM)


def similarity_label(level: SimilarityLevel, custom_threshold: float | None) -> str:
    if level is SimilarityLevel.CUSTOM:
        return f"自定义 {custom_threshold:.1f}%"
    return {
        SimilarityLevel.STRICT: "严格 90%",
        SimilarityLevel.STANDARD: "标准 85%",
        SimilarityLevel.LOOSE: "宽松 80%",
    }[level]
