#!/usr/bin/env python3
"""Finish generated Mike Pod artwork with exact, readable typography."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = BASE_DIR / "assets" / "artwork" / "source"
FINAL_DIR = BASE_DIR / "assets" / "artwork" / "final"
CANVAS_SIZE = 3000

DISPLAY_FONT = Path("/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf")
BODY_FONT = Path("/System/Library/Fonts/Avenir Next.ttc")

OFF_WHITE = (245, 239, 220)
CYAN = (86, 213, 224)
AMBER = (241, 173, 74)
NAVY = (3, 18, 32)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.exists():
        raise RuntimeError(f"Required font does not exist: {path}")
    return ImageFont.truetype(str(path), size)


def fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    path: Path,
    *,
    max_size: int,
    min_size: int,
    max_width: int,
) -> ImageFont.FreeTypeFont:
    for size in range(max_size, min_size - 1, -4):
        candidate = font(path, size)
        left, _, right, _ = draw.textbbox((0, 0), text, font=candidate)
        if right - left <= max_width:
            return candidate
    return font(path, min_size)


def darken_bottom(image: Image.Image, *, start_y: int, opacity: int) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    height = image.height - start_y
    for y in range(start_y, image.height):
        alpha = int(opacity * ((y - start_y) / max(height, 1)) ** 0.7)
        for x in range(image.width):
            pixels[x, y] = (*NAVY, alpha)
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def darken_top(image: Image.Image, *, end_y: int, opacity: int) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(0, end_y):
        alpha = int(opacity * (1 - (y / max(end_y, 1))) ** 0.55)
        for x in range(image.width):
            pixels[x, y] = (*NAVY, alpha)
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def add_tracking(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    *,
    typeface: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    tracking: int,
) -> None:
    x, y = position
    for character in text:
        draw.text((x, y), character, font=typeface, fill=fill)
        bounds = draw.textbbox((0, 0), character, font=typeface)
        x += bounds[2] - bounds[0] + tracking


def finish_show_art(base_path: Path, output_dir: Path) -> tuple[Path, Path]:
    image = Image.open(base_path).convert("RGB").resize(
        (CANVAS_SIZE, CANVAS_SIZE),
        Image.Resampling.LANCZOS,
    )
    image = darken_bottom(image, start_y=2070, opacity=235)
    draw = ImageDraw.Draw(image)

    title_font = fitted_font(
        draw,
        "MIKE POD",
        DISPLAY_FONT,
        max_size=520,
        min_size=360,
        max_width=2440,
    )
    title_box = draw.textbbox((0, 0), "MIKE POD", font=title_font)
    title_width = title_box[2] - title_box[0]
    title_x = (CANVAS_SIZE - title_width) // 2
    draw.text(
        (title_x + 9, 2300 + 12),
        "MIKE POD",
        font=title_font,
        fill=(0, 0, 0, 145),
    )
    draw.text((title_x, 2300), "MIKE POD", font=title_font, fill=OFF_WHITE)

    rule_y = 2765
    draw.rounded_rectangle((660, rule_y, 2340, rule_y + 8), radius=4, fill=AMBER)
    strap_font = font(BODY_FONT, 58)
    strap = "DEEP RESEARCH FOR A CURIOUS MIND"
    strap_box = draw.textbbox((0, 0), strap, font=strap_font)
    strap_width = strap_box[2] - strap_box[0]
    draw.text(
        ((CANVAS_SIZE - strap_width) // 2, 2810),
        strap,
        font=strap_font,
        fill=CYAN,
    )

    png_path = output_dir / "mike-pod-show-artwork-3000.png"
    jpg_path = output_dir / "mike-pod-show-artwork-3000.jpg"
    image.convert("RGB").save(png_path, format="PNG", optimize=True)
    image.convert("RGB").save(
        jpg_path,
        format="JPEG",
        quality=94,
        optimize=True,
        progressive=True,
    )
    return png_path, jpg_path


def finish_episode_art(base_path: Path, output_dir: Path) -> tuple[Path, Path]:
    image = Image.open(base_path).convert("RGB").resize(
        (CANVAS_SIZE, CANVAS_SIZE),
        Image.Resampling.LANCZOS,
    )
    image = darken_top(image, end_y=1120, opacity=238)
    draw = ImageDraw.Draw(image)

    label_font = font(BODY_FONT, 58)
    add_tracking(
        draw,
        (235, 178),
        "MIKE POD  /  EPISODE 001",
        typeface=label_font,
        fill=CYAN,
        tracking=5,
    )
    draw.rounded_rectangle((235, 295, 1050, 303), radius=4, fill=AMBER)

    title_font = font(DISPLAY_FONT, 360)
    line_height = 345
    for index, line in enumerate(("WOLFRAM'S", "COMPUTATIONAL", "UNIVERSE")):
        draw.text(
            (226, 340 + index * line_height),
            line,
            font=title_font,
            fill=OFF_WHITE,
            stroke_width=2,
            stroke_fill=(9, 23, 37),
        )

    question_font = font(BODY_FONT, 64)
    question = "WHAT WOULD COUNT AS EVIDENCE?"
    question_box = draw.textbbox((0, 0), question, font=question_font)
    question_width = question_box[2] - question_box[0]
    question_x = CANVAS_SIZE - 235 - question_width
    draw.rounded_rectangle(
        (question_x - 44, 2740, CANVAS_SIZE - 190, 2864),
        radius=26,
        fill=(3, 18, 32, 220),
        outline=AMBER,
        width=5,
    )
    draw.text((question_x, 2767), question, font=question_font, fill=OFF_WHITE)

    png_path = output_dir / "episode-001-wolfram-universe-3000.png"
    jpg_path = output_dir / "episode-001-wolfram-universe-3000.jpg"
    image.convert("RGB").save(png_path, format="PNG", optimize=True)
    image.convert("RGB").save(
        jpg_path,
        format="JPEG",
        quality=94,
        optimize=True,
        progressive=True,
    )
    return png_path, jpg_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=FINAL_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        *finish_show_art(SOURCE_DIR / "show-base.png", args.output_dir),
        *finish_episode_art(
            SOURCE_DIR / "episode-001-wolfram-base.png",
            args.output_dir,
        ),
    ]
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
