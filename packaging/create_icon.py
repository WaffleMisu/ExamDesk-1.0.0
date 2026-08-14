from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BACKGROUND = "#2563A6"
OUTLINE = "#EAF5FF"
GLYPH = "#FFFFFF"
ACCENT = "#54B3E6"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _font(logical_size: int) -> ImageFont.FreeTypeFont:
    chinese = Path("C:/Windows/Fonts/msyhbd.ttc")
    fallback = Path("C:/Windows/Fonts/arialbd.ttf")
    if chinese.exists():
        return ImageFont.truetype(str(chinese), logical_size)
    return ImageFont.truetype(str(fallback), logical_size)


def _centered_brand(draw: ImageDraw.ImageDraw, font, center: tuple[float, float]) -> None:
    first = "E"
    second = "D"
    first_box = draw.textbbox((0, 0), first, font=font)
    second_box = draw.textbbox((0, 0), second, font=font)
    first_width = first_box[2] - first_box[0]
    second_width = second_box[2] - second_box[0]
    height = max(first_box[3] - first_box[1], second_box[3] - second_box[1])
    left = center[0] - (first_width + second_width) / 2
    top = center[1] - height / 2
    draw.text((left - first_box[0], top - first_box[1]), first, fill=GLYPH, font=font)
    draw.text(
        (left + first_width - second_box[0], top - second_box[1]),
        second,
        fill=ACCENT,
        font=font,
    )


def render_icon(size: int) -> Image.Image:
    oversample = 4 if size <= 256 else 1
    canvas_size = size * oversample
    scale = canvas_size / 512

    def value(number: float) -> int:
        return round(number * scale)

    image = Image.new("RGBA", (canvas_size, canvas_size), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (value(34), value(34), value(478), value(478)),
        radius=value(64),
        outline=OUTLINE,
        width=max(1, value(10)),
    )

    compact = size <= 32
    line_width = max(1, value(7 if not compact else 12))
    if compact:
        draw.line((value(96), value(165), value(338), value(165)), fill=GLYPH, width=line_width)
        draw.line((value(96), value(255), value(300), value(255)), fill=GLYPH, width=line_width)
        draw.line((value(96), value(345), value(338), value(345)), fill=GLYPH, width=line_width)
        check_center = (value(382), value(255))
        check_radius = value(54)
    else:
        brand_font = _font(max(1, value(86)))
        _centered_brand(draw, brand_font, (value(256), value(166)))
        draw.line((value(96), value(275), value(367), value(275)), fill=GLYPH, width=line_width)
        option_font = _font(max(1, value(38)))
        draw.text((value(101), value(284)), "A B C", fill=GLYPH, font=option_font)
        draw.line((value(96), value(365), value(367), value(365)), fill=GLYPH, width=line_width)
        check_center = (value(389), value(326))
        check_radius = value(43)

    draw.ellipse(
        (
            check_center[0] - check_radius,
            check_center[1] - check_radius,
            check_center[0] + check_radius,
            check_center[1] + check_radius,
        ),
        fill=GLYPH,
    )
    check_width = max(1, value(10 if not compact else 15))
    draw.line(
        (
            check_center[0] - value(19),
            check_center[1],
            check_center[0] - value(5),
            check_center[1] + value(17),
            check_center[0] + value(22),
            check_center[1] - value(22),
        ),
        fill=BACKGROUND,
        width=check_width,
        joint="curve",
    )
    draw.rectangle((value(72), value(421), value(440), value(445)), fill=ACCENT)

    if oversample > 1:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image


def main() -> None:
    destination = Path(__file__).with_name("app.ico")
    render_icon(512).save(destination.with_suffix(".png"), format="PNG")
    frames = [render_icon(size) for size in ICON_SIZES]
    largest = frames[-1]
    largest.save(
        destination,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=frames[:-1],
    )


if __name__ == "__main__":
    main()
