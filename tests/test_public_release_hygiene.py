from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
IGNORED_TOP_LEVEL_DIRECTORIES = {".venv", ".venv311"}
TEXT_SUFFIXES = {
    ".cmd",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def iter_public_project_files():
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if relative.parts and relative.parts[0] in IGNORED_TOP_LEVEL_DIRECTORIES:
            continue
        if path.is_file():
            yield path
PRIVATE_FILE_SUFFIXES = {
    ".bankpatch",
    ".bankwork",
    ".db",
    ".exambackup",
    ".exampack",
    ".examresult",
    ".examreview",
    ".examtrust",
    ".practicepack",
    ".sqlite",
    ".sqlite3",
}


def test_public_text_contains_no_private_brand_or_business_terms() -> None:
    banned_terms = (
        "中" + "色",
        "蓝" + "图",
        "zhong" + "se",
        "杨" + "欢",
        "国土" + "变更",
        "地" + "类",
        "图" + "斑",
        "公司" + "电脑",
        "open" + "ai",
        "chat" + "gpt",
        "co" + "dex",
        "clau" + "de",
        "gem" + "ini",
    )
    matches = []
    for path in iter_public_project_files():
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        for term in banned_terms:
            if term.casefold() in text:
                matches.append(f"{path.relative_to(PROJECT_ROOT)}: {term}")
    assert matches == []


def test_public_tree_contains_no_runtime_private_files() -> None:
    private_files = [
        path.relative_to(PROJECT_ROOT)
        for path in iter_public_project_files()
        if path.suffix.casefold() in PRIVATE_FILE_SUFFIXES
    ]
    assert private_files == []
