"""
video/shorts_composer.py
Generates a 9:16 vertical YouTube Shorts video (≤ 59 seconds).

Layout:
  - Dark gradient background
  - Bold animated headline (top)
  - Source badge (bottom left)
  - Red accent bar (top & bottom)
  - TTS audio + looped BGM underneath
"""

import os
import textwrap
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    from moviepy import (
        AudioFileClip, ColorClip, CompositeVideoClip,
        TextClip, concatenate_videoclips, ImageClip,
    )
    import numpy as np
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    logger.warning("MoviePy not installed — Shorts composition will be skipped.")


SHORTS_MAX_DURATION = 59.0   # YouTube Shorts must be ≤ 59 s


class ShortsComposer:
    """Produces a 1080×1920 (9:16) Short from TTS audio + text overlays."""

    W = 1080
    H = 1920
    FPS = 30

    ACCENT  = (220, 30, 30)
    BG_TOP  = (10, 12, 20)
    BG_BOT  = (25, 5, 5)

    def __init__(self, output_dir: str = "./output/shorts"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def compose(
        self,
        audio_path: str,
        video_content,
        article_id: str,
    ) -> str:
        """Compose a Shorts MP4. Returns the output file path."""
        if not MOVIEPY_AVAILABLE:
            logger.warning("MoviePy unavailable — Shorts skipped.")
            return ""

        try:
            return self._compose(audio_path, video_content, article_id)
        except Exception as e:
            logger.error(f"Shorts composition failed: {e}")
            return ""

    # ── Internal ──────────────────────────────────────────────────────────────

    def _prepare_image_assets(self, image_url: str, article_id: str) -> tuple[Optional[str], Optional[str]]:
        """Downloads the image and creates a blurred background + bordered foreground."""
        try:
            import requests
            from PIL import Image, ImageFilter, ImageEnhance
            from io import BytesIO

            logger.info(f"Downloading image for Short: {image_url}")
            resp = requests.get(image_url, timeout=10)
            resp.raise_for_status()
            
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            w, h = img.size

            # 1. Blurred background image
            bg_w, bg_h = self.W, self.H
            scale = max(bg_w / w, bg_h / h)
            new_w, new_h = int(w * scale), int(h * scale)
            bg_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            left = (new_w - bg_w) // 2
            top = (new_h - bg_h) // 2
            bg_img = bg_img.crop((left, top, left + bg_w, top + bg_h))
            
            bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=25))
            enhancer = ImageEnhance.Brightness(bg_img)
            bg_img = enhancer.enhance(0.35)
            
            bg_path = str(self.output_dir / f"{article_id}_temp_bg.jpg")
            bg_img.save(bg_path, "JPEG")

            # 2. Bordered foreground image
            fg_w = 960
            fg_scale = fg_w / w
            fg_h = int(h * fg_scale)
            fg_img = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
            
            border_size = 6
            bordered_img = Image.new("RGB", (fg_w + 2 * border_size, fg_h + 2 * border_size), (255, 255, 255))
            bordered_img.paste(fg_img, (border_size, border_size))
            
            fg_path = str(self.output_dir / f"{article_id}_temp_fg.jpg")
            bordered_img.save(fg_path, "JPEG")

            return bg_path, fg_path
        except Exception as e:
            logger.warning(f"Could not prepare image assets for Short: {e}")
            return None, None

    def _compose(self, audio_path: str, vc, article_id: str) -> str:
        audio = AudioFileClip(audio_path)

        # Trim to Shorts limit
        if audio.duration > SHORTS_MAX_DURATION:
            audio = audio.subclipped(0, SHORTS_MAX_DURATION)
        duration = audio.duration

        # Mix in BGM at low volume
        bgm_path = "assets/bgm_news.wav"
        if not os.path.exists(bgm_path):
            bgm_path = "assets/bgm.wav"

        if os.path.exists(bgm_path):
            try:
                from moviepy import CompositeAudioClip
                from moviepy.audio.fx import MultiplyVolume, AudioLoop
                bgm = AudioFileClip(bgm_path)
                bgm = bgm.with_effects([MultiplyVolume(0.08)])
                bgm = bgm.with_effects([AudioLoop(duration=duration)])
                audio = CompositeAudioClip([bgm, audio])
            except Exception as e:
                logger.warning(f"Shorts BGM mix failed: {e}")

        # Prepare images if url exists
        bg_path = None
        fg_path = None
        image_url = getattr(vc, "_image_url", "")
        if image_url:
            bg_path, fg_path = self._prepare_image_assets(image_url, article_id)

        # Background: blurred image or vertical gradient
        if bg_path and os.path.exists(bg_path):
            bg = ImageClip(bg_path).with_duration(duration)
        else:
            bg = self._make_gradient_bg(duration)

        # Overlays
        overlays = []
        overlays += self._make_top_bar(duration)
        
        # Adjust vertical positions if we have an image
        if fg_path and os.path.exists(fg_path):
            # Centered image
            fg_clip = ImageClip(fg_path).with_duration(duration).with_position(("center", 480))
            overlays.append(fg_clip)
            
            # Draw headline higher up
            overlays += self._make_headline(vc.thumbnail_headline or vc.title, duration, headline_y=200, label_y=120)
            # Draw subtext lower down
            overlays += self._make_subtext_positioned(vc.thumbnail_subtext, duration, sub_y=1120)
        else:
            overlays += self._make_headline(vc.thumbnail_headline or vc.title, duration)
            overlays += self._make_subtext(vc.thumbnail_subtext, duration)

        overlays += self._make_source_badge(vc.source_name, duration)
        overlays += self._make_bottom_bar(duration)
        overlays += self._make_shorts_watermark(duration)

        main = CompositeVideoClip([bg] + overlays, size=(self.W, self.H)).with_duration(duration)
        main = main.with_audio(audio)

        out_path = str(self.output_dir / f"{article_id}_short.mp4")
        try:
            main.write_videofile(
                out_path,
                fps=self.FPS,
                codec="libx264",
                audio_codec="aac",
                temp_audiofile=str(self.output_dir / f"{article_id}_short_tmp.m4a"),
                remove_temp=True,
                logger=None,
            )
            logger.success(f"Short saved: {out_path}")
        finally:
            # Clean up temporary images
            if bg_path and os.path.exists(bg_path):
                try:
                    os.remove(bg_path)
                except Exception:
                    pass
            if fg_path and os.path.exists(fg_path):
                try:
                    os.remove(fg_path)
                except Exception:
                    pass
        return out_path

    # ── Visual Helpers ────────────────────────────────────────────────────────

    def _make_gradient_bg(self, duration: float) -> ColorClip:
        """Simulate a dark gradient: top dark navy → bottom deep red-black."""
        import numpy as np
        # Build a gradient frame
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        for y in range(self.H):
            t = y / self.H
            r = int(self.BG_TOP[0] * (1 - t) + self.BG_BOT[0] * t)
            g = int(self.BG_TOP[1] * (1 - t) + self.BG_BOT[1] * t)
            b = int(self.BG_TOP[2] * (1 - t) + self.BG_BOT[2] * t)
            frame[y, :] = [r, g, b]

        from moviepy import ImageClip as IC
        return IC(frame).with_duration(duration)

    def _get_font(self, bold=False) -> str:
        font = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
        if not os.path.exists(font):
            return "Arial"
        return font

    def _make_top_bar(self, duration: float) -> list:
        bar = ColorClip(size=(self.W, 12), color=self.ACCENT) \
            .with_duration(duration).with_position(("center", 0))
        return [bar]

    def _make_bottom_bar(self, duration: float) -> list:
        bar = ColorClip(size=(self.W, 12), color=self.ACCENT) \
            .with_duration(duration).with_position(("center", self.H - 12))
        return [bar]

    def _make_headline(self, headline: str, duration: float, headline_y: int = 260, label_y: int = 160) -> list:
        clips = []
        try:
            # "BREAKING NEWS" label
            label = TextClip(
                text="BREAKING NEWS", font_size=48, color="#dc1e1e",
                font=self._get_font(True), method="label",
            ).with_duration(duration).with_position(("center", label_y))
            clips.append(label)

            # Main headline (large, white, bold)
            wrapped = "\n".join(textwrap.wrap(headline, width=22))
            title = TextClip(
                text=wrapped, font_size=90, color="white",
                font=self._get_font(True), method="caption",
                size=(self.W - 80, None), text_align="center",
            ).with_duration(duration).with_position(("center", headline_y))
            clips.append(title)
        except Exception as e:
            logger.warning(f"Shorts headline failed: {e}")
        return clips

    def _make_subtext(self, subtext: str, duration: float) -> list:
        return self._make_subtext_positioned(subtext, duration, self.H // 2 + 10)

    def _make_subtext_positioned(self, subtext: str, duration: float, sub_y: int) -> list:
        if not subtext:
            return []
        clips = []
        try:
            # Divider line
            divider = ColorClip(size=(self.W - 160, 3), color=(180, 180, 180)) \
                .with_duration(duration).with_position(("center", sub_y - 20))
            clips.append(divider)

            wrapped = "\n".join(textwrap.wrap(subtext, width=34))
            sub = TextClip(
                text=wrapped, font_size=52, color="#cccccc",
                font=self._get_font(False), method="caption",
                size=(self.W - 120, None), text_align="center",
            ).with_duration(duration).with_position(("center", sub_y))
            clips.append(sub)
        except Exception as e:
            logger.warning(f"Shorts subtext failed: {e}")
        return clips

    def _make_source_badge(self, source_name: str, duration: float) -> list:
        if not source_name:
            return []
        clips = []
        try:
            # Red badge background
            badge_bg = ColorClip(size=(380, 60), color=self.ACCENT) \
                .with_duration(duration).with_position((40, self.H - 200))
            clips.append(badge_bg)

            src = TextClip(
                text=f"  {source_name.upper()}  ", font_size=32, color="white",
                font=self._get_font(True), method="label",
            ).with_duration(duration).with_position((40, self.H - 196))
            clips.append(src)
        except Exception as e:
            logger.warning(f"Shorts source badge failed: {e}")
        return clips

    def _make_shorts_watermark(self, duration: float) -> list:
        clips = []
        try:
            wm = TextClip(
                text="#Shorts", font_size=36, color="#888888",
                font=self._get_font(False), method="label",
            ).with_duration(duration).with_position((self.W - 200, self.H - 200))
            clips.append(wm)
        except Exception as e:
            logger.warning(f"Shorts watermark failed: {e}")
        return clips
