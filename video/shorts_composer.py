# -*- coding: utf-8 -*-
"""
video/shorts_composer.py
Generates a 9:16 vertical YouTube Short (25–59 seconds) for individual news stories.

Layout:
  - Animated particle / gradient background (Ken-Burns zoom)
  - Kinetic word-by-word headline reveal (top section)
  - Slide-in animated lower-third panel with script sentences
  - Animated source badge (bounces in from left)
  - Pulsing red accent bars (top & bottom)
  - Scrolling ticker at bottom
  - TTS audio + looped BGM underneath
"""

import os
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
    MOVIEPY_AVAILABLE = False
    logger.warning("MoviePy not installed — Shorts composition will be skipped.")

# 2D animation primitives
try:
    from video.animation_engine import (
        particle_overlay,
        pulse_bar,
        slide_in_panel,
        kinetic_word_reveal,
        animated_badge,
        flash_transition,
        zoom_scale_clip,
        scrolling_ticker,
        scanline_overlay,
        segment_progress_bar,
        emoji_burst,
        rolling_counter,
        highlight_sweep,
        animated_gradient_bg,
        lower_third_card,
        text_pop_in,
    )
    ANIMATION_OK = True
except Exception as _ae:
    logger.warning(f"animation_engine not available: {_ae}")
    ANIMATION_OK = False


# ---------------------------------------------------------------------------
# Headline emoji auto-detection
# ---------------------------------------------------------------------------

_HEADLINE_EMOJI_RULES = [
    # (list-of-trigger-keywords, emoji-char, label-text, delay)
    (["dead", "killed", "death", "deaths", "casualties", "war", "attack", "blast", "bomb", "terror"],
     "🔴", "BREAKING", 0.0),
    (["arrest", "arrested", "jailed", "sentenced", "verdict", "conviction", "raid"],
     "⚠️", "ALERT", 0.0),
    (["earthquake", "flood", "cyclone", "storm", "disaster", "emergency", "evacuate"],
     "🆘", "EMERGENCY", 0.0),
    (["election", "vote", "poll", "result", "winner", "won", "loses", "defeat"],
     "🗳️", "ELECTION", 0.1),
    (["stock", "market", "sensex", "nifty", "crash", "surge", "rally", "economy"],
     "📈", "MARKETS", 0.1),
    (["live", "happening", "ongoing", "now", "breaking"],
     "🔴", "LIVE", 0.0),
    (["exclusive", "reveal", "leaked", "exposed", "secret"],
     "📢", "EXCLUSIVE", 0.05),
    (["record", "historic", "first time", "milestone", "achievement"],
     "🏆", "MILESTONE", 0.1),
]


def _detect_emoji_for_headline(headline: str):
    """
    Returns (emoji_char, label, delay) if a keyword match is found,
    else (None, None, None).
    """
    hl_lower = headline.lower()
    for keywords, char, label, delay in _HEADLINE_EMOJI_RULES:
        if any(kw in hl_lower for kw in keywords):
            return char, label, delay
    # Default: generic breaking news
    return "⚡", "NEWS", 0.0


# ---------------------------------------------------------------------------
# Number / stat auto-detection for rolling counter
# ---------------------------------------------------------------------------

import re as _re

_NUM_PATTERNS = [
    # ₹ / $ / £ + number + optional unit
    (_re.compile(
        r'([₹\$£€]\s?)(\d[\d,\.]*)(\s?(?:crore|cr|lakh|lk|million|mn|billion|bn|k))?',
        _re.IGNORECASE
    ), "currency"),
    # plain number + % 
    (_re.compile(r'(\d[\d,\.]+)\s*(%)', _re.IGNORECASE), "percent"),
    # large plain numbers (≥ 4 digits) with optional unit
    (_re.compile(
        r'\b(\d{1,3}(?:,\d{3})+|\d{4,})(\s?(?:crore|cr|lakh|lk|million|mn|billion|bn))?\b',
        _re.IGNORECASE
    ), "plain"),
]


