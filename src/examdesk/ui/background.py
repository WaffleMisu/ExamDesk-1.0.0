from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .theme import ThemeManager, ThemeState


class BackgroundSurface(QWidget):
    def __init__(self, theme_manager: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("backgroundSurface")
        self.theme_manager = theme_manager
        self.state = theme_manager.state
        self.home_active = True
        self.pixmap = QPixmap()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.theme_manager.changed.connect(self.set_theme_state)
        self.set_theme_state(self.state)

    def set_content(self, widget: QWidget) -> None:
        self.layout().addWidget(widget)

    def set_home_active(self, active: bool) -> None:
        if self.home_active != active:
            self.home_active = active
            self.update()

    def set_theme_state(self, state: ThemeState) -> None:
        self.state = state
        self.pixmap = QPixmap(str(state.background_path)) if state.background_path else QPixmap()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.state.palette.canvas))
        if not self._should_draw_image() or self.pixmap.isNull():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        source = _cover_source_rect(self.pixmap.width(), self.pixmap.height(), self.width(), self.height())
        painter.drawPixmap(QRectF(self.rect()), self.pixmap, source)
        overlay = QColor(self.state.palette.canvas)
        overlay.setAlpha(175 if self.state.palette.dark else 165)
        painter.fillRect(self.rect(), overlay)

    def _should_draw_image(self) -> bool:
        scope = self.state.settings.background_scope
        return scope == "all" or (scope == "home" and self.home_active)


def _cover_source_rect(
    image_width: int,
    image_height: int,
    target_width: int,
    target_height: int,
) -> QRectF:
    if image_width <= 0 or image_height <= 0 or target_width <= 0 or target_height <= 0:
        return QRectF()
    image_ratio = image_width / image_height
    target_ratio = target_width / target_height
    if image_ratio > target_ratio:
        width = image_height * target_ratio
        return QRectF((image_width - width) / 2, 0, width, image_height)
    height = image_width / target_ratio
    return QRectF(0, (image_height - height) / 2, image_width, height)
