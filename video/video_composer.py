"""
video/video_composer.py
Assembles a broadcast-quality 16:9 news video:

Layout (top → bottom)
─────────────────────
  [Top bar] 4px red accent
  [Channel brand] top-left logo text
  [LIVE/BREAKING badge] top-right
  [Headline card] large white title, stays full video
  [Background] blurred/dimmed thumbnail or dark gradient
  [Script panel] rotating sentences from the narration script
                 white text on semi-transparent dark bar, synced to audio
  [Source badge] bottom-left, red pill
  [Ticker bar]  bottom, scrolling category + tags
  [Bottom bar]  4px red accent

Segments from vc.script_segments are timed to appear/disappear
so the on-screen text tracks the narration naturally.
"""

import os
import math
import re
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
    try:
        from moviepy.editor import (  # type: ignore[no-redef]
            AudioFileClip, ColorClip, CompositeVideoClip,
            TextClip, concatenate_videoclips, ImageClip,
        )
        import numpy as np  # type: ignore[no-redef]
        MOVIEPY_AVAILABLE = True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        class CompositeVideoClip: pass  # type: ignore[no-redef]
        class ColorClip: pass          # type: ignore[no-redef]
        class TextClip: pass           # type: ignore[no-redef]
        class ImageClip: pass          # type: ignore[no-redef]
        logger.warning("MoviePy not installed — video composition will be skipped.")