def _extract_number_for_counter(text: str):
    """
    Scan `text` for the first prominent number and return
    (end_val: float, prefix: str, suffix: str) or None.
    """
    for pattern, kind in _NUM_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                groups = m.groups()
                if kind == "currency":
                    sym   = (groups[0] or "").strip()
                    raw   = (groups[1] or "").replace(",", "")
                    unit  = (groups[2] or "").strip()
                    val   = float(raw)
                    return val, sym + " ", (" " + unit) if unit else ""
                elif kind == "percent":
                    raw = (groups[0] or "").replace(",", "")
                    val = float(raw)
                    return val, "", "%"
                elif kind == "plain":
                    raw  = (groups[0] or "").replace(",", "")
                    unit = (groups[1] or "").strip()
                    val  = float(raw)
                    if val >= 1000:
                        return val, "", (" " + unit) if unit else ""
            except Exception:
                continue
    return None


SHORTS_MAX_DURATION = 59.0   # Individual news shorts: up to 59 s

# ── Strict bottom-zone layout constants (all relative to H=1920) ───────────
_TICKER_H       = 68    # scrolling ticker height
_TICKER_Y       = 1840  # ticker top edge  (H - _TICKER_H - 12)
_PROGBAR_H      = 6     # progress bar height
_PROGBAR_Y      = 1832  # progress bar top (just above ticker)
_BADGE_H        = 58    # source badge height
_BADGE_Y        = 1768  # badge top edge   (_PROGBAR_Y - _BADGE_H - 6)
_WATERMARK_Y    = 1768  # watermark aligns with badge


