from __future__ import annotations

import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
)

from examdesk.branding import PRODUCT_NAME
from examdesk.db import AdminRepository, Database, initialize_database
from examdesk.domain.enums import AdminRole
from examdesk.exam import ExamStateStore, ExamStateStoreError
from examdesk.packages import PasswordPackageCodec
from examdesk.paths import AppPaths
from examdesk.practice import PracticePackageReader, PracticeService
from examdesk.questions import AssetManager, BankCollaborationService, QuestionRepository
from examdesk.results import AttemptError, AttemptService, SubmittedReviewStore
from examdesk.security import OrganizationKeyError, OrganizationKeyStore
from examdesk.version import __version__

from .admin_auth import AdminLoginDialog, FirstAdminDialog
from .admin_workspace import AdminWorkspace
from .background import BackgroundSurface
from .collaboration_ui import CollaborationWorkspace, InstallWorkPackageDialog
from .exam_access import ExamAccessDialog, MonitoringConsentDialog
from .exam_result import ExamResultPage
from .exam_runner import ExamRunnerPage
from .home import HomePage
from .practice_access import PracticeAccessDialog, PracticeSetupDialog
from .practice_runner import PracticeResultPage, PracticeRunnerPage
from .system_maintenance import BackupRestoreDialog
from .theme import ThemeManager
from .theme_dialog import ThemeDialog
from .theme_settings import ThemeSettingsStore
from .window_sizing import fit_window_to_available


@dataclass(frozen=True, slots=True)
class ApplicationContext:
    paths: AppPaths
    database: Database
    administrators: AdminRepository
    organization_keys: OrganizationKeyStore

    @classmethod
    def create(cls, paths: AppPaths) -> ApplicationContext:
        paths.ensure()
        initialize_database(paths.database)
        database = Database(paths.database)
        return cls(paths, database, AdminRepository(database), OrganizationKeyStore(database))


