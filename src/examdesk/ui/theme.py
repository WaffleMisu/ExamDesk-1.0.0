from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication

from .theme_settings import ThemeSettings, ThemeSettingsStore


@dataclass(frozen=True, slots=True)
class ThemePalette:
    id: str
    name: str
    ink: str
    muted: str
    line: str
    canvas: str
    paper: str
    field: str
    rail: str
    rail_text: str
    rail_muted: str
    nav_hover: str
    accent: str
    accent_hover: str
    accent_soft: str
    secondary: str
    warning_text: str
    warning_background: str
    warning_border: str
    error: str
    accent_text: str = "#FFFFFF"
    dark: bool = False


THEMES = {
    palette.id: palette
    for palette in (
        ThemePalette(
            "clean_blue", "清爽蓝", "#17212A", "#617383", "#D5E0E8", "#F3F8FC", "#FFFFFF",
            "#FFFFFF", "#2563A6", "#FFFFFF", "#D7EAFE", "#3475B8", "#2563A6", "#1F568F",
            "#E7F2FB", "#54B3E6", "#75520D", "#FFF4DE", "#E8C88D", "#B42318",
        ),
        ThemePalette(
            "classic_green", "经典绿", "#18201D", "#66716C", "#D8DEDA", "#F3F6F4", "#FFFFFF",
            "#FFFFFF", "#19382F", "#FFFFFF", "#BED0C8", "#2A5145", "#2F6B55", "#285C49",
            "#EAF3EE", "#C78A2B", "#7B5318", "#FFF4DE", "#E7C98E", "#B34A3C",
        ),
        ThemePalette(
            "minimal_light", "极简浅色", "#1D1D1F", "#6E6E73", "#D2D2D7", "#F5F5F7", "#FFFFFF",
            "#FFFFFF", "#E8E8ED", "#1D1D1F", "#6E6E73", "#DADAE0", "#0071E3", "#0060C5",
            "#EAF3FF", "#D17A00", "#714600", "#FFF5E5", "#E7C68C", "#C9342F",
        ),
        ThemePalette(
            "coral", "珊瑚红", "#2B2022", "#75676A", "#E2D8DA", "#FAF4F4", "#FFFFFF",
            "#FFFFFF", "#602A31", "#FFFFFF", "#E5BEC4", "#773841", "#D9534F", "#BD403D",
            "#FCEDEC", "#B97920", "#714B13", "#FFF4DF", "#E4C68E", "#B72F39",
        ),
        ThemePalette(
            "graphite_dark", "石墨深色", "#F4F6F8", "#AAB0B6", "#3B4047", "#1B1D20", "#25282C",
            "#30343A", "#111316", "#FFFFFF", "#AAB0B6", "#2C3035", "#6FA8FF", "#5993EA",
            "#29384F", "#E0A84D", "#F0C577", "#3A3022", "#6B5430", "#FF766D", dark=True,
        ),
        ThemePalette(
            "bright_teal", "明亮青绿", "#172322", "#617271", "#D4E0DE", "#F1F8F7", "#FFFFFF",
            "#FFFFFF", "#123C3B", "#FFFFFF", "#B8D7D4", "#1B514F", "#008A83", "#00746F",
            "#E6F5F3", "#BE7A19", "#704B13", "#FFF4DF", "#E4C68D", "#B53A36",
        ),
    )
}

CURRENT_PALETTE = THEMES["clean_blue"]


@dataclass(frozen=True, slots=True)
class ThemeState:
    settings: ThemeSettings
    palette: ThemePalette
    background_path: Path | None


class ThemeManager(QObject):
    changed = Signal(object)

    def __init__(self, application: QApplication, store: ThemeSettingsStore, parent=None) -> None:
        super().__init__(parent)
        self.application = application
        self.store = store
        self.settings = store.load()
        self.state = self._apply(self.settings)

    def preview(
        self,
        settings: ThemeSettings,
        background_override: Path | None = None,
    ) -> ThemeState:
        return self._apply(settings.normalized(), background_override)

    def commit(
        self,
        settings: ThemeSettings,
        selected_background: Path | None = None,
    ) -> ThemeState:
        if selected_background is not None:
            installed_name = self.store.install_background(selected_background)
            settings = replace(settings, background_file=installed_name)
        normalized = self.store.save(settings)
        self.store.remove_unused_backgrounds(normalized.background_file)
        self.settings = normalized
        return self._apply(normalized)

    def restore(self, settings: ThemeSettings) -> ThemeState:
        return self._apply(settings.normalized())

    def _apply(
        self,
        settings: ThemeSettings,
        background_override: Path | None = None,
    ) -> ThemeState:
        palette = resolve_palette(settings)
        apply_theme(self.application, palette)
        background_path = background_override or self.store.background_path(settings)
        state = ThemeState(settings, palette, background_path)
        self.state = state
        self.changed.emit(state)
        return state


