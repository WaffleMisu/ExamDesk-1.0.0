from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from examdesk.domain.enums import QuestionStatus, QuestionType
from examdesk.domain.models import QuestionDraft, QuestionOption
from examdesk.paths import AppPaths
from examdesk.questions import QuestionRepository
from examdesk.security.passwords import hash_secret
from examdesk.ui.admin_workspace import AdminWorkspace
from examdesk.ui.application import ApplicationContext, MainWindow
from examdesk.ui.theme import ThemeManager
from examdesk.ui.theme_dialog import ThemeDialog
from examdesk.ui.theme_settings import ThemeSettings, ThemeSettingsStore


def capture_acceptance(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    background = output_directory / "主题背景测试图.jpg"
    _make_background(background)
    _capture_home(application, output_directory, "clean_blue", "主题_清爽蓝.png")
    _capture_home(application, output_directory, "minimal_light", "主题_极简浅色.png")
    _capture_home(application, output_directory, "graphite_dark", "主题_石墨深色.png")
    _capture_home(
        application,
        output_directory,
        "clean_blue",
        "主题_自定义背景.png",
        background,
    )
    _capture_theme_dialog(application, output_directory)
    _capture_question_bank(application, output_directory)
    _capture_system_maintenance(application, output_directory)


def _capture_home(
    application: QApplication,
    output_directory: Path,
    theme_id: str,
    filename: str,
    background: Path | None = None,
) -> None:
    root = output_directory / "_qa_data" / filename.removesuffix(".png")
    context = ApplicationContext.create(AppPaths.from_root(root))
    store = ThemeSettingsStore(context.paths.app)
    settings = ThemeSettings(theme_id=theme_id)
    if background is not None:
        installed = store.install_background(background)
        settings = ThemeSettings(
            theme_id=theme_id,
            background_scope="home",
            background_file=installed,
        )
    store.save(settings)
    manager = ThemeManager(application, store)
    window = MainWindow(context, manager)
    window.resize(1280, 780)
    window.show()
    application.processEvents()
    _save_grab(window, output_directory / filename)
    window.close()
    application.processEvents()


def _capture_theme_dialog(application: QApplication, output_directory: Path) -> None:
    context = ApplicationContext.create(
        AppPaths.from_root(output_directory / "_qa_data" / "theme_dialog")
    )
    manager = ThemeManager(application, ThemeSettingsStore(context.paths.app))
    window = MainWindow(context, manager)
    dialog = ThemeDialog(manager, window)
    dialog.show()
    application.processEvents()
    _save_grab(dialog, output_directory / "外观设置.png")
    dialog.reject()
    window.close()
    application.processEvents()


def _capture_question_bank(application: QApplication, output_directory: Path) -> None:
    context = ApplicationContext.create(
        AppPaths.from_root(output_directory / "_qa_data" / "question_bank")
    )
    administrators = context.administrators.list_all()
    if administrators:
        administrator = administrators[0]
    else:
        administrator = context.administrators.create_first_admin(
            "验收管理员",
            "acceptance-pass",
            hash_secret("ACCEPTANCE-RECOVERY").encode(),
        )
    context.organization_keys.ensure_initialized()
    repository = QuestionRepository(context.database)
    if not repository.list_current():
        repository.create(
            QuestionDraft(
                question_type=QuestionType.SINGLE,
                stem="临时用地复垦后应如何认定？",
                basis="培训手册第三条",
                display_number="001",
                chapter="安全规范",
                status=QuestionStatus.ENABLED,
                options=[
                    QuestionOption("A", "直接变更"),
                    QuestionOption("B", "结合现状认定"),
                    QuestionOption("C", "保持原分类"),
                    QuestionOption("D", "删除记录"),
                ],
                correct_option_keys={"B"},
                score=Decimal("1"),
            ),
            actor_id=administrator.id,
        )
        for index in range(2, 13):
            repository.create(
                QuestionDraft(
                    question_type=QuestionType.SINGLE if index % 2 else QuestionType.JUDGE,
                    stem=f"年度安全知识培训模拟题目 {index}，用于检查题库筛选和批量管理布局。",
                    basis="2026年度培训手册",
                    display_number=f"{index:03d}",
                    applicable_year=2026,
                    source="年度培训手册",
                    chapter="安全规范" if index % 2 else "案例分析",
                    clause=f"第{index}条",
                    difficulty="一般" if index % 3 else "较难",
                    tags=["安全培训", "验收"],
                    status=QuestionStatus.ENABLED,
                    options=[QuestionOption("A", "正确"), QuestionOption("B", "错误")],
                    correct_option_keys={"A"},
                    score=Decimal("1"),
                ),
                actor_id=administrator.id,
            )
    manager = ThemeManager(application, ThemeSettingsStore(context.paths.app))
    window = MainWindow(context, manager)
    workspace = AdminWorkspace(
        context.database,
        administrator,
        context.paths.assets,
        context.organization_keys,
    )
    workspace.home_requested.connect(window.show_home)
    window.pages.addWidget(workspace)
    window.pages.setCurrentWidget(workspace)
    window.background_surface.set_home_active(False)
    workspace.pages.setCurrentIndex(1)
    workspace.nav_group.button(1).setChecked(True)
    window.resize(1380, 820)
    window.show()
    application.processEvents()
    _save_grab(window, output_directory / "题库页_筛选与批量管理.png")
    window.close()
    application.processEvents()


def _capture_system_maintenance(application: QApplication, output_directory: Path) -> None:
    context = ApplicationContext.create(
        AppPaths.from_root(output_directory / "_qa_data" / "system_maintenance")
    )
    administrators = context.administrators.list_all()
    administrator = administrators[0] if administrators else context.administrators.create_first_admin(
        "验收管理员",
        "acceptance-pass",
        hash_secret("ACCEPTANCE-RECOVERY").encode(),
    )
    context.organization_keys.ensure_initialized()
    manager = ThemeManager(application, ThemeSettingsStore(context.paths.app))
    window = MainWindow(context, manager)
    workspace = AdminWorkspace(
        context.database,
        administrator,
        context.paths.assets,
        context.organization_keys,
    )
    window.pages.addWidget(workspace)
    window.pages.setCurrentWidget(workspace)
    window.background_surface.set_home_active(False)
    workspace.pages.setCurrentIndex(4)
    workspace.nav_group.button(4).setChecked(True)
    window.resize(1380, 820)
    window.show()
    application.processEvents()
    _save_grab(window, output_directory / "系统维护_数据治理.png")
    window.close()
    application.processEvents()


def _save_grab(widget, destination: Path) -> None:
    if not widget.grab().save(str(destination)):
        raise OSError(f"无法保存截图：{destination}")


def _make_background(path: Path) -> None:
    image = Image.new("RGB", (1600, 900), (220, 225, 228))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 520, 900), fill=(125, 32, 50))
    draw.rectangle((520, 0, 1110, 430), fill=(42, 105, 126))
    draw.rectangle((520, 430, 1110, 900), fill=(212, 173, 76))
    draw.rectangle((1110, 0, 1600, 900), fill=(68, 104, 80))
    image.save(path, format="JPEG", quality=92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    capture_acceptance(args.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
