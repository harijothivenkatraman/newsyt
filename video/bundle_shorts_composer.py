"""
video/bundle_shorts_composer.py
Composes bundle YouTube Shorts from a ranked list of news stubs.

Two modes:
  "daily"  — 3 parts × 10 news items, ~5 s per item, total ~57 s each
  "weekly" — 10 parts × 10 news items, ~5 s per item, total ~57 s each

Each part is a fully self-contained 9:16 vertical MP4 ≤ 59 s, eligible as a
YouTube Short. Parts contain ONLY visuals — no narration:
  - Rank number (large, red, top-left)
  - News headline (bold white, centered)
  - News image (as blurred bg + inset if available)
  - Source badge (bottom-left)
  - Background music

Intro slide (3 s) + 10 news slots × ~5 s + outro (2 s) = ~55 s per part.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Optional
from loguru import logger

try:
    from moviepy import (
        AudioFileClip, ColorClip, CompositeVideoClip,
        TextClip, ImageClip, concatenate_videoclips,
    )
    import numpy as np
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    logger.warning("MoviePy not installed — BundleShortsComposer will be disabled.")


# Timing constants
INTRO_DURATION   = 3.0   # seconds
OUTRO_DURATION   = 2.0   # seconds
SLOT_DURATION    = 5.0   # seconds per news item
ITEMS_PER_PART   = 10    # items per bundle part

# Design constants
W, H = 1080, 1920
FPS  = 30
ACCENT      = (220, 30, 30)
BG_TOP      = (8, 10, 22)
BG_BOT      = (25, 5, 5)
PANEL_COLOR = (5, 5, 15)


class BundleShortsComposer:
    """
    Produces N×60-second bundle Short parts from a ranked article stub list.

    Usage:
        composer = BundleShortsComposer(output_dir="./output/shorts")
        paths = composer.compose(stubs, mode="daily", bundle_id="2026-05-31")
        # paths → ["./output/shorts/daily_2026-05-31_part1.mp4", ...]
    """

    def __init__(self, output_dir: str = "./output/shorts"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def compose(
        self,
        stubs: list[dict],
        mode: str,          # "daily" or "weekly"
        bundle_id: str,     # e.g. "2026-05-31" or "2026-W22"
        channel_name: str = "News Channel",
    ) -> list[str]:
        """
        Compose all parts for a bundle and return list of output MP4 paths.
        Skips parts if MoviePy is unavailable.
        """
        if not MOVIEPY_AVAILABLE:
            logger.warning("BundleShortsComposer: MoviePy not available.")
            return []

        if not stubs:
            logger.warning("BundleShortsComposer: no stubs provided.")
            return []

        # Split stubs into groups of ITEMS_PER_PART
        parts = [stubs[i:i + ITEMS_PER_PART] for i in range(0, len(stubs), ITEMS_PER_PART)]
        total_parts = len(parts)
        output_paths = []

        for part_idx, part_stubs in enumerate(parts, start=1):
            try:
                out_path = self._compose_part(
                    stubs=part_stubs,
                    mode=mode,
                    bundle_id=bundle_id,
                    part_num=part_idx,
                    total_parts=total_parts,
                    channel_name=channel_name,
                    global_offset=(part_idx - 1) * ITEMS_PER_PART,
                )
                if out_path:
                    output_paths.append(out_path)
                    logger.success(f"Bundle part {part_idx}/{total_parts} saved: {out_path}")
            except Exception as e:
                logger.error(f"Bundle part {part_idx} failed: {e}")

        return output_paths

    # ── Part composition ─────────────────────────────────────────────────────

    def _compose_part(
        self,
        stubs: list[dict],
        mode: str,
        bundle_id: str,
        part_num: int,
        total_parts: int,
        channel_name: str,
        global_offset: int,         # rank of first item in this part (0-indexed)
    ) -> str:
        label = "TOP NEWS TODAY" if mode == "daily" else "TOP NEWS THIS WEEK"
        part_label = f"PART {part_num} OF {total_parts}"
        n_items = len(stubs)
        slot_dur = SLOT_DURATION  # per item

        clips = []

        # ── Intro slide ───────────────────────────────────────────────────────
        intro = self._make_intro_slide(
            label=label,
            part_label=part_label,
            item_range=f"#{global_offset + 1}–#{global_offset + n_items}",
            channel_name=channel_name,
        )
        clips.append(intro)

        # ── News item slots ───────────────────────────────────────────────────
        for slot_idx, stub in enumerate(stubs):
            rank = global_offset + slot_idx + 1
            slot_clip = self._make_news_slot(stub=stub, rank=rank, duration=slot_dur)
            clips.append(slot_clip)

        # ── Outro slide ───────────────────────────────────────────────────────
        outro = self._make_outro_slide(channel_name=channel_name)
        clips.append(outro)

        # ── Concatenate ───────────────────────────────────────────────────────
        final = concatenate_videoclips(clips, method="compose")

        # ── BGM ───────────────────────────────────────────────────────────────
        total_dur = final.duration
        for bgm_candidate in ("assets/bgm_news.wav", "assets/bgm.wav"):
            if os.path.exists(bgm_candidate):
                try:
                    from moviepy import CompositeAudioClip
                    from moviepy.audio.fx import MultiplyVolume, AudioLoop
                    bgm = AudioFileClip(bgm_candidate)
                    bgm = bgm.with_effects([MultiplyVolume(0.15)])
                    bgm = bgm.with_effects([AudioLoop(duration=total_dur)])
                    final = final.with_audio(bgm)
                except Exception as e:
                    logger.warning(f"Bundle BGM mix failed: {e}")
                break

        # ── Write ─────────────────────────────────────────────────────────────
        mode_slug = "daily" if mode == "daily" else "weekly"
        out_name  = f"{mode_slug}_{bundle_id}_part{part_num}.mp4"
        out_path  = str(self.output_dir / out_name)

        tmp_audio = str(self.output_dir / f"{mode_slug}_{bundle_id}_part{part_num}_tmp.m4a")
        final.write_videofile(
            out_path,
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=tmp_audio,
            remove_temp=True,
            logger=None,
        )
        return out_path

    # ── Intro slide ───────────────────────────────────────────────────────────

    def _make_intro_slide(
        self,
        label: str,
        part_label: str,
        item_range: str,
        channel_name: str,
    ) -> "CompositeVideoClip":
        dur = INTRO_DURATION
        bg  = self._make_gradient_bg(dur)
        clips: list = [bg]

        # Top + bottom accent bars
        clips += self._accent_bars(dur)

        # Channel name
        try:
            clips.append(
                TextClip(
                    text=channel_name.upper(), font_size=38, color="#dc1e1e",
                    font=self._font(True), method="label",
                ).with_duration(dur).with_position(("center", 120))
            )
        except Exception:
            pass

        # Channel logo
        logo_path = "assets/channel_logo.png"
        if os.path.exists(logo_path):
            try:
                from PIL import Image
                logo_resized_path = str(self.output_dir / "temp_logo_180.png")
                if not os.path.exists(logo_resized_path):
                    with Image.open(logo_path) as img:
                        w, h = img.size
                        new_w = int(w * (180 / h))
                        img.resize((new_w, 180), Image.Resampling.LANCZOS).save(logo_resized_path, "PNG")
                        
                logo = ImageClip(logo_resized_path).with_duration(dur)
                logo = logo.with_position(("center", 240))
                clips.append(logo)
            except Exception as e:
                logger.warning(f"Bundle intro logo failed: {e}")

        # Main label
        try:
            clips.append(
                TextClip(
                    text=label, font_size=92, color="white",
                    font=self._font(True), method="label",
                ).with_duration(dur).with_position(("center", 700))
            )
        except Exception:
            pass

        # Part indicator
        try:
            clips.append(
                TextClip(
                    text=part_label, font_size=52, color="#cccccc",
                    font=self._font(False), method="label",
                ).with_duration(dur).with_position(("center", 860))
            )
        except Exception:
            pass

        # Item range
        try:
            clips.append(
                TextClip(
                    text=item_range, font_size=58, color="#dc1e1e",
                    font=self._font(True), method="label",
                ).with_duration(dur).with_position(("center", 950))
            )
        except Exception:
            pass

        return CompositeVideoClip(clips, size=(W, H)).with_duration(dur)

    # ── News slot ─────────────────────────────────────────────────────────────

    def _make_news_slot(self, stub: dict, rank: int, duration: float) -> "CompositeVideoClip":
        """Single news item slide with rank + title + image background."""
        image_url = stub.get("image_url", "")
        title     = stub.get("title", "Untitled News")
        source    = stub.get("source", "")

        bg_path, fg_path = None, None
        if image_url:
            bg_path, fg_path = self._prepare_images(image_url, f"bundle_{rank}")

        # Background
        if bg_path and os.path.exists(bg_path):
            bg = ImageClip(bg_path).with_duration(duration)
        else:
            bg = self._make_gradient_bg(duration)

        clips: list = [bg]
        clips += self._accent_bars(duration)

        # Dark overlay for readability
        try:
            overlay = np.zeros((H, W, 4), dtype=np.uint8)
            overlay[:, :, 3] = 160
            clips.append(ImageClip(overlay).with_duration(duration))
        except Exception:
            pass

        # Rank number (bold, red, top-left)
        try:
            clips.append(
                TextClip(
                    text=f"#{rank}", font_size=130, color="#dc1e1e",
                    font=self._font(True), method="label",
                ).with_duration(duration).with_position((50, 80))
            )
        except Exception:
            pass

        # Foreground image inset (right side)
        if fg_path and os.path.exists(fg_path):
            try:
                fg = ImageClip(fg_path).with_duration(duration)
                # Position on right side, vertically centered
                fg_w, fg_h = fg.size
                fg_x = W - fg_w - 20
                fg_y = (H - fg_h) // 2
                clips.append(fg.with_position((fg_x, fg_y)))
            except Exception as e:
                logger.debug(f"Bundle FG image failed for rank {rank}: {e}")

        # Title text (large, white, centered lower half)
        try:
            wrapped = "\n".join(textwrap.wrap(title, width=24))
            clips.append(
                TextClip(
                    text=wrapped, font_size=78, color="white",
                    font=self._font(True), method="caption",
                    size=(W - 80, None), text_align="center",
                ).with_duration(duration).with_position(("center", 900))
            )
        except Exception as e:
            logger.debug(f"Bundle title text failed for rank {rank}: {e}")

        # Source badge (bottom-left)
        if source:
            try:
                badge = ColorClip(size=(min(400, len(source) * 16 + 40), 56), color=ACCENT) \
                    .with_duration(duration).with_position((40, H - 240))
                clips.append(badge)
                clips.append(
                    TextClip(
                        text=f"  {source.upper()}  ", font_size=32, color="white",
                        font=self._font(True), method="label",
                    ).with_duration(duration).with_position((40, H - 236))
                )
            except Exception:
                pass

        # Clean up temp images
        slot_clip = CompositeVideoClip(clips, size=(W, H)).with_duration(duration)

        # Schedule image cleanup after clip is created (best-effort)
        for p in [bg_path, fg_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        return slot_clip

    # ── Outro slide ───────────────────────────────────────────────────────────

    def _make_outro_slide(self, channel_name: str) -> "CompositeVideoClip":
        dur = OUTRO_DURATION
        bg  = self._make_gradient_bg(dur)
        clips: list = [bg]
        clips += self._accent_bars(dur)

        try:
            logo_path = "assets/channel_logo.png"
            if os.path.exists(logo_path):
                from PIL import Image
                logo_resized_path = str(self.output_dir / "temp_logo_160.png")
                if not os.path.exists(logo_resized_path):
                    with Image.open(logo_path) as img:
                        w, h = img.size
                        new_w = int(w * (160 / h))
                        img.resize((new_w, 160), Image.Resampling.LANCZOS).save(logo_resized_path, "PNG")
                        
                logo = ImageClip(logo_resized_path).with_duration(dur)
                logo = logo.with_position(("center", H // 2 - 280))
                clips.append(logo)
        
            clips.append(
                TextClip(
                    text=channel_name.upper(), font_size=60, color="#dc1e1e",
                    font=self._font(True), method="label",
                ).with_duration(dur).with_position(("center", H // 2 - 100))
            )
            clips.append(
                TextClip(
                    text="LIKE  ·  SUBSCRIBE  ·  SHARE",
                    font_size=44, color="white",
                    font=self._font(True), method="label",
                ).with_duration(dur).with_position(("center", H // 2 + 40))
            )
            clips.append(
                TextClip(
                    text="#Shorts #NewsRoundup",
                    font_size=32, color="#888888",
                    font=self._font(False), method="label",
                ).with_duration(dur).with_position(("center", H - 200))
            )
        except Exception as e:
            logger.warning(f"Bundle outro text failed: {e}")

        return CompositeVideoClip(clips, size=(W, H)).with_duration(dur)

    # ── Visual helpers ────────────────────────────────────────────────────────

    def _make_gradient_bg(self, duration: float) -> "ImageClip":
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        for y in range(H):
            t = y / H
            frame[y, :] = [
                int(BG_TOP[i] * (1 - t) + BG_BOT[i] * t) for i in range(3)
            ]
        return ImageClip(frame).with_duration(duration)

    def _accent_bars(self, duration: float) -> list:
        top = ColorClip(size=(W, 12), color=ACCENT) \
            .with_duration(duration).with_position(("center", 0))
        bot = ColorClip(size=(W, 12), color=ACCENT) \
            .with_duration(duration).with_position(("center", H - 12))
        return [top, bot]

    def _font(self, bold: bool = False) -> str:
        path = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
        return path if os.path.exists(path) else "Arial"

    def _prepare_images(
        self, image_url: str, prefix: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Download image → blurred dark BG + small inset FG. Returns (bg_path, fg_path)."""
        try:
            import requests
            from PIL import Image, ImageFilter, ImageEnhance
            from io import BytesIO

            resp = requests.get(image_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            w, h = img.size

            # Background: scale to fill 1080×1920, blur, darken
            scale = max(W / w, H / h)
            nw, nh = int(w * scale), int(h * scale)
            bg_img = img.resize((nw, nh), Image.Resampling.LANCZOS)
            left = (nw - W) // 2
            top  = (nh - H) // 2
            bg_img = bg_img.crop((left, top, left + W, top + H))
            bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=20))
            bg_img = ImageEnhance.Brightness(bg_img).enhance(0.3)

            bg_path = str(self.output_dir / f"{prefix}_bg.jpg")
            bg_img.save(bg_path, "JPEG")

            # Foreground: small inset (400 px wide), right side
            fg_w = 380
            fg_h = int(h * fg_w / w)
            fg_img = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
            # Add white border
            bordered = Image.new("RGB", (fg_w + 8, fg_h + 8), (255, 255, 255))
            bordered.paste(fg_img, (4, 4))

            fg_path = str(self.output_dir / f"{prefix}_fg.jpg")
            bordered.save(fg_path, "JPEG")

            return bg_path, fg_path

        except Exception as e:
            logger.debug(f"Bundle image fetch failed for {image_url}: {e}")
            return None, None
