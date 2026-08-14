from pathlib import Path

from PIL import Image


def test_application_icon_contains_required_sizes_and_theme_color() -> None:
    path = Path(__file__).parents[1] / "packaging" / "app.ico"
    with Image.open(path) as icon:
        assert icon.ico.sizes() == {
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        }
        largest = icon.ico.getimage((256, 256)).convert("RGB")
    red, green, blue = largest.getpixel((128, 40))
    assert blue > green > red
    assert abs(red - 37) <= 2
    assert abs(green - 99) <= 2
    assert abs(blue - 166) <= 2