def resolve_palette(settings: ThemeSettings) -> ThemePalette:
    palette = THEMES.get(settings.theme_id, THEMES["clean_blue"])
    if not settings.accent_color:
        return palette
    accent = QColor(settings.accent_color)
    hover = accent.darker(116).name().upper()
    paper = QColor(palette.paper)
    ratio = 0.20 if palette.dark else 0.10
    soft = QColor(
        round(paper.red() * (1 - ratio) + accent.red() * ratio),
        round(paper.green() * (1 - ratio) + accent.green() * ratio),
        round(paper.blue() * (1 - ratio) + accent.blue() * ratio),
    ).name().upper()
    return replace(
        palette,
        accent=settings.accent_color.upper(),
        accent_hover=hover,
        accent_soft=soft,
        accent_text=_accent_text_color(accent),
    )


def _accent_text_color(accent: QColor) -> str:
    brightness = (accent.red() * 299 + accent.green() * 587 + accent.blue() * 114) / 1000
    return "#171717" if brightness >= 165 else "#FFFFFF"


def apply_theme(application: QApplication, palette: ThemePalette | None = None) -> None:
    global CURRENT_PALETTE
    CURRENT_PALETTE = palette or THEMES["clean_blue"]
    application.setStyle("Fusion")
    font = QFont("Microsoft YaHei UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    application.setFont(font)
    application.setStyleSheet(build_stylesheet(CURRENT_PALETTE))


def current_palette() -> ThemePalette:
    return CURRENT_PALETTE


def build_stylesheet(colors: ThemePalette) -> str:
    return f"""
QWidget {{
    color: {colors.ink};
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
}}
QMainWindow {{ background: {colors.canvas}; }}
QWidget#backgroundSurface, QStackedWidget#pageStack, QWidget#appRoot {{ background: transparent; }}
QWidget#brandRail {{ background: {colors.rail}; }}
QLabel#brandName {{ color: {colors.rail_text}; font-size: 25px; font-weight: 700; }}
QLabel#brandVersion, QLabel#railMeta {{ color: {colors.rail_muted}; font-size: 13px; }}
QLabel#pageTitle {{ color: {colors.ink}; font-size: 25px; font-weight: 700; }}
QLabel#pageMeta, QLabel#entryMeta, QLabel#formHint {{ color: {colors.muted}; font-size: 13px; }}
QFrame#entryTile, QFrame#summaryPanel {{
    background: {colors.paper}; border: 1px solid {colors.line}; border-radius: 7px;
}}
QFrame#practiceFeedback {{
    background: {colors.paper}; border: 1px solid {colors.line}; border-radius: 6px;
}}
QLabel#feedbackCorrect {{ color: #2F8F5B; font-size: 19px; font-weight: 700; }}
QLabel#feedbackPartial {{ color: {colors.secondary}; font-size: 19px; font-weight: 700; }}
QLabel#feedbackWrong {{ color: {colors.error}; font-size: 19px; font-weight: 700; }}
QLabel#reviewOption, QLabel#reviewOptionCorrect, QLabel#reviewOptionWrong {{
    min-height: 34px; padding: 5px 9px; border-radius: 5px; font-size: 15px;
}}
QLabel#reviewOptionCorrect {{
    color: #17472F; background: #E7F6ED; border: 1px solid #2F8F5B;
}}
QLabel#reviewOptionWrong {{
    color: #7A1C18; background: #FCEBEA; border: 1px solid #C7433A;
}}
QFrame#entryAccentAdmin {{ background: {colors.accent}; border-radius: 2px; }}
QFrame#entryAccentExam {{ background: {colors.secondary}; border-radius: 2px; }}
QFrame#entryAccentPractice {{ background: #46728A; border-radius: 2px; }}
QLabel#entryTitle {{ font-size: 19px; font-weight: 700; }}
QPushButton {{
    min-height: 38px; padding: 0 14px; border-radius: 5px;
    border: 1px solid {colors.line}; background: {colors.paper}; color: {colors.ink}; font-weight: 600;
}}
QPushButton:hover {{ border-color: {colors.accent}; background: {colors.accent_soft}; }}
QPushButton:pressed {{ background: {colors.accent_soft}; }}
QPushButton:disabled {{ color: {colors.muted}; background: {colors.canvas}; }}
QPushButton#primaryButton {{
    color: {colors.accent_text}; background: {colors.accent}; border-color: {colors.accent};
}}
QPushButton#primaryButton:hover {{ background: {colors.accent_hover}; border-color: {colors.accent_hover}; }}
QPushButton#dangerButton {{ color: #FFFFFF; background: {colors.error}; border-color: {colors.error}; }}
QPushButton#quietButton {{ border-color: transparent; background: transparent; color: {colors.accent}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    min-height: 38px; padding: 0 10px; border: 1px solid {colors.line}; border-radius: 5px;
    background: {colors.field}; color: {colors.ink}; selection-background-color: {colors.accent};
}}
QPlainTextEdit, QTextEdit {{ padding: 8px 10px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{
    border: 2px solid {colors.accent};
}}
QDialog {{ background: {colors.canvas}; }}
QLabel#dialogTitle {{ font-size: 21px; font-weight: 700; }}
QLabel#errorText {{ color: {colors.error}; font-size: 13px; }}
QFrame#topBar, QFrame#examTopBar {{ background: {colors.paper}; border-bottom: 1px solid {colors.line}; }}
QFrame#questionSurface {{ background: {colors.paper}; border: 1px solid {colors.line}; border-radius: 6px; }}
QLabel#questionStem {{ font-size: 18px; font-weight: 600; }}
QRadioButton#answerOption, QCheckBox#answerOption {{
    min-height: 42px; padding: 4px 8px; spacing: 10px; font-size: 15px;
}}
QRadioButton#answerOption[reviewState="correct"],
QCheckBox#answerOption[reviewState="correct"] {{
    color: #17472F; background: #E7F6ED; border: 1px solid #2F8F5B; border-radius: 5px;
}}
QRadioButton#answerOption[reviewState="wrong"],
QCheckBox#answerOption[reviewState="wrong"] {{
    color: #7A1C18; background: #FCEBEA; border: 1px solid #C7433A; border-radius: 5px;
}}
QWidget#answerCard {{ background: {colors.paper}; border-left: 1px solid {colors.line}; }}
QPushButton#numberButton {{
    min-width: 40px; max-width: 40px; min-height: 36px; max-height: 36px; padding: 0;
}}
QLabel#timerLabel {{ font-size: 20px; font-weight: 700; }}
QLabel#examWarning, QLabel#warningText {{
    color: {colors.warning_text}; background: {colors.warning_background};
    border: 1px solid {colors.warning_border}; border-radius: 5px; padding: 8px 12px;
}}
QWidget#adminNav {{ background: {colors.rail}; }}
QPushButton#navButton {{
    min-height: 42px; padding: 0 14px; text-align: left; color: {colors.rail_muted};
    background: transparent; border: 0; border-radius: 4px; font-weight: 500;
}}
QPushButton#navButton:hover {{ background: {colors.nav_hover}; }}
QPushButton#navButton:checked {{
    color: {colors.accent_text}; background: {colors.accent}; font-weight: 700;
}}
QLabel#metricValue {{ font-size: 28px; font-weight: 700; }}
QLabel#metricLabel {{ color: {colors.muted}; font-size: 13px; }}
QTableWidget, QTableView, QListWidget {{
    background: {colors.paper}; alternate-background-color: {colors.canvas}; color: {colors.ink};
    border: 1px solid {colors.line}; gridline-color: {colors.line}; selection-background-color: {colors.accent_soft};
    selection-color: {colors.ink};
}}
QMenu, QComboBox QAbstractItemView {{
    background: {colors.paper}; color: {colors.ink}; border: 1px solid {colors.line};
    selection-background-color: {colors.accent_soft}; selection-color: {colors.ink};
}}
QMenu::item {{ padding: 7px 24px 7px 12px; }}
QMenu::item:selected {{ background: {colors.accent_soft}; }}
QHeaderView::section {{
    background: {colors.canvas}; color: {colors.ink}; border: 0; border-bottom: 1px solid {colors.line};
    padding: 8px; font-weight: 700;
}}
QTableCornerButton::section {{ background: {colors.canvas}; border: 1px solid {colors.line}; }}
QTabWidget::pane {{ border: 1px solid {colors.line}; background: {colors.paper}; }}
QTabBar::tab {{ background: {colors.canvas}; color: {colors.muted}; padding: 9px 16px; }}
QTabBar::tab:selected {{ background: {colors.paper}; color: {colors.accent}; }}
QToolTip {{ color: {colors.ink}; background: {colors.paper}; border: 1px solid {colors.line}; padding: 5px; }}
"""
