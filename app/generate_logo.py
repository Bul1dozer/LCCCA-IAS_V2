"""
Generates a simple placeholder logo for Life Changing Christian Church
Academy. Run once during setup:

    python3 -m app.generate_logo

This keeps the project self-contained (no external image assets needed
for the demo). Replace static/img/logo.png with the real school crest
for production use.
"""

from PIL import Image, ImageDraw, ImageFont
import os

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "static", "img", "logo.png")


def main():
    size = 240
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # Outer circle (navy)
    navy = (16, 42, 89, 255)
    gold = (212, 175, 55, 255)
    draw.ellipse([4, 4, size - 4, size - 4], fill=navy, outline=gold, width=8)

    # Cross (gold) - represents the Christian academy
    cx, cy = size // 2, size // 2
    bar_w = 22
    draw.rectangle([cx - bar_w // 2, cy - 70, cx + bar_w // 2, cy + 70], fill=gold)
    draw.rectangle([cx - 55, cy - 28, cx + 55, cy - 28 + bar_w], fill=gold)

    # Open book shape underneath (white)
    draw.polygon(
        [
            (cx - 60, cy + 75),
            (cx, cy + 55),
            (cx + 60, cy + 75),
            (cx + 60, cy + 90),
            (cx, cy + 70),
            (cx - 60, cy + 90),
        ],
        fill=(255, 255, 255, 255),
    )

    img.save(OUT_PATH)
    print(f"Logo written to {OUT_PATH}")


if __name__ == "__main__":
    main()
