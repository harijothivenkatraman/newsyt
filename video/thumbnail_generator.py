"""
video/thumbnail_generator.py
Creates professional news-channel thumbnails using Pillow.
Style: Bold breaking-news aesthetic with gradient overlays,
source logo area, and punchy headline text.
"""

import os
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from loguru import logger
import requests
from io import BytesIO
import random


# Color palettes per news channel style
PALETTES = {
    "breaking": {
        "bg_top":    (180, 0, 0),
        "bg_bottom": (30, 0, 0),
        "accent":    (255, 200, 0),
        "text":      (255, 255, 255),
        "badge_bg":  (255, 30, 30),
        "badge_txt": (255, 255, 255),
        "badge_label": "BREAKING NEWS",
    },
    "analysis": {
        "bg_top":    (10, 30, 80),
        "bg_bottom": (5, 10, 30),
        "accent":    (0, 180, 255),
        "text":      (255, 255, 255),
        "badge_bg":  (0, 120, 220),
        "badge_txt": (255, 255, 255),
        "badge_label": "SPECIAL REPORT",
    },
    "tech": {
        "bg_top":    (0, 50, 30),
        "bg_bottom": (0, 15, 10),
        "accent":    (0, 255, 140),
        "text":      (255, 255, 255),
        "badge_bg":  (0, 180, 90),
        "badge_txt": (0, 0, 0),
        "badge_label": "TECHNOLOGY",
    },
    "sports": {
        "bg_top":    (20, 80, 180),
        "bg_bottom": (5, 20, 60),
        "accent":    (255, 165, 0),
        "text":      (255, 255, 255),
        "badge_bg":  (255, 165, 0),
        "badge_txt": (0, 0, 50),
        "badge_label": "SPORTS",
    },
}

CATEGORY_PALETTE = {
    "national": "breaking", "india": "breaking", "politics": "breaking",
    "technology": "tech", "tech": "tech", "science": "tech",
    "sports": "sports",
    "business": "analysis", "world": "analysis", "international": "analysis",
}


