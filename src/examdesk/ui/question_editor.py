from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from examdesk.domain.enums import (
    MatchMode,
    QuestionStatus,
    QuestionType,
    UsageScope,
)
from examdesk.domain.models import (
    BlankDefinition,
    QuestionDraft,
    QuestionOption,
    UnorderedGroup,
)
from examdesk.questions import (
    AssetManager,
    QuestionRepository,
    QuestionValidationError,
    validate_question,
)

from .window_sizing import fit_window_to_available

TYPE_OPTIONS = (
    ("单选题", QuestionType.SINGLE),
    ("多选题", QuestionType.MULTIPLE),
    ("判断题", QuestionType.JUDGE),
    ("填空题", QuestionType.FILL),
)
SCOPE_OPTIONS = (
    ("练习和考试", UsageScope.BOTH),
    ("仅考试", UsageScope.EXAM_ONLY),
    ("仅练习", UsageScope.PRACTICE_ONLY),
)
STATUS_OPTIONS = (
    ("启用", QuestionStatus.ENABLED),
    ("草稿", QuestionStatus.DRAFT),
    ("停用", QuestionStatus.DISABLED),
)


class ImageBucket(QWidget):
    def __init__(self, label: str, compact: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.images: list[tuple[bytes, str]] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.count_label = QLabel(label)
        self.count_label.setObjectName("pageMeta")
        layout.addWidget(self.count_label)
        add = QPushButton("添加图片")
        add.clicked.connect(self.add_files)
        if compact:
            add.setFixedWidth(96)
        layout.addWidget(add)
        paste = QPushButton("粘贴")
        paste.clicked.connect(self.paste_clipboard)
        if compact:
            paste.setFixedWidth(72)
        layout.addWidget(paste)
        clear = QPushButton("清空")
        clear.clicked.connect(self.clear_images)
        if compact:
            clear.setFixedWidth(72)
        layout.addWidget(clear)
        layout.addStretch(1)
        self.base_label = label
        self._refresh()

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            str(Path.home() / "Desktop"),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)",
        )
        for path_text in paths:
            path = Path(path_text)
            try:
                self.images.append((path.read_bytes(), path.name))
            except OSError:
                continue
        self._refresh()

    def paste_clipboard(self) -> None:
        mime = QApplication.clipboard().mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.is_file():
                    try:
                        self.images.append((path.read_bytes(), path.name))
                    except OSError:
                        continue
        elif mime.hasImage():
            image = QApplication.clipboard().image()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if image.save(buffer, "PNG"):
                self.images.append((bytes(buffer.data()), "clipboard.png"))
        self._refresh()

    def clear_images(self) -> None:
        self.images.clear()
        self._refresh()

    def _refresh(self) -> None:
        self.count_label.setText(f"{self.base_label}：{len(self.images)} 张")