class MainWindow(QMainWindow):
    def __init__(
        self,
        context: ApplicationContext,
        theme_manager: ThemeManager | None = None,
        admin_enabled: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.admin_enabled = admin_enabled
        self.edition_name = "主管理员版" if admin_enabled else "考生协作版"
        application = QApplication.instance()
        if application is None:
            raise RuntimeError("QApplication must exist before MainWindow")
        self.theme_manager = theme_manager or ThemeManager(
            application,
            ThemeSettingsStore(context.paths.app),
            self,
        )
        self.setWindowTitle(f"{PRODUCT_NAME} {__version__} · {self.edition_name}")
        fit_window_to_available(
            self,
            1280,
            780,
            minimum_width=800,
            minimum_height=520,
            margin=12,
        )

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.background_surface = BackgroundSurface(self.theme_manager)
        self.background_surface.set_content(self.pages)
        self.setCentralWidget(self.background_surface)
        self.home_page = HomePage(admin_enabled=admin_enabled, edition_name=self.edition_name)
        if admin_enabled:
            self.home_page.admin_requested.connect(self.open_admin)
        else:
            self.home_page.collaboration_requested.connect(self.open_collaboration)
            self.home_page.trust_requested.connect(self.import_trust_certificates)
        self.home_page.exam_requested.connect(self.choose_exam_package)
        self.home_page.practice_requested.connect(self.choose_practice_package)
        self.home_page.theme_requested.connect(self.open_theme_settings)
        self.pages.addWidget(self.home_page)
        self._refresh_collaboration_entry()
        self._refresh_trust_entry()

    def open_theme_settings(self) -> None:
        ThemeDialog(self.theme_manager, self).exec()

    def open_admin(self) -> None:
        if not self.admin_enabled:
            return
        if self.context.administrators.list_all():
            dialog = AdminLoginDialog(
                self.context.administrators,
                self,
                required_role=AdminRole.SUPERVISOR,
            )
        else:
            dialog = FirstAdminDialog(self.context.administrators, self)
        result = dialog.exec()
        if isinstance(dialog, FirstAdminDialog) and dialog.restore_requested:
            restore = BackupRestoreDialog(
                self.context.database,
                self.context.paths.assets,
                self.context.organization_keys,
                parent=self,
            )
            if restore.exec() == QDialog.DialogCode.Accepted and restore.restored is not None:
                QMessageBox.information(
                    self,
                    "恢复完成",
                    "备份已经恢复。软件将退出，请重新打开后登录恢复的主管理员账户。",
                )
                QApplication.instance().quit()
            return
        if result != QDialog.DialogCode.Accepted or dialog.administrator is None:
            return
        try:
            self.context.organization_keys.ensure_initialized()
        except (OSError, OrganizationKeyError) as exc:
            QMessageBox.critical(self, "密钥初始化失败", str(exc))
            return
        workspace = AdminWorkspace(
            self.context.database,
            dialog.administrator,
            self.context.paths.assets,
            self.context.organization_keys,
        )
        workspace.home_requested.connect(self.show_home)
        self.pages.addWidget(workspace)
        self.pages.setCurrentWidget(workspace)
        self.background_surface.set_home_active(False)

    def replace_collaboration_package(self) -> None:
        answer = QMessageBox.warning(
            self,
            "安装新工作包",
            "新工作包会替换当前协作题库。尚未导出的修改将无法恢复，建议先导出题库变更包。\n\n"
            "本机考试、练习和复盘记录不会受影响。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.show_home()
        self._install_collaboration_package(self._collaboration_service(), replace_existing=True)

    def _collaboration_service(self) -> BankCollaborationService:
        context = self._collaboration_context()
        return BankCollaborationService(
            context.database,
            QuestionRepository(context.database),
            AssetManager(context.database, context.paths.assets),
            context.administrators,
        )

    def _collaboration_context(self) -> ApplicationContext:
        return ApplicationContext.create(
            AppPaths.from_root(self.context.paths.root / "collaboration")
        )

    def _refresh_collaboration_entry(self) -> None:
        if self.admin_enabled:
            return
        available = self._collaboration_service().installed_work_package() is not None
        self.home_page.set_collaboration_available(available)

    def _refresh_trust_entry(self) -> None:
        if self.admin_enabled:
            return
        try:
            count = len(self.context.organization_keys.trusted_public_keys())
        except OrganizationKeyError:
            count = 0
        self.home_page.set_trust_certificate_count(count)

    def import_trust_certificates(self) -> None:
        path_texts, _ = QFileDialog.getOpenFileNames(
            self,
            "选择考试信任证书",
            str(Path.home() / "Desktop"),
            "考试信任证书 (*.examtrust)",
        )
        if not path_texts:
            return
        try:
            known = set(self.context.organization_keys.trusted_public_keys())
        except OrganizationKeyError as exc:
            QMessageBox.critical(self, "信任证书错误", str(exc))
            return
        added: list[str] = []
        existing: list[str] = []
        failures: list[str] = []
        for path_text in path_texts:
            path = Path(path_text)
            try:
                signer_id = self.context.organization_keys.import_trust_certificate(
                    path.read_bytes(),
                    path.name,
                )
            except (OSError, OrganizationKeyError) as exc:
                failures.append(f"{path.name}：{exc}")
                continue
            if signer_id in known:
                existing.append(f"{path.name}（{signer_id}）")
            else:
                added.append(f"{path.name}（{signer_id}）")
                known.add(signer_id)
        self._refresh_trust_entry()
        lines = [
            f"新增：{len(added)} 个",
            f"已存在：{len(existing)} 个",
            f"失败：{len(failures)} 个",
            f"当前可信证书总数：{len(known)} 个",
        ]
        if added:
            lines.extend(("", "新增证书：", *added))
        if existing:
            lines.extend(("", "重复证书：", *existing))
        if failures:
            lines.extend(("", "失败文件：", *failures))
            QMessageBox.warning(self, "证书导入完成", "\n".join(lines))
        else:
            QMessageBox.information(self, "证书导入完成", "\n".join(lines))

    def open_collaboration(self) -> None:
        if self.admin_enabled:
            return
        service = self._collaboration_service()
        context = self._collaboration_context()
        installed = service.installed_work_package()
        if installed is None:
            self._install_collaboration_package(service)
            installed = service.installed_work_package()
            if installed is None:
                return
        dialog = AdminLoginDialog(
            context.administrators,
            self,
            required_role=AdminRole.ADMIN,
            allow_recovery=False,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.administrator is None:
            return
        workspace = CollaborationWorkspace(
            context.database,
            dialog.administrator,
            context.paths.assets,
            service,
        )
        workspace.home_requested.connect(self.show_home)
        workspace.replace_package_requested.connect(self.replace_collaboration_package)
        self.pages.addWidget(workspace)
        self.pages.setCurrentWidget(workspace)
        self.background_surface.set_home_active(False)

    def _install_collaboration_package(
        self,
        service: BankCollaborationService,
        *,
        replace_existing: bool = False,
    ) -> None:
        if not self._ensure_trust_installed():
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择协作工作包",
            str(Path.home() / "Desktop"),
            "协作工作包 (*.bankwork)",
        )
        if not path_text:
            return
        dialog = InstallWorkPackageDialog(Path(path_text), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target_root = self.context.paths.root / "collaboration"
        staging_root = self.context.paths.root / "collaboration_installing"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging_context = ApplicationContext.create(AppPaths.from_root(staging_root))
        staging_service = BankCollaborationService(
            staging_context.database,
            QuestionRepository(staging_context.database),
            AssetManager(staging_context.database, staging_context.paths.assets),
            staging_context.administrators,
        )
        try:
            installed = staging_service.install_work_package(
                Path(path_text).read_bytes(),
                package_password=dialog.first_value,
                local_login_password=dialog.second_value,
                trusted_signers=self.context.organization_keys.trusted_public_keys(),
            )
        except (OSError, PermissionError, ValueError) as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            QMessageBox.critical(self, "工作包安装失败", str(exc))
            return
        try:
            backup_root = self.context.paths.root / "collaboration_previous"
            if backup_root.exists():
                shutil.rmtree(backup_root)
            if target_root.exists():
                target_root.replace(backup_root)
            try:
                staging_root.replace(target_root)
            except OSError:
                if backup_root.exists() and not target_root.exists():
                    backup_root.replace(target_root)
                raise
            shutil.rmtree(backup_root, ignore_errors=True)
        except OSError as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            QMessageBox.critical(self, "工作包安装失败", f"无法替换协作数据：{exc}")
            return
        self._refresh_collaboration_entry()
        QMessageBox.information(
            self,
            "协作工作包已安装",
            f"管理员：{installed.admin_name}\n题库题目：{installed.question_count} 道\n\n"
            + ("协作题库已更新。" if replace_existing else "现在可以登录协作题库。"),
        )

    def show_home(self) -> None:
        current = self.pages.currentWidget()
        self.pages.setCurrentWidget(self.home_page)
        self.background_surface.set_home_active(True)
        self._refresh_collaboration_entry()
        self._refresh_trust_entry()
        if current is not self.home_page:
            self.pages.removeWidget(current)
            current.deleteLater()

    def choose_exam_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择正式考试包",
            str(Path.home() / "Desktop"),
            "正式考试包 (*.exampack)",
        )
        if not path:
            return
        if not self._ensure_trust_installed():
            return
        review_store = SubmittedReviewStore(self.context.paths.state / "submitted_reviews")
        dialog = ExamAccessDialog(
            Path(path),
            self.context.organization_keys.trusted_public_keys(),
            review_store,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.definition is None:
            return
        if dialog.submitted_review is not None:
            self._show_exam_result(dialog.submitted_review, dialog.definition.assets)
            return
        if dialog.definition.monitoring_enabled:
            consent = MonitoringConsentDialog(dialog.definition.name, self)
            if consent.exec() != QDialog.DialogCode.Accepted:
                return
        self._start_or_resume_exam(dialog.definition, dialog.candidate_name, review_store)

    def choose_practice_package(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择练习包",
            str(Path.home() / "Desktop"),
            "练习包 (*.practicepack)",
        )
        if not path:
            return
        if not self._ensure_trust_installed():
            return
        package_path = Path(path)
        try:
            package_data = package_path.read_bytes()
            if PasswordPackageCodec.requires_password(package_data):
                access = PracticeAccessDialog(
                    package_path,
                    self.context.organization_keys.trusted_public_keys(),
                    self,
                )
                if access.exec() != QDialog.DialogCode.Accepted or access.definition is None:
                    return
                definition = access.definition
            else:
                definition = PracticePackageReader.open(
                    package_data,
                    distribution_password="",
                    trusted_signers=self.context.organization_keys.trusted_public_keys(),
                )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "无法打开练习包", str(exc))
            return
        setup = PracticeSetupDialog(definition, self)
        if setup.exec() != QDialog.DialogCode.Accepted or setup.counts is None:
            return
        service = PracticeService(
            self.context.database,
            QuestionRepository(self.context.database),
            AssetManager(self.context.database, self.context.paths.assets),
        )
        try:
            session = service.start_session(definition, setup.counts)
        except ValueError as exc:
            QMessageBox.critical(self, "无法开始练习", str(exc))
            return
        runner = PracticeRunnerPage(session, service)
        runner.home_requested.connect(self.show_home)
        runner.finished.connect(self._show_practice_result)
        self.pages.addWidget(runner)
        self.pages.setCurrentWidget(runner)
        self.background_surface.set_home_active(False)

    def _show_practice_result(self, session, grade) -> None:
        current = self.pages.currentWidget()
        page = PracticeResultPage(session, grade)
        page.home_requested.connect(self.show_home)
        self.pages.addWidget(page)
        self.pages.setCurrentWidget(page)
        self.background_surface.set_home_active(False)
        if current is not self.home_page:
            self.pages.removeWidget(current)
            current.deleteLater()

    def _ensure_trust_installed(self) -> bool:
        try:
            if self.context.organization_keys.trusted_public_keys():
                return True
        except OrganizationKeyError as exc:
            QMessageBox.critical(self, "信任证书错误", str(exc))
            return False
        answer = QMessageBox.question(
            self,
            "安装信任证书",
            "首次打开考试包需要安装管理员发放的信任证书。现在选择证书吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择考试信任证书",
            str(Path.home() / "Desktop"),
            "考试信任证书 (*.examtrust)",
        )
        if not path_text:
            return False
        try:
            self.context.organization_keys.import_trust_certificate(
                Path(path_text).read_bytes(),
                Path(path_text).name,
            )
        except (OSError, OrganizationKeyError) as exc:
            QMessageBox.critical(self, "证书安装失败", str(exc))
            return False
        self._refresh_trust_entry()
        return True

    def _start_or_resume_exam(self, definition, candidate_name: str, review_store) -> None:
        state_key = hashlib.sha256(
            definition.session_auth_key + b"\0exam-state\0" + definition.package_id.encode()
        ).digest()
        state_store = ExamStateStore(
            [self.context.paths.state, self.context.paths.root / "state_backup"],
            state_key,
            filename="active_exam_{}.state".format(
                hashlib.sha256(definition.package_id.encode("utf-8")).hexdigest()[:16]
            ),
        )
        attempts = AttemptService(self.context.database, state_store, review_store)
        try:
            state = state_store.load(definition)
        except FileNotFoundError:
            try:
                state = attempts.start(
                    definition,
                    candidate_name=candidate_name,
                    software_version=__version__,
                )
            except (AttemptError, ValueError, OSError) as exc:
                QMessageBox.critical(self, "无法开始考试", str(exc))
                return
        except ExamStateStoreError as exc:
            attempts.mark_state_error(definition.package_id, str(exc))
            QMessageBox.critical(self, "无法恢复考试", str(exc))
            return
        else:
            attempts.clear_state_error(definition.package_id)
            if state.candidate_name.casefold() != candidate_name.casefold():
                QMessageBox.critical(self, "无法恢复考试", "未完成考试属于另一名考生")
                return

        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        try:
            runner = ExamRunnerPage(
                definition,
                state,
                attempts,
                self.context.paths.results,
                Path(desktop) / "待提交答题记录",
            )
        except OSError as exc:
            QMessageBox.critical(self, "监控组件无法启动", str(exc))
            return
        runner.submitted.connect(
            lambda review, assets=definition.assets: self._show_exam_result(review, assets)
        )
        self.pages.addWidget(runner)
        self.pages.setCurrentWidget(runner)
        self.background_surface.set_home_active(False)
        self.showMaximized()

    def _show_exam_result(self, review, assets: dict[str, bytes] | None = None) -> None:
        current = self.pages.currentWidget()
        result_page = ExamResultPage(review, assets)
        result_page.home_requested.connect(self.show_home)
        self.pages.addWidget(result_page)
        self.pages.setCurrentWidget(result_page)
        self.background_surface.set_home_active(False)
        if current is not self.home_page:
            self.pages.removeWidget(current)
            current.deleteLater()

    def closeEvent(self, event: QCloseEvent) -> None:
        current = self.pages.currentWidget()
        if isinstance(current, ExamRunnerPage) and current.state.status.value == "active":
            QMessageBox.warning(self, "考试进行中", "考试进行中，不能关闭软件。")
            event.ignore()
            return
        event.accept()


def run_application(
    paths: AppPaths | None = None,
    *,
    screenshot_path: Path | None = None,
    admin_enabled: bool = True,
) -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName(PRODUCT_NAME)
    application.setOrganizationName("WaffleMisu")
    application.setApplicationVersion(__version__)
    application.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    context = ApplicationContext.create(paths or AppPaths.for_current_user())
    theme_manager = ThemeManager(application, ThemeSettingsStore(context.paths.app))
    window = MainWindow(context, theme_manager, admin_enabled=admin_enabled)
    window.show()
    if screenshot_path is not None:
        application.processEvents()
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(screenshot_path)):
            raise OSError(f"无法保存界面截图：{screenshot_path}")
        window.close()
        return 0
    return application.exec()