class ThumbnailGenerator:
    WIDTH  = 1280
    HEIGHT = 720

    def __init__(self, output_dir: str = "./output/thumbnails"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, video_content, article_id: str) -> str:
        """Generate a thumbnail. Returns path to PNG file."""
        palette_key = CATEGORY_PALETTE.get(video_content.category.lower(), "breaking")
        palette = PALETTES[palette_key]

        img = Image.new("RGB", (self.WIDTH, self.HEIGHT))
        self._draw_gradient(img, palette["bg_top"], palette["bg_bottom"])

        # Try to place article image in the right half
        if hasattr(video_content, '_image_url') and video_content._image_url:
            self._place_article_image(img, video_content._image_url)
        else:
            self._draw_abstract_bg(img, palette)

        draw = ImageDraw.Draw(img)

        # Dark overlay on left side for text readability
        overlay = Image.new("RGBA", (self.WIDTH, self.HEIGHT), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        ov_draw.rectangle([0, 0, 780, self.HEIGHT], fill=(0, 0, 0, 180))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Accent bar on left edge
        draw.rectangle([0, 0, 8, self.HEIGHT], fill=palette["accent"])

        # BADGE (BREAKING NEWS etc)
        badge_font = self._font(28, bold=True)
        badge_text = palette["badge_label"]
        badge_w = badge_font.getbbox(badge_text)[2] + 30
        draw.rectangle([30, 40, 30 + badge_w, 80], fill=palette["badge_bg"])
        draw.text((45, 48), badge_text, font=badge_font, fill=palette["badge_txt"])

        # Main headline
        headline = video_content.thumbnail_headline.upper()
        self._draw_wrapped_text(
            draw, headline, x=30, y=110, max_width=700,
            font_size=68, bold=True, color=palette["text"],
            shadow_color=(0, 0, 0),
        )

        # Sub-headline
        subtext = video_content.thumbnail_subtext
        sub_font = self._font(32)
        draw.text((32, 530), subtext, font=sub_font, fill=palette["accent"])

        # Source watermark bottom left
        src_font = self._font(24)
        draw.text((30, self.HEIGHT - 50), f"Source: {video_content.source_name}",
                  font=src_font, fill=(200, 200, 200))

        # Bottom accent bar
        draw.rectangle([0, self.HEIGHT - 6, self.WIDTH, self.HEIGHT], fill=palette["accent"])

        out_path = str(self.output_dir / f"{article_id}_thumbnail.png")
        img.save(out_path, "PNG", quality=95)
        logger.success(f"Thumbnail saved: {out_path}")
        return out_path

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _draw_gradient(self, img: Image.Image, top: tuple, bottom: tuple):
        draw = ImageDraw.Draw(img)
        for y in range(self.HEIGHT):
            ratio = y / self.HEIGHT
            r = int(top[0] + (bottom[0] - top[0]) * ratio)
            g = int(top[1] + (bottom[1] - top[1]) * ratio)
            b = int(top[2] + (bottom[2] - top[2]) * ratio)
            draw.line([(0, y), (self.WIDTH, y)], fill=(r, g, b))

    def _draw_abstract_bg(self, img: Image.Image, palette: dict):
        """Draw geometric abstract shapes on the right side."""
        draw = ImageDraw.Draw(img)
        cx, cy = 1050, 360
        for i in range(5, 0, -1):
            r = 80 * i
            alpha = 30 + i * 10
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                outline=(*palette["accent"], alpha),
                width=2
            )

    def _place_article_image(self, img: Image.Image, image_url: str):
        try:
            resp = requests.get(image_url, timeout=8)
            article_img = Image.open(BytesIO(resp.content)).convert("RGB")
            # Resize and crop for right half
            article_img = article_img.resize((640, 720))
            # Darken slightly
            enhancer = ImageEnhance.Brightness(article_img)
            article_img = enhancer.enhance(0.65)
            img.paste(article_img, (640, 0))
        except Exception as e:
            logger.warning(f"Could not place article image: {e}")

    def _font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        """
        Load a system font, searching Windows → Linux → macOS paths.
        Falls back to PIL's built-in bitmap font if nothing is found.
        """
        windir = os.environ.get("WINDIR", "C:\\Windows")
        font_dir = os.path.join(windir, "Fonts")

        if bold:
            candidates = [
                # Windows
                os.path.join(font_dir, "arialbd.ttf"),        # Arial Bold
                os.path.join(font_dir, "calibrib.ttf"),        # Calibri Bold
                os.path.join(font_dir, "segoeuib.ttf"),        # Segoe UI Bold
                os.path.join(font_dir, "tahomabd.ttf"),        # Tahoma Bold
                os.path.join(font_dir, "verdanab.ttf"),        # Verdana Bold
                os.path.join(font_dir, "impact.ttf"),          # Impact
                # Linux
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
                # macOS
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
            ]
        else:
            candidates = [
                # Windows
                os.path.join(font_dir, "arial.ttf"),           # Arial
                os.path.join(font_dir, "calibri.ttf"),         # Calibri
                os.path.join(font_dir, "segoeui.ttf"),         # Segoe UI
                os.path.join(font_dir, "tahoma.ttf"),          # Tahoma
                os.path.join(font_dir, "verdana.ttf"),         # Verdana
                # Linux
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                # macOS
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            ]

        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue

        # Last resort: PIL default (small bitmap, no size control)
        logger.warning(
            "No system font found — using PIL default bitmap font. "
            "Thumbnail text will be small. Install Arial or Calibri to fix this."
        )
        return ImageFont.load_default()

    def _draw_wrapped_text(
        self, draw, text: str, x: int, y: int, max_width: int,
        font_size: int, bold: bool, color: tuple, shadow_color: tuple
    ):
        font = self._font(font_size, bold=bold)
        # Word wrap
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            if font.getbbox(test)[2] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        line_h = font_size + 8
        for i, line in enumerate(lines[:4]):   # max 4 lines
            ly = y + i * line_h
            # Shadow
            draw.text((x + 2, ly + 2), line, font=font, fill=shadow_color)
            draw.text((x, ly), line, font=font, fill=color)
