from pathlib import Path

from PIL import Image

from examdesk.branding import BRAND_RAIL_NAME, PRODUCT_NAME
from examdesk.version import __version__

PROJECT_ROOT = Path(__file__).parents[1]


def test_examdesk_product_name_and_version() -> None:
    assert PRODUCT_NAME == "ExamDesk 离线考试系统"
    assert BRAND_RAIL_NAME == "ExamDesk\n离线考试系统"
    assert __version__ == "1.0.0"


def test_examdesk_template_and_multisize_icon_exist() -> None:
    template = PROJECT_ROOT / "templates" / "ExamDesk_题库维护模板.xlsx"
    icon_path = PROJECT_ROOT / "packaging" / "app.ico"

    assert template.is_file()
    with Image.open(icon_path) as icon:
        sizes = icon.info.get("sizes", set())
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= sizes