class VideoComposer:
    W   = 1920
    H   = 1080
    FPS = 24

    # Brand palette
    ACCENT     = (220, 30, 30)     # brand red
    BG_DARK    = (8, 10, 18)       # near-black navy
    PANEL_RGBA = (0, 0, 0, 180)    # semi-transparent overlay

    def __init__(self, output_dir: str = "./output/videos"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public ────────────────────────────────────────────────────────────────

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

    # ── Main composition ──────────────────────────────────────────────────────

    def _moviepy_compose(self, audio_path, thumbnail_path, vc, article_id) -> str:
        audio = AudioFileClip(audio_path)
        duration = audio.duration

        # ── BGM mix ──────────────────────────────────────────────────────────
        for bgm_candidate in ("assets/bgm_news.wav", "assets/bgm.wav"):
            if os.path.exists(bgm_candidate):
                try:
                    from moviepy import CompositeAudioClip
                    from moviepy.audio.fx import MultiplyVolume, AudioLoop
                    bgm = AudioFileClip(bgm_candidate)
                    bgm = bgm.with_effects([MultiplyVolume(0.08)])
                    bgm = bgm.with_effects([AudioLoop(duration=duration)])
                    audio = CompositeAudioClip([bgm, audio])
                    logger.debug("BGM mixed.")
                except Exception as e:
                    logger.warning(f"BGM mix failed: {e}")
                break

        channel_name = os.getenv("CHANNEL_NAME", "News Channel")
        clips = []

        # ── INTRO card (3 s) ─────────────────────────────────────────────────
        intro = self._make_intro_card(vc, channel_name, duration=3)
        clips.append(intro)

        # ── MAIN content section ─────────────────────────────────────────────
        main_clip = self._make_main_section(audio, thumbnail_path, vc, channel_name, duration)
        clips.append(main_clip)

        # ── OUTRO card (4 s) ─────────────────────────────────────────────────
        outro = self._make_outro_card(vc, channel_name, duration=4)
        clips.append(outro)

        final = concatenate_videoclips(clips, method="compose")

        out_path = str(self.output_dir / f"{article_id}.mp4")
        final.write_videofile(
            out_path,
            fps=self.FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(self.output_dir / f"{article_id}_tmp.m4a"),
            remove_temp=True,
            logger=None,
        )
        logger.success(f"Video saved: {out_path}")
        return out_path

    # ── Background ────────────────────────────────────────────────────────────

    def _make_background(self, thumbnail_path: str, duration: float):
        """
        Blurred & darkened thumbnail as a full-frame background.
        Falls back to a dark gradient if no thumbnail.
        """
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                from PIL import Image, ImageFilter, ImageEnhance
                import numpy as np

                img = Image.open(thumbnail_path).convert("RGB")
                # Scale to fill 1920×1080
                scale = max(self.W / img.width, self.H / img.height)
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                left = (new_w - self.W) // 2
                top  = (new_h - self.H) // 2
                img  = img.crop((left, top, left + self.W, top + self.H))
                # Blur + darken — keeps it as atmosphere, not distraction
                img = img.filter(ImageFilter.GaussianBlur(radius=18))
                img = ImageEnhance.Brightness(img).enhance(0.30)
                frame = np.array(img)
                return ImageClip(frame).with_duration(duration)
            except Exception as e:
                logger.warning(f"Background image processing failed: {e}")

        # Gradient fallback (dark navy → dark red)
        return self._make_gradient_bg(duration)

    def _make_gradient_bg(self, duration: float):
        import numpy as np
        frame = np.zeros((self.H, self.W, 3), dtype=np.uint8)
        top, bot = (8, 10, 18), (20, 5, 5)
        for y in range(self.H):
            t = y / self.H
            frame[y, :] = [int(top[i] * (1 - t) + bot[i] * t) for i in range(3)]
        return ImageClip(frame).with_duration(duration)

    # ── Main section ──────────────────────────────────────────────────────────

    def _make_main_section(self, audio, thumbnail_path, vc, channel_name, duration):
        """
        Full-duration section:
          bg | dark overlay | headline card | rotating script sentences | source badge | ticker
        """
        overlays = []

        # 1. Background (blurred thumbnail or gradient)
        bg = self._make_background(thumbnail_path, duration)

        # 2. Dark semi-transparent overlay so text is always readable
        try:
            import numpy as np
            overlay_frame = np.zeros((self.H, self.W, 4), dtype=np.uint8)
            overlay_frame[:, :, 3] = 170  # alpha ~67%
            dark_overlay = ImageClip(overlay_frame).with_duration(duration)
            overlays.append(dark_overlay)
        except Exception:
            pass

        # 3. Top accent bar
        overlays.append(
            ColorClip(size=(self.W, 6), color=self.ACCENT)
            .with_duration(duration).with_position(("center", 0))
        )

        # 4. Channel brand — top left
        overlays += self._make_channel_brand(channel_name, duration)

        # 5. LIVE/BREAKING badge — top right
        overlays += self._make_breaking_badge(vc.category, duration)

        # 6. Headline card — large title, sits in upper-mid zone
        overlays += self._make_headline_card(vc.title, duration)

        # 7. Script sentences — rotating in the lower half
        overlays += self._make_script_panel(vc, duration)

        # 8. Source badge — bottom left
        overlays += self._make_source_badge(vc.source_name, duration)

        # 9. Bottom accent bar
        overlays.append(
            ColorClip(size=(self.W, 6), color=self.ACCENT)
            .with_duration(duration).with_position(("center", self.H - 6))
        )

        main = CompositeVideoClip([bg] + overlays, size=(self.W, self.H)).with_duration(duration)
        return main.with_audio(audio)

    # ── Intro card ────────────────────────────────────────────────────────────

    def _make_intro_card(self, vc, channel_name, duration=3):
        bg = ColorClip(size=(self.W, self.H), color=self.BG_DARK).with_duration(duration)
        clips = [bg]

        clips.append(
            ColorClip(size=(self.W, 8), color=self.ACCENT)
            .with_duration(duration).with_position(("center", 0))
        )
        clips.append(
            ColorClip(size=(self.W, 8), color=self.ACCENT)
            .with_duration(duration).with_position(("center", self.H - 8))
        )

        try:
            # Channel name (top)
            clips.append(
                TextClip(
                    text=channel_name.upper(), font_size=38, color="#dc1e1e",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position(("center", 90))
            )

            # Red divider
            clips.append(
                ColorClip(size=(600, 3), color=self.ACCENT)
                .with_duration(duration).with_position(("center", 155))
            )

            # Category pill
            cat = vc.category.upper() if vc.category else "NEWS"
            clips.append(
                TextClip(
                    text=f"  {cat}  ", font_size=26, color="white",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position(("center", 175))
            )

            # Main title (large, centered)
            wrapped = "\n".join(textwrap.wrap(vc.title, width=52))
            clips.append(
                TextClip(
                    text=wrapped, font_size=68, color="white",
                    font=self._get_font(bold=True), method="caption",
                    size=(self.W - 160, None), text_align="center",
                ).with_duration(duration).with_position(("center", "center"))
            )

            # Thumbnail subtext below title
            if vc.thumbnail_subtext:
                clips.append(
                    TextClip(
                        text=vc.thumbnail_subtext, font_size=34, color="#cccccc",
                        font=self._get_font(bold=False), method="label",
                    ).with_duration(duration).with_position(("center", self.H - 120))
                )
        except Exception as e:
            logger.warning(f"Intro card text failed: {e}")

        return CompositeVideoClip(clips, size=(self.W, self.H)).with_duration(duration)

    # ── Overlay elements ──────────────────────────────────────────────────────

    def _make_channel_brand(self, channel_name, duration):
        clips = []
        try:
            # Red brand bar
            clips.append(
                ColorClip(size=(320, 48), color=self.ACCENT)
                .with_duration(duration).with_position((40, 24))
            )
            clips.append(
                TextClip(
                    text=f"  {channel_name.upper()}  ", font_size=24, color="white",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position((40, 28))
            )
        except Exception as e:
            logger.warning(f"Channel brand failed: {e}")
        return clips

    def _make_breaking_badge(self, category, duration):
        clips = []
        try:
            cat = (category or "NEWS").upper()
            label = "● BREAKING NEWS" if cat in ("INDIA", "WORLD", "NATIONAL") else f"● {cat} NEWS"
            clips.append(
                TextClip(
                    text=label, font_size=26, color="#ff4444",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position((self.W - 400, 36))
            )
        except Exception as e:
            logger.warning(f"Breaking badge failed: {e}")
        return clips

    def _make_headline_card(self, title, duration):
        """Large white headline on a semi-transparent dark pill, upper-center."""
        clips = []
        try:
            wrapped = "\n".join(textwrap.wrap(title, width=58))
            n_lines = wrapped.count("\n") + 1
            panel_h = 60 + n_lines * 80
            panel_y = 160

            # Dark panel behind headline
            clips.append(
                ColorClip(size=(self.W - 80, panel_h), color=(0, 0, 0))
                .with_duration(duration).with_position((40, panel_y - 10))
            )
            # Red left accent stripe
            clips.append(
                ColorClip(size=(8, panel_h), color=self.ACCENT)
                .with_duration(duration).with_position((40, panel_y - 10))
            )
            # Title text
            clips.append(
                TextClip(
                    text=wrapped, font_size=72, color="white",
                    font=self._get_font(bold=True), method="caption",
                    size=(self.W - 160, None), text_align="left",
                ).with_duration(duration).with_position((80, panel_y))
            )
        except Exception as e:
            logger.warning(f"Headline card failed: {e}")
        return clips

    def _make_script_panel(self, vc, total_duration):
        """
        Rotate through script sentences in the lower half of the screen.
        Each sentence shows for (total_duration / n_sentences) seconds.
        A semi-transparent dark panel keeps text readable over the bg.
        """
        clips = []
        try:
            # Get sentences from segments or split the script directly
            if vc.script_segments:
                sentences = [
                    s["text"] for s in vc.script_segments
                    if s.get("text", "").strip()
                ]
            else:
                raw = re.split(r'(?<=[.!?])\s+', vc.script.strip())
                sentences = [s.strip() for s in raw if s.strip()]

            if not sentences:
                return clips

            n = len(sentences)
            seg_dur = total_duration / n
            panel_y = self.H - 220
            panel_h = 170

            # Static dark panel background
            clips.append(
                ColorClip(size=(self.W, panel_h), color=(5, 5, 10))
                .with_duration(total_duration).with_position(("center", panel_y))
            )
            # Top accent on panel
            clips.append(
                ColorClip(size=(self.W, 3), color=self.ACCENT)
                .with_duration(total_duration).with_position(("center", panel_y))
            )

            # "ON AIR" label left side
            clips.append(
                TextClip(
                    text="ON AIR", font_size=20, color="#dc1e1e",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(total_duration).with_position((52, panel_y + 12))
            )

            # Each sentence appears for seg_dur seconds
            for i, sent in enumerate(sentences):
                t_start = i * seg_dur
                t_end   = t_start + seg_dur

                # Wrap text
                wrapped = "\n".join(textwrap.wrap(sent, width=88))

                try:
                    txt = TextClip(
                        text=wrapped, font_size=42, color="white",
                        font=self._get_font(bold=False), method="caption",
                        size=(self.W - 140, None), text_align="left",
                    ).with_start(t_start).with_end(t_end).with_position((70, panel_y + 36))
                    clips.append(txt)
                except Exception as e:
                    logger.debug(f"Script sentence {i} render failed: {e}")

        except Exception as e:
            logger.warning(f"Script panel failed: {e}")
        return clips

    def _make_source_badge(self, source_name, duration):
        """Red pill badge at bottom-left showing the news source."""
        clips = []
        if not source_name:
            return clips
        try:
            badge_w = max(260, len(source_name) * 18)
            clips.append(
                ColorClip(size=(badge_w, 44), color=self.ACCENT)
                .with_duration(duration).with_position((52, self.H - 170))
            )
            clips.append(
                TextClip(
                    text=f"  {source_name.upper()}  ",
                    font_size=26, color="white",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position((52, self.H - 166))
            )
        except Exception as e:
            logger.warning(f"Source badge failed: {e}")
        return clips

    # ── Outro card ────────────────────────────────────────────────────────────

    def _make_outro_card(self, vc, channel_name, duration=4):
        bg = ColorClip(size=(self.W, self.H), color=(5, 5, 15)).with_duration(duration)
        clips = [bg]

        clips.append(
            ColorClip(size=(self.W, 8), color=self.ACCENT)
            .with_duration(duration).with_position(("center", 0))
        )
        clips.append(
            ColorClip(size=(self.W, 8), color=self.ACCENT)
            .with_duration(duration).with_position(("center", self.H - 8))
        )

        try:
            clips.append(
                TextClip(
                    text=channel_name.upper(), font_size=52, color="#dc1e1e",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position(("center", self.H // 2 - 130))
            )

            clips.append(
                ColorClip(size=(500, 3), color=self.ACCENT)
                .with_duration(duration).with_position(("center", self.H // 2 - 60))
            )

            clips.append(
                TextClip(
                    text="LIKE  ·  SUBSCRIBE  ·  TURN ON NOTIFICATIONS",
                    font_size=40, color="white",
                    font=self._get_font(bold=True), method="label",
                ).with_duration(duration).with_position(("center", self.H // 2 - 40))
            )

            if vc.source_name:
                clips.append(
                    TextClip(
                        text=f"Reported by  {vc.source_name}",
                        font_size=28, color="#888888",
                        font=self._get_font(bold=False), method="label",
                    ).with_duration(duration).with_position(("center", self.H // 2 + 60))
                )

            clips.append(
                TextClip(
                    text="Stay informed. Stay ahead.",
                    font_size=32, color="#cccccc",
                    font=self._get_font(bold=False), method="label",
                ).with_duration(duration).with_position(("center", self.H // 2 + 100))
            )
        except Exception as e:
            logger.warning(f"Outro text failed: {e}")

        return CompositeVideoClip(clips, size=(self.W, self.H)).with_duration(duration)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_font(self, bold=False):
        font = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
        if not os.path.exists(font):
            return "Arial"
        return font

    # ── Fallback ──────────────────────────────────────────────────────────────

    def _placeholder_compose(self, audio_path: str, article_id: str) -> str:
        import shutil
        out_path = str(self.output_dir / f"{article_id}_audio_only.mp3")
        shutil.copy(audio_path, out_path)
        logger.warning(f"MoviePy unavailable — saved audio only: {out_path}")
        return out_path
