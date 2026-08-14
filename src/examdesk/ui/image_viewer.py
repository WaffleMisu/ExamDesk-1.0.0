from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .window_sizing import fit_window_to_available


class ImageViewerDialog(QDialog):
    def __init__(self, images: list[bytes], initial_index: int = 0, parent=None) -> None:
        super().__init__(parent)
        self.images = images
        self.index = initial_index
        self.zoom = 1.0
        self.pixmaps = [_pixmap(data) for data in images]
        self.setWindowTitle("查看题图")
        fit_window_to_available(self, 1000, 720, minimum_width=560, minimum_height=400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        self.previous_button = QToolButton()
        self.previous_button.setToolTip("上一张")
        self.previous_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowLeft))
        self.previous_button.clicked.connect(self.previous)
        toolbar.addWidget(self.previous_button)
        self.counter = QLabel()
        self.counter.setObjectName("pageMeta")
        toolbar.addWidget(self.counter)
        self.next_button = QToolButton()
        self.next_button.setToolTip("下一张")
        self.next_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        self.next_button.clicked.connect(self.next)
        toolbar.addWidget(self.next_button)
        toolbar.addStretch(1)
        zoom_out = QToolButton()
        zoom_out.setText("-")
        zoom_out.setToolTip("缩小")
        zoom_out.clicked.connect(lambda: self.set_zoom(self.zoom / 1.2))
        toolbar.addWidget(zoom_out)
        zoom_in = QToolButton()
        zoom_in.setText("+")
        zoom_in.setToolTip("放大")
        zoom_in.clicked.connect(lambda: self.set_zoom(self.zoom * 1.2))
        toolbar.addWidget(zoom_in)
        actual = QPushButton("1:1")
        actual.setToolTip("实际大小")
        actual.clicked.connect(lambda: self.set_zoom(1.0))
        toolbar.addWidget(actual)
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, 1)
        self.refresh()

    def previous(self) -> None:
        self.index = max(0, self.index - 1)
        self.zoom = 1.0
        self.refresh()

    def next(self) -> None:
        self.index = min(len(self.pixmaps) - 1, self.index + 1)
        self.zoom = 1.0
        self.refresh()

    def set_zoom(self, value: float) -> None:
        self.zoom = max(0.2, min(5.0, value))
        self.refresh()

    def refresh(self) -> None:
        pixmap = self.pixmaps[self.index]
        width = max(1, int(pixmap.width() * self.zoom))
        height = max(1, int(pixmap.height() * self.zoom))
        self.image_label.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.image_label.resize(width, height)
        self.counter.setText(f"{self.index + 1} / {len(self.pixmaps)}")
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index + 1 < len(self.pixmaps))


class ImageStrip(QWidget):
    def __init__(self, images: list[bytes], parent=None) -> None:
        super().__init__(parent)
        self.images = images
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)
        for index, data in enumerate(images):
            button = ImageThumbnail(index, data)
            button.clicked_index.connect(self.open_image)
            layout.addWidget(button)
        layout.addStretch(1)

    def open_image(self, index: int) -> None:
        ImageViewerDialog(self.images, index, self).exec()


class ImageThumbnail(QToolButton):
    clicked_index = Signal(int)

    def __init__(self, index: int, data: bytes, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.setToolTip("查看题图")
        self.setFixedSize(172, 116)
        pixmap = _pixmap(data).scaled(
            160,
            104,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setIcon(pixmap)
        self.setIconSize(pixmap.size())
        self.clicked.connect(lambda: self.clicked_index.emit(self.index))


def _pixmap(data: bytes) -> QPixmap:
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        raise ValueError("无法显示题目图片")
    return pixmap