class ShortsComposer:
    """Produces a 1080×1920 (9:16) Short from TTS audio + animated 2D overlays."""

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

    def _prepare_image_assets(self, image_url, article_id):
        """Downloads the image and creates a blurred background + bordered foreground."""
        try:
            import requests
            from PIL import Image, ImageFilter, ImageEnhance
            from io import BytesIO

            logger.info(f"Downloading image for Short: {image_url}")
            resp = requests.get(image_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            w, h = img.size

            # 1. Blurred background image
            bg_w, bg_h = self.W, self.H
            scale = max(bg_w / w, bg_h / h)
            new_w, new_h = int(w * scale), int(h * scale)
            bg_img = img.resize((new_w, new_h), Image.LANCZOS)
            
            left = (new_w - bg_w) // 2
            top = (new_h - bg_h) // 2
            bg_img = bg_img.crop((left, top, left + bg_w, top + bg_h))
            
            bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=25))
            enhancer = ImageEnhance.Brightness(bg_img)
            bg_img = enhancer.enhance(0.30)
            
            bg_path = str(self.output_dir / f"{article_id}_temp_bg.jpg")
            bg_img.save(bg_path, "JPEG")

            # 2. Bordered foreground image
            fg_w = 960
            fg_scale = fg_w / w
            fg_h = int(h * fg_scale)
            fg_img = img.resize((fg_w, fg_h), Image.LANCZOS)
            
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

        # ── Background ────────────────────────────────────────────────────────
        if bg_path and os.path.exists(bg_path):
            bg_base = ImageClip(bg_path).with_duration(duration)
            # Ken-Burns slow zoom on background
            if ANIMATION_OK:
                try:
                    bg_base = zoom_scale_clip(bg_base, zoom_start=1.0, zoom_end=1.06)
                except Exception as e:
                    logger.debug(f"Ken-Burns zoom failed: {e}")
        else:
            # Animated gradient — much better than static colour
            if ANIMATION_OK:
                try:
                    bg_base = animated_gradient_bg(
                        duration=duration, W=self.W, H=self.H, fps=self.FPS,
                        top_color=self.BG_TOP, bot_color=self.BG_BOT,
                        hue_shift_deg=18,
                    )
                except Exception:
                    bg_base = self._make_gradient_bg(duration)
            else:
                bg_base = self._make_gradient_bg(duration)

        overlays = []

        # ── Intro flash (broadcast cut-in feel) ───────────────────────────────
        if ANIMATION_OK:
            try:
                flash = flash_transition(duration=0.22, W=self.W, H=self.H,
                                        color=self.ACCENT, fps=self.FPS)
                overlays.append(flash)
            except Exception as e:
                logger.debug(f"Intro flash failed: {e}")

        # ── Dark readability overlay ──────────────────────────────────────────
        try:
            overlay_frame = np.zeros((self.H, self.W, 4), dtype=np.uint8)
            overlay_frame[:, :, 3] = 150
            overlays.append(ImageClip(overlay_frame).with_duration(duration))
        except Exception:
            pass

        # ── Particle energy overlay ───────────────────────────────────────────
        if ANIMATION_OK:
            try:
                particles = particle_overlay(
                    duration=duration, W=self.W, H=self.H, fps=self.FPS,
                    n_particles=40, color=self.ACCENT,
                )
                overlays.append(particles)
            except Exception as e:
                logger.debug(f"Particle overlay failed: {e}")

        # ── Scanline overlay (CRT broadcast texture + subtle glitch) ──────────
        if ANIMATION_OK:
            try:
                sl = scanline_overlay(
                    duration=duration, W=self.W, H=self.H, fps=self.FPS,
                    line_alpha=14, glitch_alpha=45,
                    glitch_interval=8.0, glitch_dur=0.07,
                )
                overlays.append(sl)
            except Exception as e:
                logger.debug(f"Scanline overlay failed: {e}")

        # ── Pulsing accent bars (top & bottom) ────────────────────────────────
        if ANIMATION_OK:
            try:
                overlays.append(
                    pulse_bar(duration, W=self.W, bar_h=12, color=self.ACCENT,
                              y_pos=0, fps=self.FPS, pulse_hz=1.0)
                )
                overlays.append(
                    pulse_bar(duration, W=self.W, bar_h=12, color=self.ACCENT,
                              y_pos=self.H - 12, fps=self.FPS, pulse_hz=1.0)
                )
            except Exception as e:
                logger.debug(f"Pulse bars failed: {e}")
        else:
            overlays += self._make_top_bar(duration)
            overlays += self._make_bottom_bar(duration)

        # ── Foreground image inset ────────────────────────────────────────────
        if fg_path and os.path.exists(fg_path):
            fg_clip = ImageClip(fg_path).with_duration(duration)
            # Apply Ken-Burns zoom to foreground image too
            if ANIMATION_OK:
                try:
                    fg_clip = zoom_scale_clip(fg_clip, zoom_start=1.0, zoom_end=1.04)
                except Exception as e:
                    logger.debug(f"FG zoom failed: {e}")
            fg_clip = fg_clip.with_position(("center", 480))
            overlays.append(fg_clip)
            headline_y = 200
            label_y = 120
        else:
            headline_y = 260
            label_y = 160

        # ── Kinetic headline (word-by-word reveal) ────────────────────────────
        headline = vc.thumbnail_headline or vc.title
        if ANIMATION_OK:
            try:
                words = headline.split()
                word_dur = min(duration, len(words) * 0.5 + 1.0)
                kinetic = kinetic_word_reveal(
                    words=words,
                    duration=word_dur,
                    W=self.W, H=self.H,
                    font_size=78,
                    text_color=(255, 255, 255),
                    highlight_color=self.ACCENT,
                    y_center=label_y + (headline_y - label_y) // 2 + 100,
                    fps=self.FPS,
                )
                overlays.append(kinetic)

                # ── Highlight sweep: underline-only, no text redraw ───────────
                try:
                    n_words = len(words)
                    word_interval = word_dur / max(n_words, 1)
                    word_timings = [i * word_interval for i in range(n_words)]
                    sweep = highlight_sweep(
                        words=words,
                        word_timings=word_timings,
                        duration=word_dur,
                        W=self.W,
                        font_size=78,
                        y_center=label_y + (headline_y - label_y) // 2 + 100,
                        fps=self.FPS,
                        underline_color=self.ACCENT,
                    )
                    overlays.append(sweep)
                except Exception as e:
                    logger.debug(f"Highlight sweep failed: {e}")

            except Exception as e:
                logger.debug(f"Kinetic headline failed: {e}, falling back")
                overlays += self._make_headline(headline, duration, headline_y, label_y)
        else:
            overlays += self._make_headline(headline, duration, headline_y, label_y)

        # ── Emoji burst badge (auto-detected from headline) ───────────────────
        if ANIMATION_OK:
            try:
                emoji_char, emoji_label, emoji_delay = _detect_emoji_for_headline(headline)
                burst = emoji_burst(
                    char=emoji_char,
                    duration=duration,
                    x=self.W // 2,
                    y=label_y,
                    font_size=64,
                    fps=self.FPS,
                    delay=emoji_delay,
                    pulse_hz=0.7,
                )
                overlays.append(burst)
                overlays.append(
                    TextClip(
                        text=f"  {emoji_label}", font_size=40, color="#dc1e1e",
                        font=self._get_font(True), method="label",
                    ).with_duration(duration).with_position(("center", label_y + 4))
                )
            except Exception as e:
                logger.debug(f"Emoji burst failed: {e}")
                try:
                    overlays.append(
                        TextClip(
                            text="● BREAKING NEWS", font_size=44, color="#dc1e1e",
                            font=self._get_font(True), method="label",
                        ).with_duration(duration).with_position(("center", label_y))
                    )
                except Exception:
                    pass
        else:
            try:
                overlays.append(
                    TextClip(
                        text="● BREAKING NEWS", font_size=44, color="#dc1e1e",
                        font=self._get_font(True), method="label",
                    ).with_duration(duration).with_position(("center", label_y))
                )
            except Exception as e:
                logger.debug(f"Breaking label failed: {e}")

        # ── Animated lower-third script cards ─────────────────────────────────
        overlays += self._make_animated_script_panel(vc, duration)

        # ── Animated source badge (above progress bar) ────────────────────────
        if ANIMATION_OK and vc.source_name:
            try:
                badge = animated_badge(
                    label=vc.source_name,
                    duration=duration,
                    x_final=40,
                    y_pos=_BADGE_Y,
                    badge_h=_BADGE_H,
                    font_size=30,
                    bg_color=self.ACCENT,
                    fps=self.FPS,
                    delay=0.5,
                )
                overlays.append(badge)
            except Exception as e:
                logger.debug(f"Animated badge failed: {e}")
                overlays += self._make_source_badge(vc.source_name, duration)
        else:
            overlays += self._make_source_badge(vc.source_name, duration)

        # ── Stat pop-in (auto-detect number from script) ──────────────────────
        if ANIMATION_OK:
            try:
                script_text = getattr(vc, "short_script", "") or vc.script[:400]
                num_info = _extract_number_for_counter(script_text)
                if num_info:
                    end_val, prefix, suffix = num_info
                    stat_text = f"{prefix}{end_val:,.0f}{suffix}".strip()
                    stat = text_pop_in(
                        text=stat_text,
                        duration=min(duration, 5.0),
                        W=self.W,
                        font_size=84,
                        x=self.W // 2,
                        y=880,          # stat card zone — above script panel
                        fps=self.FPS,
                        text_color=(255, 220, 50),
                        bg_color=(10, 10, 20),
                        bg_alpha=210,
                        delay=0.6,
                    )
                    overlays.append(stat)
            except Exception as e:
                logger.debug(f"Stat pop-in failed: {e}")

        # ── Segment progress bar (just above ticker) ──────────────────────────
        if ANIMATION_OK:
            try:
                prog_bar = segment_progress_bar(
                    duration=duration,
                    W=self.W,
                    bar_h=_PROGBAR_H,
                    fps=self.FPS,
                    fill_color=self.ACCENT,
                    bg_color=(60, 10, 10),
                    y_pos=_PROGBAR_Y,
                    glow=True,
                )
                overlays.append(prog_bar)
            except Exception as e:
                logger.debug(f"Segment progress bar failed: {e}")

        # ── Scrolling ticker ──────────────────────────────────────────────────
        if ANIMATION_OK:
            ticker_text = vc.title or headline
            try:
                ticker = scrolling_ticker(
                    text=ticker_text,
                    duration=duration,
                    W=self.W,
                    ticker_h=_TICKER_H,
                    y_pos=_TICKER_Y,
                    font_size=34,
                    bg_color=(180, 10, 10),
                    scroll_speed=210,
                    fps=self.FPS,
                )
                overlays.append(ticker)
            except Exception as e:
                logger.debug(f"Scrolling ticker failed: {e}")

        # ── Shorts watermark ──────────────────────────────────────────────────
        overlays += self._make_shorts_watermark(duration)

        # ── Compose ───────────────────────────────────────────────────────────
        main = CompositeVideoClip([bg_base] + overlays, size=(self.W, self.H)).with_duration(duration)
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
            for p in (bg_path, fg_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        return out_path

    # ── Animated script panel ─────────────────────────────────────────────────

    def _make_animated_script_panel(self, vc, duration: float) -> list:
        """
        Each script sentence appears as a lower_third_card broadcast card.
        Cards slide up from below sequentially — one per sentence segment.
        Falls back to slide_in_panel if lower_third_card unavailable.
        """
        clips = []
        try:
            text = getattr(vc, "short_script", "") or vc.script[:400]
            raw = re.split(r'(?<=[.!?])\s+', text.strip())
            sentences = [s.strip() for s in raw if s.strip()]
            if not sentences:
                return clips

            n = len(sentences)
            seg_dur = duration / n
            # Card sits at y=1080 (lower half, above badge zone)
            card_y  = 1060
            card_h  = 280
            source_label = getattr(vc, "source_name", None) or "NEWS"

            if ANIMATION_OK:
                for i, sent in enumerate(sentences):
                    t_start = i * seg_dur
                    seg_d   = seg_dur
                    try:
                        card = lower_third_card(
                            title_text=source_label,
                            body_text=sent,
                            duration=seg_d,
                            W=self.W,
                            card_h=card_h,
                            y_pos=card_y,
                            fps=self.FPS,
                            bg_color=(5, 5, 18),
                            accent_color=self.ACCENT,
                            title_color=self.ACCENT,
                            body_color=(255, 255, 255),
                            slide_in_dur=0.26,
                            font_path=self._get_font(True),
                            font_path_regular=self._get_font(False),
                        )
                        clips.append(card.with_start(t_start))
                    except Exception as e:
                        logger.debug(f"lower_third_card {i} failed: {e}")
                        clips += self._static_sentence_clip(sent, t_start, seg_d, card_y + 30)
            else:
                clips += self._make_script_sentences(vc, duration)

        except Exception as e:
            logger.warning(f"Animated script panel failed: {e}")
        return clips

    def _static_sentence_clip(self, text: str, t_start: float, seg_dur: float, panel_y: int) -> list:
        """Fallback static text clip for a single sentence."""
        clips = []
        try:
            wrapped = "\n".join(textwrap.wrap(text, width=28))
            txt = TextClip(
                text=wrapped, font_size=60, color="white",
                font=self._get_font(True), method="caption",
                size=(self.W - 80, None), text_align="center",
            ).with_start(t_start).with_duration(seg_dur).with_position(("center", panel_y + 24))
            clips.append(txt)
        except Exception as e:
            logger.debug(f"Static sentence clip failed: {e}")
        return clips

    # ── Visual Helpers ────────────────────────────────────────────────────────

    def _make_gradient_bg(self, duration: float) -> ColorClip:
        """Simulate a dark gradient: top dark navy → bottom deep red-black."""
        import numpy as np
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

            # Main headline (large, white, bold with stroke)
            wrapped = "\n".join(textwrap.wrap(headline, width=28))
            title = TextClip(
                text=wrapped, font_size=65, color="white",
                stroke_color="black", stroke_width=3,
                font=self._get_font(True), method="caption",
                size=(self.W - 80, None), text_align="center",
            ).with_duration(duration).with_position(("center", headline_y))
            clips.append(title)
        except Exception as e:
            logger.warning(f"Shorts headline failed: {e}")
        return clips

    def _make_script_sentences(self, vc, duration: float) -> list:
        """
        Rotate sentences from short_script in the lower-center zone of the Short.
        Each sentence shows for an equal slice of the total duration.
        """
        clips = []
        try:
            text = getattr(vc, "short_script", "") or vc.script[:400]
            raw = re.split(r'(?<=[.!?])\s+', text.strip())
            sentences = [s.strip() for s in raw if s.strip()]
            if not sentences:
                return clips

            n = len(sentences)
            seg_dur = duration / n
            panel_y = 1100
            panel_h = 360

            # Dark panel
            clips.append(
                ColorClip(size=(self.W, panel_h), color=(5, 5, 10))
                .with_duration(duration).with_position(("center", panel_y))
            )
            clips.append(
                ColorClip(size=(self.W, 4), color=self.ACCENT)
                .with_duration(duration).with_position(("center", panel_y))
            )

            for i, sent in enumerate(sentences):
                t_start = i * seg_dur
                t_end   = t_start + seg_dur
                wrapped = "\n".join(textwrap.wrap(sent, width=28))
                try:
                    txt = TextClip(
                        text=wrapped, font_size=60, color="white",
                        font=self._get_font(True), method="caption",
                        size=(self.W - 80, None), text_align="center",
                    ).with_start(t_start).with_end(t_end).with_position(("center", panel_y + 24))
                    clips.append(txt)
                except Exception as e:
                    logger.debug(f"Shorts sentence {i} failed: {e}")
        except Exception as e:
            logger.warning(f"Shorts script sentences failed: {e}")
        return clips

    def _make_subtext(self, subtext: str, duration: float) -> list:
        return self._make_subtext_positioned(subtext, duration, self.H // 2 + 10)

    def _make_subtext_positioned(self, subtext: str, duration: float, sub_y: int) -> list:
        if not subtext:
            return []
        clips = []
        try:
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
            badge_bg = ColorClip(size=(380, _BADGE_H), color=self.ACCENT) \
                .with_duration(duration).with_position((40, _BADGE_Y))
            clips.append(badge_bg)
            src = TextClip(
                text=f"  {source_name.upper()}  ", font_size=30, color="white",
                font=self._get_font(True), method="label",
            ).with_duration(duration).with_position((40, _BADGE_Y + 8))
            clips.append(src)
        except Exception as e:
            logger.warning(f"Shorts source badge failed: {e}")
        return clips

    def _make_shorts_watermark(self, duration: float) -> list:
        clips = []
        try:
            logo_path = "assets/channel_logo.png"
            if os.path.exists(logo_path):
                from PIL import Image
                logo_resized_path = str(self.output_dir / "temp_logo_48.png")
                if not os.path.exists(logo_resized_path):
                    with Image.open(logo_path) as img:
                        w, h = img.size
                        new_w = int(w * (48 / h))
                        img.resize((new_w, 48), Image.Resampling.LANCZOS).save(logo_resized_path, "PNG")
                
                logo = ImageClip(logo_resized_path).with_duration(duration)
                logo = logo.with_position((self.W - 260, self.H - 206))
                clips.append(logo)
                
            wm = TextClip(
                text="#Shorts", font_size=36, color="#888888",
                font=self._get_font(False), method="label",
            ).with_duration(duration).with_position((self.W - 190, self.H - 200))
            clips.append(wm)
        except Exception as e:
            logger.warning(f"Shorts watermark failed: {e}")
        return clips