class QuestionEditorDialog(QDialog):
    def __init__(
        self,
        repository: QuestionRepository,
        asset_manager: AssetManager,
        actor_id: str,
        question: QuestionDraft | None = None,
        version: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.asset_manager = asset_manager
        self.actor_id = actor_id
        self.question = question
        self.version = version
        self.saved_question = None
        self.option_images = {key: ImageBucket(f"{key}图", compact=True) for key in "ABCD"}
        self.setWindowTitle("编辑题目" if question is not None else "新建题目")
        fit_window_to_available(self, 900, 820, minimum_width=640, minimum_height=460)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        title = QLabel("编辑题目" if question is not None else "新建题目")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(4, 4, 12, 4)
        self.content_layout.setSpacing(14)
        self._build_metadata()
        self._build_stem()
        self._build_choice_editor()
        self._build_fill_editor()
        self._build_basis()
        self.content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = QPushButton("保存题目")
        save.setObjectName("primaryButton")
        save.clicked.connect(self.save)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._type_changed()
        if question is not None:
            self._load_question(question)

    def _build_metadata(self) -> None:
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.type_combo = QComboBox()
        for label, value in TYPE_OPTIONS:
            self.type_combo.addItem(label, value)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        self.number_edit = QLineEdit()
        self.year_spin = QSpinBox()
        self.year_spin.setRange(0, 2100)
        self.year_spin.setSpecialValueText("长期有效")
        self.chapter_edit = QLineEdit()
        self.clause_edit = QLineEdit()
        self.source_edit = QLineEdit()
        self.difficulty_edit = QLineEdit()
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("多个标签用英文分号隔开")
        self.scope_combo = QComboBox()
        for label, value in SCOPE_OPTIONS:
            self.scope_combo.addItem(label, value)
        self.status_combo = QComboBox()
        for label, value in STATUS_OPTIONS:
            self.status_combo.addItem(label, value)
        form.addRow("题型", self.type_combo)
        form.addRow("编号", self.number_edit)
        form.addRow("适用年度", self.year_spin)
        form.addRow("章节", self.chapter_edit)
        form.addRow("条款", self.clause_edit)
        form.addRow("来源", self.source_edit)
        form.addRow("难度", self.difficulty_edit)
        form.addRow("标签", self.tags_edit)
        form.addRow("使用范围", self.scope_combo)
        form.addRow("状态", self.status_combo)
        self.content_layout.addLayout(form)

    def _build_stem(self) -> None:
        row = QHBoxLayout()
        label = QLabel("题目内容")
        label.setObjectName("entryTitle")
        row.addWidget(label)
        row.addStretch(1)
        self.insert_blank_button = QPushButton("插入填空")
        self.insert_blank_button.clicked.connect(self.insert_blank)
        row.addWidget(self.insert_blank_button)
        self.content_layout.addLayout(row)
        self.stem_edit = QTextEdit()
        self.stem_edit.setMinimumHeight(120)
        self.content_layout.addWidget(self.stem_edit)
        self.stem_images = ImageBucket("题图")
        self.content_layout.addWidget(self.stem_images)

    def _build_choice_editor(self) -> None:
        self.choice_panel = QFrame()
        layout = QVBoxLayout(self.choice_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.option_edits = {}
        self.correct_checks = {}
        self.option_rows = {}
        self.correct_group = QButtonGroup(self)
        for key in "ABCD":
            row_widget = QWidget()
            row = QVBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            text_row = QHBoxLayout()
            text_row.addWidget(QLabel(key), 0)
            edit = QLineEdit()
            text_row.addWidget(edit, 1)
            correct = QCheckBox("正确答案")
            text_row.addWidget(correct)
            row.addLayout(text_row)
            row.addWidget(self.option_images[key])
            self.option_edits[key] = edit
            self.correct_checks[key] = correct
            self.correct_group.addButton(correct)
            self.option_rows[key] = row_widget
            layout.addWidget(row_widget)
        score_row = QFormLayout()
        self.score_spin = QDoubleSpinBox()
        self.score_spin.setRange(0.01, 1000)
        self.score_spin.setDecimals(2)
        self.score_spin.setValue(1)
        score_row.addRow("题目分值", self.score_spin)
        layout.addLayout(score_row)
        self.content_layout.addWidget(self.choice_panel)

    def _build_fill_editor(self) -> None:
        self.fill_panel = QFrame()
        layout = QVBoxLayout(self.fill_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        title = QLabel("各空答案与分值")
        title.setObjectName("entryTitle")
        row.addWidget(title)
        row.addStretch(1)
        add = QPushButton("增加一个空")
        add.clicked.connect(self.add_blank_row)
        row.addWidget(add)
        remove = QPushButton("删除最后一空")
        remove.clicked.connect(self.remove_blank_row)
        row.addWidget(remove)
        layout.addLayout(row)
        self.blank_table = QTableWidget()
        self.blank_table.setColumnCount(4)
        self.blank_table.setHorizontalHeaderLabels(("空号", "合法答案（/分隔）", "每空分值", "无序组"))
        self.blank_table.verticalHeader().setVisible(False)
        self.blank_table.setMinimumHeight(190)
        self.blank_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.blank_table)
        self.content_layout.addWidget(self.fill_panel)

    def _build_basis(self) -> None:
        label = QLabel("依据（可选）")
        label.setObjectName("entryTitle")
        self.content_layout.addWidget(label)
        self.basis_edit = QTextEdit()
        self.basis_edit.setMinimumHeight(90)
        self.content_layout.addWidget(self.basis_edit)

    def _type_changed(self) -> None:
        question_type = QuestionType(self.type_combo.currentData())
        is_fill = question_type is QuestionType.FILL
        self.choice_panel.setVisible(not is_fill)
        self.fill_panel.setVisible(is_fill)
        self.insert_blank_button.setVisible(is_fill)
        self.correct_group.setExclusive(question_type in (QuestionType.SINGLE, QuestionType.JUDGE))
        if self.correct_group.exclusive():
            checked = [check for check in self.correct_checks.values() if check.isChecked()]
            for check in checked[1:]:
                self.correct_group.setExclusive(False)
                check.setChecked(False)
                self.correct_group.setExclusive(True)
        is_judge = question_type is QuestionType.JUDGE
        if is_judge:
            self.option_edits["A"].setText("正确")
            self.option_edits["B"].setText("错误")
        for key in "AB":
            self.option_edits[key].setReadOnly(is_judge)
        for key in "CD":
            self.option_rows[key].setVisible(not is_judge)
        if is_fill and self.blank_table.rowCount() == 0:
            self.add_blank_row()

    def insert_blank(self) -> None:
        self.add_blank_row()
        number = self.blank_table.rowCount()
        self.stem_edit.textCursor().insertText(f"（{number}）")
        self.stem_edit.setFocus()

    def add_blank_row(self) -> None:
        row = self.blank_table.rowCount()
        self.blank_table.insertRow(row)
        number = QLabel(str(row + 1))
        answer = QLineEdit()
        answer.setPlaceholderText("同义答案用 / 分隔")
        score = QDoubleSpinBox()
        score.setRange(0.01, 1000)
        score.setDecimals(2)
        score.setValue(1)
        group = QLineEdit()
        group.setPlaceholderText("如 1；固定空留空")
        self.blank_table.setCellWidget(row, 0, number)
        self.blank_table.setCellWidget(row, 1, answer)
        self.blank_table.setCellWidget(row, 2, score)
        self.blank_table.setCellWidget(row, 3, group)

    def remove_blank_row(self) -> None:
        if self.blank_table.rowCount() > 1:
            self.blank_table.removeRow(self.blank_table.rowCount() - 1)

    def save(self) -> None:
        try:
            draft = self._build_draft()
            issues = validate_question(draft)
            if issues:
                self.error_label.setText("；".join(issue.message for issue in issues[:5]))
                return
            self._materialize_images(draft)
            if self.question is None:
                self.saved_question = self.repository.create(draft, self.actor_id)
            else:
                self.saved_question = self.repository.update(
                    draft,
                    self.actor_id,
                    expected_version=self.version,
                )
        except (OSError, ValueError, QuestionValidationError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()

    def _build_draft(self) -> QuestionDraft:
        question_type = QuestionType(self.type_combo.currentData())
        common = {
            "question_type": question_type,
            "stem": self.stem_edit.toPlainText().strip(),
            "basis": self.basis_edit.toPlainText().strip(),
            "display_number": self.number_edit.text().strip(),
            "status": QuestionStatus(self.status_combo.currentData()),
            "usage_scope": UsageScope(self.scope_combo.currentData()),
            "applicable_year": self.year_spin.value() or None,
            "source": self.source_edit.text().strip(),
            "chapter": self.chapter_edit.text().strip(),
            "clause": self.clause_edit.text().strip(),
            "difficulty": self.difficulty_edit.text().strip(),
            "tags": _split(self.tags_edit.text()),
            "question_asset_ids": (["pending:stem"] if self.stem_images.images else []),
            "id": self.question.id if self.question is not None else None,
        }
        if common["id"] is None:
            common.pop("id")
        if question_type is QuestionType.FILL:
            blanks, groups = self._fill_data()
            return QuestionDraft(
                **common,
                blanks=blanks,
                unordered_groups=groups,
                score=sum((blank.score for blank in blanks), Decimal("0")),
            )
        option_keys = "AB" if question_type is QuestionType.JUDGE else "ABCD"
        options = []
        for key in option_keys:
            text = self.option_edits[key].text().strip()
            image_ids = (f"pending:{key}",) if self.option_images[key].images else ()
            if text or image_ids:
                options.append(QuestionOption(key, text, image_ids))
        return QuestionDraft(
            **common,
            options=options,
            correct_option_keys={key for key in option_keys if self.correct_checks[key].isChecked()},
            score=Decimal(str(self.score_spin.value())),
        )

    def _fill_data(self) -> tuple[list[BlankDefinition], list[UnorderedGroup]]:
        blanks = []
        groups: dict[str, list[int]] = {}
        for row in range(self.blank_table.rowCount()):
            answer = self.blank_table.cellWidget(row, 1).text()
            score = self.blank_table.cellWidget(row, 2).value()
            group = self.blank_table.cellWidget(row, 3).text().strip()
            index = row + 1
            blanks.append(
                BlankDefinition(
                    index,
                    tuple(part.strip() for part in answer.split("/") if part.strip()),
                    Decimal(str(score)),
                    MatchMode.TEXT_SIMILARITY,
                )
            )
            if group:
                groups.setdefault(group, []).append(index)
        unordered = [UnorderedGroup(tuple(indexes)) for indexes in groups.values()]
        return blanks, unordered

    def _materialize_images(self, draft: QuestionDraft) -> None:
        draft.question_asset_ids = [
            self.asset_manager.ingest_bytes(data, name).id
            for data, name in self.stem_images.images
        ]
        options = []
        for option in draft.options:
            asset_ids = tuple(
                self.asset_manager.ingest_bytes(data, name).id
                for data, name in self.option_images[option.key].images
            )
            options.append(QuestionOption(option.key, option.text, asset_ids))
        draft.options = options

    def _load_question(self, question: QuestionDraft) -> None:
        self.type_combo.setCurrentIndex(
            next(
                index
                for index in range(self.type_combo.count())
                if QuestionType(self.type_combo.itemData(index)) is question.question_type
            )
        )
        self.number_edit.setText(question.display_number)
        self.year_spin.setValue(question.applicable_year or 0)
        self.chapter_edit.setText(question.chapter)
        self.clause_edit.setText(question.clause)
        self.source_edit.setText(question.source)
        self.difficulty_edit.setText(question.difficulty)
        self.tags_edit.setText(";".join(question.tags))
        _select_enum(self.scope_combo, question.usage_scope)
        _select_enum(self.status_combo, question.status)
        self.stem_edit.setPlainText(question.stem)
        self.basis_edit.setPlainText(question.basis)
        self.stem_images.images = self._existing_images(question.question_asset_ids)
        self.stem_images._refresh()
        if question.question_type is QuestionType.FILL:
            self.blank_table.setRowCount(0)
            group_by_index = {
                index: str(group_number)
                for group_number, group in enumerate(question.unordered_groups, start=1)
                for index in group.indexes
            }
            for blank in question.blanks:
                self.add_blank_row()
                row = blank.index - 1
                self.blank_table.cellWidget(row, 1).setText("/".join(blank.accepted_answers))
                self.blank_table.cellWidget(row, 2).setValue(float(blank.score))
                self.blank_table.cellWidget(row, 3).setText(group_by_index.get(blank.index, ""))
            return
        self.score_spin.setValue(float(question.score))
        by_key = {option.key: option for option in question.options}
        for key in "ABCD":
            option = by_key.get(key)
            self.option_edits[key].setText(option.text if option else "")
            self.correct_checks[key].setChecked(key in question.correct_option_keys)
            self.option_images[key].images = self._existing_images(option.asset_ids if option else ())
            self.option_images[key]._refresh()

    def _existing_images(self, asset_ids) -> list[tuple[bytes, str]]:
        images = []
        for asset_id in asset_ids:
            try:
                record = self.asset_manager.get(asset_id)
                images.append((self.asset_manager.absolute_path(record).read_bytes(), Path(record.relative_path).name))
            except (KeyError, OSError, ValueError):
                continue
        return images


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]


def _select_enum(combo: QComboBox, value) -> None:
    for index in range(combo.count()):
        if type(value)(combo.itemData(index)) is value:
            combo.setCurrentIndex(index)
            return
