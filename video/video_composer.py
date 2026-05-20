"""
video/video_composer.py
Assembles the final news video from:
  - TTS audio segments
  - Animated title card (intro)
  - Text-on-screen lower thirds
  - Background visuals
  - Outro card

Uses MoviePy for composition.
"""

import os
import math
import textwrap
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    # MoviePy v2 (current) — direct imports
    from moviepy import (
        AudioFileClip, ColorClip, CompositeVideoClip,
        TextClip, concatenate_videoclips,
        ImageClip,
    )
    import numpy as np
    MOVIEPY_AVAILABLE = True
except ImportError:
    try:
        # MoviePy v1 fallback — uses moviepy.editor
        from moviepy.editor import (  # type: ignore[no-redef]
            AudioFileClip, ColorClip, CompositeVideoClip,
            TextClip, concatenate_videoclips,
            ImageClip,
        )
        import numpy as np  # type: ignore[no-redef]
        MOVIEPY_AVAILABLE = True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        # Stub types so class body / type hints don't raise NameError
        class CompositeVideoClip: pass  # type: ignore[no-redef]
        class ColorClip: pass          # type: ignore[no-redef]
        class TextClip: pass           # type: ignore[no-redef]
        class ImageClip: pass          # type: ignore[no-redef]
        logger.warning("MoviePy not installed — video composition will be skipped.")


