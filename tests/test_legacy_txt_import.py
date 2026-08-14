from pathlib import Path

from examdesk.domain.enums import QuestionType
from examdesk.importers import parse_legacy_txt

HEADERS = "编号|章节|题型|题目|A|B|C|D|答案|依据|分值|题图|A图|B图|C图|D图"


def test_imports_gbk_pipe_delimited_choice_bank(tmp_path: Path) -> None:
    image_dir = tmp_path / "图片"
    image_dir.mkdir()
    (image_dir / "001.jpg").write_bytes(b"image")
    content = "\n".join(
        [
            HEADERS,
            "001|安全规范|单选|应选择哪项？|甲|乙|丙|丁|C|培训手册第3条|1|图片\\001.jpg||||",
            "002|审核|多选|应选择哪些？|甲|乙|丙|丁|ABC|培训手册第4条|2|||||",
        ]
    )
    path = tmp_path / "选择题.txt"
    path.write_bytes(content.encode("gbk"))

    result = parse_legacy_txt(path)

    assert result.encoding == "gbk"
    assert result.delimiter == "|"
    assert len(result.questions) == 2
    assert result.questions[0].question.correct_option_keys == {"C"}
    assert result.questions[1].question.question_type is QuestionType.MULTIPLE
    assert result.error_count == 0


def test_imports_utf8_tab_fill_with_grouped_unordered_answers_and_single_score(
    tmp_path: Path,
) -> None:
    headers = HEADERS.split("|")
    values = [
        "008",
        "填空",
        "填空",
        "（1）（2）（3）（4）（5）（6）（7）（8）",
        "",
        "",
        "",
        "",
        "@1-3,5-7|1;2;3;4;5;6;7;8;",
        "培训手册",
        "1",
        "",
        "",
        "",
        "",
        "",
    ]
    path = tmp_path / "填空题.txt"
    path.write_text("\t".join(headers) + "\n" + "\t".join(values), encoding="utf-8")

    result = parse_legacy_txt(path)
    question = result.questions[0].question

    assert result.encoding == "utf-8"
    assert question.question_type is QuestionType.FILL
    assert len(question.blanks) == 8
    assert [group.indexes for group in question.unordered_groups] == [
        (1, 2, 3),
        (5, 6, 7),
    ]
    assert str(question.score) == "8"
    assert result.error_count == 0


def test_import_reports_duplicate_numbers_and_unsafe_or_missing_images(tmp_path: Path) -> None:
    rows = [
        HEADERS,
        "001|章节|单选|第一题|甲|乙|||A|依据|1|..\\secret.png||||",
        "001|章节|单选|第二题|甲|乙|||B|依据|1|图片\\missing.png||||",
    ]
    path = tmp_path / "题库.txt"
    path.write_text("\n".join(rows), encoding="utf-8")

    result = parse_legacy_txt(path)
    codes = {issue.code for issue in result.issues}

    assert "duplicate_number" in codes
    assert "unsafe_path" in codes
    assert "missing_image" in codes


def test_import_rejects_unknown_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("编号,题型,题目,答案,分值", encoding="utf-8")

    try:
        parse_legacy_txt(path)
    except ValueError as exc:
        assert "制表符" in str(exc)
    else:
        raise AssertionError("expected delimiter error")