class VideoComposer:
    W = 1920
    H = 1080
    FPS = 24

    # Brand colors
    ACCENT   = (220, 30, 30)    # red
    BG_DARK  = (10, 12, 20)
    TEXT_W   = (255, 255, 255)
    TEXT_Y   = (255, 200, 0)

    def __init__(self, output_dir: str = "./output/videos"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compose(
        self,
        audio_path: str,
        thumbnail_path: str,
        video_content,
        article_id: str,
    ) -> str:
        """Compose full video. Returns path to MP4."""
        if not MOVIEPY_AVAILABLE:
            return self._placeholder_compose(audio_path, article_id)

        try:
            return self._moviepy_compose(audio_path, thumbnail_path, video_content, article_id)
        except Exception as e:
            logger.error(f"Video composition failed: {e}")
            return self._placeholder_compose(audio_path, article_id)

    # ── MoviePy Composition ───────────────────────────────────────────────────

    def _moviepy_compose(self, audio_path, thumbnail_path, vc, article_id) -> str:
        audio = AudioFileClip(audio_path)
        
        bgm_path = "assets/bgm_news.wav"
        if not os.path.exists(bgm_path):
            bgm_path = "assets/bgm.wav"

        if os.path.exists(bgm_path):
            try:
                from moviepy import CompositeAudioClip
                from moviepy.audio.fx import MultiplyVolume, AudioLoop
                bgm = AudioFileClip(bgm_path)
                bgm = bgm.with_effects([MultiplyVolume(0.1)])
                bgm = bgm.with_effects([AudioLoop(duration=audio.duration)])
                audio = CompositeAudioClip([bgm, audio])
                logger.debug("BGM mixed successfully.")
            except Exception as e:
                logger.warning(f"Could not mix BGM: {e}")
                
        duration = audio.duration

        clips = []

        # ── 1. INTRO card (3 sec) ─────────────────────────────────────────
        intro = self._make_intro_card(vc.title, duration=3)
        clips.append(intro)

        # ── 2. Main content: thumbnail/bg with lower-third text ───────────
        if os.path.exists(thumbnail_path):
            bg = ImageClip(thumbnail_path).with_duration(duration).resized((self.W, self.H))
        else:
            bg = ColorClip(size=(self.W, self.H), color=self.BG_DARK).with_duration(duration)

        # Lower third bar with scrolling script text
        lower_clips = self._make_lower_thirds(vc, duration)
        main_clip = CompositeVideoClip([bg] + lower_clips).with_duration(duration)
        main_clip = main_clip.with_audio(audio)
        clips.append(main_clip)

        # ── 3. OUTRO card (4 sec) ─────────────────────────────────────────
        outro = self._make_outro_card(vc.source_name, duration=4)
        clips.append(outro)

        final = concatenate_videoclips(clips, method="compose")

        out_path = str(self.output_dir / f"{article_id}.mp4")
        final.write_videofile(
            out_path,
            fps=self.FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(self.output_dir / f"{article_id}_temp_audio.m4a"),
            remove_temp=True,
            logger=None,
        )
        logger.success(f"Video saved: {out_path}")
        return out_path

    def _get_font(self, bold=False):
        font = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
        if not os.path.exists(font):
            return "Arial"  # Let MoviePy/ImageMagick attempt to find it natively
        return font

    def _make_intro_card(self, title: str, duration: float = 3) -> CompositeVideoClip:
        bg = ColorClip(size=(self.W, self.H), color=self.BG_DARK).with_duration(duration)

        # Red top bar
        top_bar = ColorClip(size=(self.W, 8), color=self.ACCENT).with_duration(duration).with_position(("center", 0))
        bot_bar = ColorClip(size=(self.W, 8), color=self.ACCENT).with_duration(duration).with_position(("center", self.H - 8))

        # Channel name
        ch_clip = TextClip(
            text="NEWS CHANNEL", font_size=36, color="white",
            font=self._get_font(True), method="caption", size=(self.W - 100, None)
        ).with_duration(duration).with_position(("center", 100))

        # Title text (wrapped)
        wrapped = "\n".join(textwrap.wrap(title, width=55))
        title_clip = TextClip(
            text=wrapped, font_size=62, color="white",
            font=self._get_font(True), method="caption", size=(self.W - 200, None),
            text_align="center"
        ).with_duration(duration).with_position("center")

        return CompositeVideoClip([bg, top_bar, bot_bar, ch_clip, title_clip]).with_duration(duration)

    def _make_lower_thirds(self, vc, total_duration: float) -> list:
        clips = []
        # Scrolling headline ticker at bottom
        ticker_text = f"  {vc.title}  |  Source: {vc.source_name}  |  {vc.category.upper()}  "

        try:
            ticker_bg = ColorClip(size=(self.W, 60), color=self.ACCENT) \
                .with_duration(total_duration) \
                .with_position(("center", self.H - 60))
            clips.append(ticker_bg)

            ticker = TextClip(
                text=ticker_text, font_size=28, color="white",
                font=self._get_font(True), method="label"
            ).with_duration(total_duration).with_position(lambda t: (self.W - (t * 120) % (self.W + 1200), self.H - 55))
            clips.append(ticker)
        except Exception as e:
            logger.warning(f"Failed to make lower thirds: {e}")

        return clips

    def _make_outro_card(self, source_name: str, duration: float = 4) -> CompositeVideoClip:
        bg = ColorClip(size=(self.W, self.H), color=(5, 5, 15)).with_duration(duration)
        bar = ColorClip(size=(self.W, 6), color=self.ACCENT).with_duration(duration).with_position(("center", self.H // 2 - 60))

        try:
            sub_clip = TextClip(
                text="LIKE · SUBSCRIBE · SHARE", font_size=52,
                color="white", font=self._get_font(True), method="label"
            ).with_duration(duration).with_position(("center", self.H // 2 - 30))

            src_clip = TextClip(
                text=f"Source: {source_name}", font_size=30,
                color="#aaaaaa", font=self._get_font(False), method="label"
            ).with_duration(duration).with_position(("center", self.H // 2 + 60))

            return CompositeVideoClip([bg, bar, sub_clip, src_clip]).with_duration(duration)
        except Exception as e:
            logger.warning(f"Failed to make outro card: {e}")
            return bg

    # ── Fallback (no MoviePy) ─────────────────────────────────────────────────

    def _placeholder_compose(self, audio_path: str, article_id: str) -> str:
        """
        When MoviePy is unavailable, just rename/copy the audio as a placeholder.
        In production this should never trigger.
        """
        import shutil
        out_path = str(self.output_dir / f"{article_id}_audio_only.mp3")
        shutil.copy(audio_path, out_path)
        logger.warning(f"MoviePy unavailable — saved audio only: {out_path}")
        return out_path
