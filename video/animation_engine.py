"""
video/animation_engine.py
Pure-numpy 2D animation primitives for YouTube Shorts.
Python 3.6+ compatible.

All functions return MoviePy-compatible clips (VideoClip / ImageClip /
CompositeVideoClip) so they slot seamlessly into the existing composer pipeline.

Effects catalogue:
  - particle_overlay   : scattered glowing dots that drift upward
  - pulse_bar          : a colour bar that throbs in opacity
  - slide_in_panel     : panel + text sliding up/down/left/right
  - kinetic_word_reveal: word-by-word pop-in kinetic text
  - animated_badge     : source badge that bounces in from the left
  - flash_transition   : full-frame colour flash
  - zoom_scale_clip    : Ken-Burns slow zoom wrapper
  - scrolling_ticker   : horizontal scrolling news ticker bar
  - scanline_overlay   : CRT/monitor scanline aesthetic
  - segment_progress_bar: progress bar for segments
  - emoji_burst        : exploding emoji effect
  - rolling_counter    : animating numbers (e.g. subscriber counts)
  - highlight_sweep    : text highlight sweep effect
"""

import math
import random
import textwrap
from typing import List, Optional, Tuple

import numpy as np

# lazy MoviePy imports
try:
    from moviepy import VideoClip, ImageClip, ColorClip, CompositeVideoClip, TextClip
    MOVIEPY_OK = True
except ImportError:
    MOVIEPY_OK = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ease_out_cubic(t):
    # type: (float) -> float
    """Smooth deceleration curve (0->1)."""
    return 1 - (1 - t) ** 3


def _ease_in_out(t):
    # type: (float) -> float
    if t < 0.5:
        return 4 * t * t * t
    return 1 - (-2 * t + 2) ** 3 / 2


def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, v))


def _ease_out_spring(t, overshoot=0.18):
    # type: (float, float) -> float
    """Ease-out with a small spring overshoot, then settles at 1.0."""
    if t <= 0:
        return 0.0
    if t >= 1.0:
        return 1.0
    decay = math.exp(-5 * t)
    return 1.0 - decay * math.cos(t * math.pi * 2.5) + overshoot * decay * math.sin(t * math.pi * 3.5)



# ---------------------------------------------------------------------------
# 1. Particle / energy background overlay
# ---------------------------------------------------------------------------

def particle_overlay(duration, W=1080, H=1920, fps=30, n_particles=60,
                     color=(220, 30, 30), seed=42):
    """
    Returns a VideoClip (RGBA) of drifting glowing particles.
    Fully vectorised with numpy — no Python loops per pixel.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    rng = np.random.default_rng(seed)
    px0  = rng.uniform(0, W,        n_particles).astype(np.float32)
    py0  = rng.uniform(0, H,        n_particles).astype(np.float32)
    vx   = rng.uniform(-0.3, 0.3,   n_particles).astype(np.float32)
    vy   = -rng.uniform(0.4, 1.8,   n_particles).astype(np.float32)  # upward drift
    radii = rng.uniform(2, 6,        n_particles).astype(np.float32)
    alphas = rng.uniform(60, 160,    n_particles).astype(np.float32)
    phases = rng.uniform(0, 2*math.pi, n_particles).astype(np.float32)

    # Pre-build a soft glow kernel for each unique radius bucket
    max_r = int(radii.max()) + 1
    _kernel_cache = {}
    for r_int in range(1, max_r + 2):
        ks = 2 * r_int + 1
        cy_k, cx_k = np.mgrid[-r_int:r_int+1, -r_int:r_int+1]
        dist_k = np.sqrt(cx_k**2 + cy_k**2)
        kern = np.clip(1.0 - dist_k / r_int, 0, 1) ** 2
        _kernel_cache[r_int] = kern

    def make_frame(t):
        frame = np.zeros((H, W, 4), dtype=np.float32)
        twinkle = 0.5 + 0.5 * np.sin(phases + t * 3.0)
        cur_alpha = alphas * twinkle

        cx = np.mod(px0 + vx * t * fps, W).astype(np.int32)
        cy = np.mod(py0 + vy * t * fps, H).astype(np.int32)

        for i in range(n_particles):
            r = int(radii[i])
            a = cur_alpha[i]
            kern = _kernel_cache.get(r, _kernel_cache[max(1, min(r, max_r))])
            ks = 2 * r + 1

            y0 = cy[i] - r;  y1 = cy[i] + r + 1
            x0 = cx[i] - r;  x1 = cx[i] + r + 1
            ky0 = max(0, -y0);  ky1 = ks - max(0, y1 - H)
            kx0 = max(0, -x0);  kx1 = ks - max(0, x1 - W)
            fy0 = max(0, y0);   fy1 = min(H, y1)
            fx0 = max(0, x0);   fx1 = min(W, x1)

            if fy1 > fy0 and fx1 > fx0:
                k_slice = kern[ky0:ky1, kx0:kx1] * a
                frame[fy0:fy1, fx0:fx1, 0] += color[0] * k_slice
                frame[fy0:fy1, fx0:fx1, 1] += color[1] * k_slice
                frame[fy0:fy1, fx0:fx1, 2] += color[2] * k_slice
                frame[fy0:fy1, fx0:fx1, 3] += k_slice

        return np.clip(frame, 0, 255).astype(np.uint8)

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip


# ---------------------------------------------------------------------------
# 2. Pulse bar
# ---------------------------------------------------------------------------

def pulse_bar(duration, W=1080, bar_h=10, color=(220, 30, 30),
              y_pos=0, fps=30, pulse_hz=1.2):
    """
    A solid colour bar that pulses (brightens/dims) at pulse_hz beats per second.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    def make_frame(t):
        pulse = 0.55 + 0.45 * math.sin(2 * math.pi * pulse_hz * t)
        r = int(_clamp(color[0] * pulse))
        g = int(_clamp(color[1] * pulse))
        b = int(_clamp(color[2] * pulse))
        frame = np.zeros((bar_h, W, 3), dtype=np.uint8)
        frame[:, :] = [r, g, b]
        return frame

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position(("center", y_pos))


# ---------------------------------------------------------------------------
# 3. Slide-in panel
# ---------------------------------------------------------------------------

def slide_in_panel(text, duration, W=1080, panel_h=220, y_final=1500,
                   font_size=52, bg_color=(5, 5, 15), text_color=(255, 255, 255),
                   accent_color=(220, 30, 30), slide_in_dur=0.35, fps=30,
                   direction="up", font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Panel that slides in from outside the frame edge.
    Once settled, stays for the remaining duration.
    direction: 'up' | 'down' | 'left' | 'right'
    Wrap width is computed dynamically to prevent text overflow.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    try:
        from PIL import Image, ImageDraw, ImageFont
        import os

        img = Image.new("RGBA", (W, panel_h), (bg_color[0], bg_color[1], bg_color[2], 230))
        draw = ImageDraw.Draw(img)
        # Left accent stripe (4 px wide on the left side)
        draw.rectangle([(0, 0), (4, panel_h)], fill=(accent_color[0], accent_color[1], accent_color[2], 255))
        # Top accent line
        draw.rectangle([(0, 0), (W, 3)], fill=(accent_color[0], accent_color[1], accent_color[2], 180))

        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        # Dynamic wrap: ~0.52 chars per pixel width at given font size
        usable_w = W - 80  # 40px padding each side
        wrap_chars = max(12, int(usable_w / (font_size * 0.52)))
        wrapped = "\n".join(textwrap.wrap(text, width=wrap_chars))
        draw.multiline_text(
            (24, 14), wrapped,
            font=font, fill=(text_color[0], text_color[1], text_color[2], 255),
            spacing=10,
        )
        panel_arr = np.array(img)

    except Exception:
        panel_arr = np.zeros((panel_h, W, 4), dtype=np.uint8)
        panel_arr[:, :, 0] = bg_color[0]
        panel_arr[:, :, 1] = bg_color[1]
        panel_arr[:, :, 2] = bg_color[2]
        panel_arr[:, :, 3] = 200
        panel_arr[:, :4, 0] = accent_color[0]
        panel_arr[:, :4, 1] = accent_color[1]
        panel_arr[:, :4, 2] = accent_color[2]
        panel_arr[:, :4, 3] = 255

    from moviepy import VideoClip as VC

    if direction == "up":
        off_x, off_y_start = 0, panel_h
    elif direction == "down":
        off_x, off_y_start = 0, -panel_h
    elif direction == "left":
        off_x, off_y_start = -W, 0
    else:
        off_x, off_y_start = W, 0

    def make_frame(t):
        progress = min(t / slide_in_dur, 1.0)
        ease = _ease_out_cubic(progress)

        if direction in ("up", "down"):
            dy = int(off_y_start * (1 - ease))
            dx = 0
        else:
            dx = int(off_x * (1 - ease))
            dy = 0

        canvas = np.zeros((panel_h, W, 4), dtype=np.uint8)
        src_x = max(0, -dx);  src_y = max(0, -dy)
        dst_x = max(0, dx);   dst_y = max(0, dy)
        copy_w = W - abs(dx); copy_h = panel_h - abs(dy)

        if copy_w > 0 and copy_h > 0:
            canvas[dst_y:dst_y + copy_h, dst_x:dst_x + copy_w] = \
                panel_arr[src_y:src_y + copy_h, src_x:src_x + copy_w]
        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position(("center", y_final))


# ---------------------------------------------------------------------------
# 4. Kinetic word-pop text reveal
# ---------------------------------------------------------------------------

def kinetic_word_reveal(words, duration, W=1080, H=1920, font_size=80,
                        text_color=(255, 255, 255), highlight_color=(220, 30, 30),
                        y_center=400, fps=30, font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Words appear one at a time, each fading+drifting in.
    The current word is highlighted in accent colour; past words remain white.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    n = len(words)
    word_interval = duration / max(n, 1)
    scale_dur = 0.25

    # Pre-compute line layout
    max_chars_per_line = 18
    lines = []
    current_line = []
    char_count = 0
    for word in words:
        if char_count + len(word) > max_chars_per_line and current_line:
            lines.append(current_line)
            current_line = [word]
            char_count = len(word)
        else:
            current_line.append(word)
            char_count += len(word) + 1
    if current_line:
        lines.append(current_line)

    line_height = font_size + 16
    total_h = len(lines) * line_height + 40

    def make_frame(t):
        revealed = int(t / word_interval)

        try:
            from PIL import Image, ImageDraw, ImageFont
            import os

            canvas_h = total_h
            img = Image.new("RGBA", (W, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            try:
                f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
                    else ImageFont.load_default()
            except Exception:
                f = ImageFont.load_default()

            word_global_idx = 0
            for li, line in enumerate(lines):
                line_text = " ".join(line)
                try:
                    bbox = draw.textbbox((0, 0), line_text, font=f)
                    line_w = bbox[2] - bbox[0]
                except Exception:
                    line_w = len(line_text) * (font_size // 2)
                x_start = (W - line_w) // 2
                y = li * line_height + 10

                x_cursor = x_start
                for word in line:
                    try:
                        bbox = draw.textbbox((0, 0), word + " ", font=f)
                        ww = bbox[2] - bbox[0]
                    except Exception:
                        ww = len(word) * (font_size // 2 + 2)

                    if word_global_idx < revealed:
                        draw.text((x_cursor, y), word, font=f,
                                  fill=(text_color[0], text_color[1], text_color[2], 255))
                    elif word_global_idx == revealed:
                        word_t = t - revealed * word_interval
                        progress = min(word_t / scale_dur, 1.0)
                        ease = _ease_out_cubic(progress)
                        alpha = int(255 * ease)
                        drift_y = int(12 * (1 - ease))
                        draw.text(
                            (x_cursor, y - drift_y), word, font=f,
                            fill=(highlight_color[0], highlight_color[1], highlight_color[2], alpha)
                        )

                    x_cursor += ww
                    word_global_idx += 1

            return np.array(img)

        except Exception:
            return np.zeros((total_h, W, 4), dtype=np.uint8)

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    clip_y = y_center - total_h // 2
    return clip.with_position(("center", clip_y))


# ---------------------------------------------------------------------------
# 5. Animated badge (bounces in from left)
# ---------------------------------------------------------------------------

def animated_badge(label, duration, x_final=40, y_pos=1700, badge_h=64,
                   font_size=36, bg_color=(220, 30, 30), text_color=(255, 255, 255),
                   fps=30, delay=0.4, font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Red pill badge that slides in from the left with a bounce overshoot.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    try:
        from PIL import Image, ImageDraw, ImageFont
        import os

        try:
            f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
                else ImageFont.load_default()
        except Exception:
            f = ImageFont.load_default()

        tmp = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(tmp)
        try:
            bbox = d.textbbox((0, 0), "  {}  ".format(label.upper()), font=f)
            badge_w = bbox[2] - bbox[0] + 20
        except Exception:
            badge_w = len(label) * (font_size // 2) + 40
        badge_w = max(200, badge_w)

        badge_img = Image.new("RGBA", (badge_w, badge_h),
                              (bg_color[0], bg_color[1], bg_color[2], 255))
        draw = ImageDraw.Draw(badge_img)
        try:
            bbox = draw.textbbox((0, 0), "  {}  ".format(label.upper()), font=f)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = badge_w - 20, badge_h - 10
        tx = (badge_w - tw) // 2
        ty = (badge_h - th) // 2
        draw.text((tx, ty), "  {}  ".format(label.upper()), font=f,
                  fill=(text_color[0], text_color[1], text_color[2], 255))
        badge_arr = np.array(badge_img)

    except Exception:
        badge_w = max(200, len(label) * 20 + 40)
        badge_arr = np.zeros((badge_h, badge_w, 4), dtype=np.uint8)
        badge_arr[:, :, 0] = bg_color[0]
        badge_arr[:, :, 1] = bg_color[1]
        badge_arr[:, :, 2] = bg_color[2]
        badge_arr[:, :, 3] = 255

    from moviepy import VideoClip as VC

    x_start = -(badge_w + 20)
    slide_dur = 0.4
    bounce_amount = 14

    def make_frame(t):
        canvas_w = x_final + badge_w + 20
        canvas = np.zeros((badge_h, canvas_w, 4), dtype=np.uint8)

        effective_t = max(0.0, t - delay)
        if effective_t <= 0:
            return canvas

        progress = min(effective_t / slide_dur, 1.0)
        if progress < 0.75:
            ease = _ease_out_cubic(progress / 0.75)
        else:
            overshoot_progress = (progress - 0.75) / 0.25
            ease = 1.0 + bounce_amount / (x_final - x_start) * math.sin(overshoot_progress * math.pi)

        current_x = int(x_start + (x_final - x_start) * ease)
        current_x = max(x_start, min(current_x, x_final + badge_w))

        paste_x = current_x
        if paste_x < canvas_w and paste_x + badge_w > 0:
            src_x = max(0, -paste_x)
            dst_x = max(0, paste_x)
            copy_w = min(badge_w - src_x, canvas_w - dst_x)
            if copy_w > 0:
                canvas[:, dst_x:dst_x + copy_w] = badge_arr[:, src_x:src_x + copy_w]

        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position((0, y_pos))


# ---------------------------------------------------------------------------
# 6. Flash transition frame
# ---------------------------------------------------------------------------

def flash_transition(duration=0.25, W=1080, H=1920, color=(220, 30, 30), fps=30):
    """
    Quick full-frame colour flash that fades out — used at segment transitions.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    def make_frame(t):
        alpha = int(255 * (1 - t / duration) ** 2)
        frame = np.zeros((H, W, 4), dtype=np.uint8)
        frame[:, :, 0] = color[0]
        frame[:, :, 1] = color[1]
        frame[:, :, 2] = color[2]
        frame[:, :, 3] = alpha
        return frame

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip


# ---------------------------------------------------------------------------
# 7. Zoom scale clip wrapper (Ken-Burns)
# ---------------------------------------------------------------------------

def zoom_scale_clip(clip, zoom_start=1.0, zoom_end=1.06):
    """
    Applies a slow zoom (Ken-Burns) to any VideoClip.
    Returns a new VideoClip of the same duration.
    """
    if not MOVIEPY_OK:
        return clip

    from moviepy import VideoClip as VC

    W, H = clip.size
    duration = clip.duration

    def make_frame(t):
        progress = t / duration
        zoom = zoom_start + (zoom_end - zoom_start) * _ease_in_out(progress)

        frame = clip.get_frame(t)
        if len(frame.shape) == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]

        try:
            from PIL import Image
            img = Image.fromarray(frame.astype(np.uint8))
            new_w = int(W * zoom)
            new_h = int(H * zoom)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - W) // 2
            top = (new_h - H) // 2
            img = img.crop((left, top, left + W, top + H))
            return np.array(img)
        except Exception:
            return frame

    new_clip = VC(make_frame, duration=duration)
    new_clip.fps = clip.fps if hasattr(clip, "fps") and clip.fps else 30
    return new_clip


# ---------------------------------------------------------------------------
# 8. Scrolling news ticker
# ---------------------------------------------------------------------------

def scrolling_ticker(text, duration, W=1080, ticker_h=72, y_pos=1820,
                     font_size=38, bg_color=(180, 10, 10), text_color=(255, 255, 255),
                     scroll_speed=220, fps=30, font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Scrolling red ticker bar with white text — broadcast-style lower-third.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    try:
        from PIL import Image, ImageDraw, ImageFont
        import os

        try:
            f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
                else ImageFont.load_default()
        except Exception:
            f = ImageFont.load_default()

        ticker_text = "  \u25cf {}  \u25cf  {}  \u25cf  {}  ".format(
            text.upper(), text.upper(), text.upper()
        )
        tmp_img = Image.new("RGB", (1, 1))
        d = ImageDraw.Draw(tmp_img)
        try:
            bbox = d.textbbox((0, 0), ticker_text, font=f)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(ticker_text) * (font_size // 2)

        strip_w = text_w + W
        strip = Image.new("RGBA", (strip_w, ticker_h),
                          (bg_color[0], bg_color[1], bg_color[2], 255))
        draw = ImageDraw.Draw(strip)
        try:
            bbox = draw.textbbox((0, 0), ticker_text, font=f)
            ty = (ticker_h - (bbox[3] - bbox[1])) // 2
        except Exception:
            ty = (ticker_h - font_size) // 2
        draw.text((10, ty), ticker_text, font=f,
                  fill=(text_color[0], text_color[1], text_color[2], 255))
        strip_arr = np.array(strip)

    except Exception:
        strip_w = W * 3
        strip_arr = np.zeros((ticker_h, strip_w, 4), dtype=np.uint8)
        strip_arr[:, :, 0] = bg_color[0]
        strip_arr[:, :, 1] = bg_color[1]
        strip_arr[:, :, 2] = bg_color[2]
        strip_arr[:, :, 3] = 255

    from moviepy import VideoClip as VC

    def make_frame(t):
        frame = np.zeros((ticker_h, W, 4), dtype=np.uint8)
        offset = int(t * scroll_speed) % strip_w
        src_start = offset
        src_end = min(src_start + W, strip_w)
        copy_w = src_end - src_start
        frame[:, :copy_w] = strip_arr[:, src_start:src_end]
        if copy_w < W:
            wrap_w = W - copy_w
            frame[:, copy_w:] = strip_arr[:, :wrap_w]
        return frame

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position(("center", y_pos))


# ---------------------------------------------------------------------------
# 9. Scanline overlay — subtle CRT broadcast texture + rare RGB glitch
# ---------------------------------------------------------------------------

def scanline_overlay(duration, W=1080, H=1920, fps=30,
                     line_alpha=18, glitch_alpha=55,
                     glitch_interval=6.0, glitch_dur=0.08):
    """
    Overlays dark horizontal scanlines (like a CRT/broadcast monitor) and
    occasionally flashes a very brief RGB channel-split glitch.

    line_alpha   : 0-25 keeps scanlines invisible at a glance but adds texture
    glitch_alpha : max brightness of glitch flash — keep <=80 for subtlety
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    # Pre-build static scanline mask (every other row lightly darkened)
    scanline_mask = np.zeros((H, W, 4), dtype=np.uint8)
    scanline_mask[::2, :, 3] = line_alpha

    def make_frame(t):
        frame = scanline_mask.copy()

        # Glitch: brief RGB channel-split at fixed intervals
        t_mod = t % glitch_interval
        if t_mod < glitch_dur:
            progress = t_mod / glitch_dur
            intensity = glitch_alpha * math.sin(progress * math.pi)
            shift = int(6 * math.sin(progress * math.pi * 3))

            if 0 < shift < W:
                r_strip = np.zeros((H, W, 4), dtype=np.uint8)
                b_strip = np.zeros((H, W, 4), dtype=np.uint8)
                r_strip[:, shift:, 0] = int(intensity)
                r_strip[:, shift:, 3] = int(intensity * 0.6)
                b_strip[:, :W - shift, 2] = int(intensity * 0.8)
                b_strip[:, :W - shift, 3] = int(intensity * 0.5)
                for ch_frame in (r_strip, b_strip):
                    mask = ch_frame[:, :, 3] > 0
                    frame[mask] = np.clip(
                        frame[mask].astype(np.int16) + ch_frame[mask].astype(np.int16),
                        0, 255
                    ).astype(np.uint8)
        return frame

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip


# ---------------------------------------------------------------------------
# 10. Segment progress bar — fills left→right, with glowing leading edge
# ---------------------------------------------------------------------------

def segment_progress_bar(duration, W=1080, bar_h=6, fps=30,
                         fill_color=(220, 30, 30), bg_color=(60, 10, 10),
                         y_pos=None, glow=True):
    """
    A thin bar that fills from left to right over `duration` seconds.
    Glow computed with numpy vectorised ops (no Python loop).
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    # Pre-build glow gradient for leading edge (40px wide)
    glow_w = 40
    glow_ramp = np.linspace(0, 35, glow_w, dtype=np.float32)

    def make_frame(t):
        frame = np.zeros((bar_h, W, 4), dtype=np.uint8)
        frame[:, :, 0] = bg_color[0]
        frame[:, :, 1] = bg_color[1]
        frame[:, :, 2] = bg_color[2]
        frame[:, :, 3] = 120

        fill_w = min(int((t / duration) * W), W)
        if fill_w > 0:
            frame[:, :fill_w, 0] = fill_color[0]
            frame[:, :fill_w, 1] = fill_color[1]
            frame[:, :fill_w, 2] = fill_color[2]
            frame[:, :fill_w, 3] = 255

            if glow and fill_w < W:
                g = min(glow_w, fill_w)
                x0 = fill_w - g
                bright = np.clip(fill_color[0] + glow_ramp[-g:], 0, 255).astype(np.uint8)
                frame[:, x0:fill_w, 0] = bright[np.newaxis, :]
                frame[:, x0:fill_w, 3] = 255
        return frame

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    if y_pos is not None:
        clip = clip.with_position(("center", y_pos))
    return clip


# ---------------------------------------------------------------------------
# 11. Emoji burst — spring-bounce pop-in, pre-rendered at max size
# ---------------------------------------------------------------------------

def emoji_burst(char, duration, x=540, y=160, font_size=72, fps=30,
                delay=0.0, pulse_hz=0.8,
                font_path="C:/Windows/Fonts/seguiemj.ttf",
                fallback_font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Renders a single character/emoji that springs into frame with an overshoot
    bounce then gently pulses.  The glyph is pre-rendered ONCE at max size and
    downscaled per frame (faster + more reliable than per-frame font load).
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC
    import os

    spring_dur = 0.45
    max_scale  = 1.24            # spring overshoot headroom
    canvas_size = int(font_size * max_scale * 2) + 20
    render_size = max(8, font_size)   # pre-render at 1× size

    # --- pre-render glyph once ---
    _glyph_arr = None
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_loaded = None
        for fp in (font_path, fallback_font_path,
                   "C:/Windows/Fonts/seguisym.ttf",
                   "C:/Windows/Fonts/arial.ttf"):
            try:
                if fp and os.path.exists(fp):
                    font_loaded = ImageFont.truetype(fp, render_size)
                    break
            except Exception:
                pass
        if font_loaded is None:
            font_loaded = ImageFont.load_default()

        tmp = Image.new("RGBA", (render_size * 3, render_size * 3), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tmp)
        try:
            bbox = draw.textbbox((0, 0), char, font=font_loaded)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = render_size, render_size
        cx = (render_size * 3 - tw) // 2
        cy = (render_size * 3 - th) // 2
        draw.text((cx, cy), char, font=font_loaded, fill=(255, 255, 255, 255))
        # Crop tightly
        bbox2 = tmp.getbbox()
        if bbox2:
            tmp = tmp.crop(bbox2)
        _glyph_arr = np.array(tmp)
    except Exception:
        _glyph_arr = None

    def make_frame(t):
        canvas = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)
        effective_t = max(0.0, t - delay)
        if effective_t <= 0 or _glyph_arr is None:
            # Fallback: coloured circle
            if effective_t > 0:
                r = canvas_size // 4
                cy_c, cx_c = canvas_size // 2, canvas_size // 2
                ys, xs = np.ogrid[-r:r+1, -r:r+1]
                mask = xs*xs + ys*ys <= r*r
                y0 = cy_c - r; y1 = cy_c + r + 1
                x0 = cx_c - r; x1 = cx_c + r + 1
                canvas[y0:y1, x0:x1][mask] = (220, 30, 30, 200)
            return canvas

        if effective_t < spring_dur:
            scale = _ease_out_spring(effective_t / spring_dur, overshoot=0.22)
        else:
            scale = 1.0 + 0.04 * math.sin(2 * math.pi * pulse_hz * (effective_t - spring_dur))
        scale = max(0.05, min(scale, max_scale))

        try:
            from PIL import Image
            gh, gw = _glyph_arr.shape[:2]
            nw = max(2, int(gw * scale))
            nh = max(2, int(gh * scale))
            glyph_img = Image.fromarray(_glyph_arr).resize((nw, nh), Image.LANCZOS)
            glyph_np = np.array(glyph_img)

            dst_x = (canvas_size - nw) // 2
            dst_y = (canvas_size - nh) // 2
            x0 = max(0, dst_x);  y0 = max(0, dst_y)
            x1 = min(canvas_size, dst_x + nw)
            y1 = min(canvas_size, dst_y + nh)
            sx = x0 - dst_x;  sy = y0 - dst_y
            sw = x1 - x0;     sh = y1 - y0
            if sw > 0 and sh > 0:
                canvas[y0:y1, x0:x1] = glyph_np[sy:sy+sh, sx:sx+sw]
        except Exception:
            pass
        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position((x - canvas_size // 2, y - canvas_size // 2))


# ---------------------------------------------------------------------------
# 12. Rolling counter — animated number rollup (e.g. "₹2,000 Cr")
# ---------------------------------------------------------------------------

def rolling_counter(end_val, duration, W=1080, font_size=90,
                    prefix="", suffix="",
                    fill_color=(255, 220, 50), bg_alpha=0,
                    fps=30, start_val=0,
                    font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Animates a number rolling up from start_val to end_val with ease-out.
    Ideal for stat-heavy news: ₹ amounts, percentages, vote counts, etc.
    Returns an RGBA VideoClip; position it with with_position() in the parent.

    prefix  : Text before number e.g. "₹ " or "$ "
    suffix  : Text after number e.g. " Cr" or "%" or " Lakh"
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    roll_dur = min(duration * 0.75, 2.5)

    def _fmt(v):
        if isinstance(end_val, float):
            return f"{v:,.1f}"
        return f"{int(round(v)):,}"

    # Pre-measure canvas
    sample = prefix + _fmt(end_val) + suffix
    try:
        from PIL import Image, ImageDraw, ImageFont
        import os
        _f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
            else ImageFont.load_default()
        _tmp = Image.new("RGBA", (1, 1))
        _d = ImageDraw.Draw(_tmp)
        _bbox = _d.textbbox((0, 0), sample, font=_f)
        canvas_w = (_bbox[2] - _bbox[0]) + 60
        canvas_h = (_bbox[3] - _bbox[1]) + 40
    except Exception:
        canvas_w = len(sample) * (font_size // 2) + 60
        canvas_h = font_size + 40
    canvas_w = max(canvas_w, 300)

    def make_frame(t):
        progress = _ease_out_cubic(min(t / roll_dur, 1.0)) if t < roll_dur else 1.0
        current = start_val + (end_val - start_val) * progress
        text = prefix + _fmt(current) + suffix

        try:
            from PIL import Image, ImageDraw, ImageFont
            import os

            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            if bg_alpha > 0:
                draw.rectangle([(0, 0), (canvas_w - 1, canvas_h - 1)],
                               fill=(5, 5, 15, bg_alpha))

            f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
                else ImageFont.load_default()
            try:
                bbox = draw.textbbox((0, 0), text, font=f)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                tw, th = canvas_w - 40, canvas_h - 20
            tx = (canvas_w - tw) // 2
            ty = (canvas_h - th) // 2
            draw.text((tx + 2, ty + 2), text, font=f, fill=(0, 0, 0, 160))
            draw.text((tx, ty), text, font=f,
                      fill=(fill_color[0], fill_color[1], fill_color[2], 255))
            return np.array(img)
        except Exception:
            return np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip


# ---------------------------------------------------------------------------
# 13. Highlight sweep — underline-only karaoke bar (no text redraw)
# ---------------------------------------------------------------------------

def highlight_sweep(words, word_timings, duration, W=1080,
                    font_size=76, y_center=350, fps=30,
                    text_color=(255, 255, 255),
                    highlight_color=(255, 200, 40),
                    underline_color=(220, 30, 30),
                    font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    Draws ONLY the animated underline sweep beneath each word as it becomes
    active.  Text is NOT redrawn here (kinetic_word_reveal handles text).
    Layout is pre-computed once; make_frame only composites the underline bar.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC
    import os

    # ── pre-compute layout ────────────────────────────────────────────────────
    max_chars = 18
    lines = []
    cur_line, cc = [], 0
    for w in words:
        if cc + len(w) > max_chars and cur_line:
            lines.append(cur_line)
            cur_line, cc = [w], len(w)
        else:
            cur_line.append(w)
            cc += len(w) + 1
    if cur_line:
        lines.append(cur_line)

    line_h = font_size + 16
    total_h = len(lines) * line_h + 20
    underline_h = max(4, font_size // 12)
    sweep_dur = 0.16

    # pre-measure word positions with PIL (one-time cost)
    word_positions = []   # list of (wx, wy, ww, wh)
    try:
        from PIL import Image, ImageDraw, ImageFont
        _tmp = Image.new("RGBA", (W, total_h))
        _d   = ImageDraw.Draw(_tmp)
        try:
            _f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
                else ImageFont.load_default()
        except Exception:
            _f = ImageFont.load_default()

        for li, line in enumerate(lines):
            line_text = " ".join(line)
            try:
                lbbox = _d.textbbox((0, 0), line_text, font=_f)
                line_w = lbbox[2] - lbbox[0]
            except Exception:
                line_w = len(line_text) * (font_size // 2)
            x_cursor = (W - line_w) // 2
            y_top = li * line_h + 10
            for word in line:
                try:
                    wb = _d.textbbox((0, 0), word, font=_f)
                    ww, wh = wb[2]-wb[0], wb[3]-wb[1]
                    sp = _d.textbbox((0, 0), " ", font=_f)
                    sp_w = sp[2] - sp[0]
                except Exception:
                    ww, wh, sp_w = len(word)*(font_size//2), font_size, font_size//3
                word_positions.append((x_cursor, y_top, ww, wh))
                x_cursor += ww + sp_w
    except Exception:
        # fallback: uniform spacing estimate
        cw = font_size // 2
        x_cursor = 40
        for i, w in enumerate(words):
            word_positions.append((x_cursor, 10, len(w)*cw, font_size))
            x_cursor += len(w)*cw + cw

    # ── make_frame: only draw the animated underline ──────────────────────────
    def make_frame(t):
        canvas = np.zeros((total_h, W, 4), dtype=np.uint8)

        # find active word
        active_idx = len(words) - 1
        for i, ts in enumerate(word_timings):
            if t < ts:
                active_idx = i - 1
                break
        active_idx = max(0, active_idx)

        if active_idx >= len(word_positions):
            return canvas

        wx, wy, ww, wh = word_positions[active_idx]
        t_since = t - word_timings[active_idx]
        sweep_p = _ease_out_cubic(min(t_since / max(sweep_dur, 1e-6), 1.0))
        filled_w = max(1, int(ww * sweep_p))
        uy = wy + wh + 3

        # glow layers (wider, more transparent → narrower, fully opaque)
        for glow_off, glow_a in ((3, 35), (2, 80), (1, 160), (0, 255)):
            x0 = max(0, wx - glow_off)
            x1 = min(W, wx + filled_w + glow_off)
            y0 = max(0, uy - glow_off)
            y1 = min(total_h, uy + underline_h + glow_off)
            if x1 > x0 and y1 > y0:
                canvas[y0:y1, x0:x1, 0] = underline_color[0]
                canvas[y0:y1, x0:x1, 1] = underline_color[1]
                canvas[y0:y1, x0:x1, 2] = underline_color[2]
                canvas[y0:y1, x0:x1, 3] = glow_a
        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position(("center", y_center - total_h // 2))


# ---------------------------------------------------------------------------
# 14. Animated gradient background — slowly shifting hue
# ---------------------------------------------------------------------------

def animated_gradient_bg(duration, W=1080, H=1920, fps=24,
                          top_color=(10, 12, 20), bot_color=(30, 5, 5),
                          hue_shift_deg=20):
    """
    Dark gradient that slowly shifts hue over `duration` seconds.
    Much cheaper than particle overlay for pure background use.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC

    # pre-build base gradient (H×W×3)
    t_vals = np.linspace(0, 1, H, dtype=np.float32)[:, np.newaxis]
    base = np.zeros((H, W, 3), dtype=np.float32)
    for ch in range(3):
        base[:, :, ch] = (top_color[ch] * (1 - t_vals) + bot_color[ch] * t_vals)

    def make_frame(t):
        progress = t / max(duration, 1e-6)
        # Gentle brightness pulse (±4%)
        bright = 1.0 + 0.04 * math.sin(progress * math.pi * 2)
        # Subtle red hue accent grows toward end
        red_boost = hue_shift_deg * progress * 0.5
        frame = base.copy()
        frame[:, :, 0] = np.clip(frame[:, :, 0] * bright + red_boost, 0, 255)
        frame[:, :, 1] = np.clip(frame[:, :, 1] * bright, 0, 255)
        frame[:, :, 2] = np.clip(frame[:, :, 2] * bright, 0, 255)
        return frame.astype(np.uint8)

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip


# ---------------------------------------------------------------------------
# 15. Lower-third card — broadcast-style stat/info card
# ---------------------------------------------------------------------------

def lower_third_card(title_text, body_text, duration, W=1080,
                     card_h=220, y_pos=1280, fps=30,
                     bg_color=(5, 5, 18), accent_color=(220, 30, 30),
                     title_color=(220, 30, 30), body_color=(255, 255, 255),
                     slide_in_dur=0.28,
                     font_path="C:/Windows/Fonts/arialbd.ttf",
                     font_path_regular="C:/Windows/Fonts/arial.ttf"):
    """
    Professional broadcast lower-third: a dark card that slides up from below
    with a left red stripe, a bold title line, and a body text line.

    title_text : short label e.g. "BREAKING" or source name
    body_text  : main sentence from script
    y_pos      : top-y of the card in the parent frame
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC
    import os

    stripe_w = 8
    pad_l    = stripe_w + 18
    pad_tb   = 16

    # ── pre-render card image ─────────────────────────────────────────────────
    try:
        from PIL import Image, ImageDraw, ImageFont

        img  = Image.new("RGBA", (W, card_h), (bg_color[0], bg_color[1], bg_color[2], 225))
        draw = ImageDraw.Draw(img)

        # Accent stripe + top line
        draw.rectangle([(0, 0), (stripe_w, card_h)],
                       fill=(accent_color[0], accent_color[1], accent_color[2], 255))
        draw.rectangle([(0, 0), (W, 3)],
                       fill=(accent_color[0], accent_color[1], accent_color[2], 140))

        try:
            f_bold = ImageFont.truetype(font_path, 38) if os.path.exists(font_path) \
                else ImageFont.load_default()
            f_body = ImageFont.truetype(font_path_regular, 48) \
                if os.path.exists(font_path_regular) else f_bold
        except Exception:
            f_bold = f_body = ImageFont.load_default()

        # Title row
        draw.text((pad_l, pad_tb), title_text.upper(), font=f_bold,
                  fill=(title_color[0], title_color[1], title_color[2], 255))
        try:
            tbbox = draw.textbbox((0, 0), title_text.upper(), font=f_bold)
            title_h = tbbox[3] - tbbox[1]
        except Exception:
            title_h = 42

        # Body row — dynamic wrap
        usable_w = W - pad_l - 24
        wrap_chars = max(10, int(usable_w / (48 * 0.52)))
        wrapped_body = "\n".join(textwrap.wrap(body_text, width=wrap_chars))
        draw.multiline_text(
            (pad_l, pad_tb + title_h + 10), wrapped_body,
            font=f_body,
            fill=(body_color[0], body_color[1], body_color[2], 255),
            spacing=6,
        )
        card_arr = np.array(img)
    except Exception:
        card_arr = np.zeros((card_h, W, 4), dtype=np.uint8)
        card_arr[:, :, 0] = bg_color[0]
        card_arr[:, :, 1] = bg_color[1]
        card_arr[:, :, 2] = bg_color[2]
        card_arr[:, :, 3] = 220
        card_arr[:, :stripe_w, 0] = accent_color[0]
        card_arr[:, :stripe_w, 1] = accent_color[1]
        card_arr[:, :stripe_w, 2] = accent_color[2]
        card_arr[:, :stripe_w, 3] = 255

    def make_frame(t):
        progress = min(t / max(slide_in_dur, 1e-6), 1.0)
        ease     = _ease_out_cubic(progress)
        offset   = int(card_h * (1.0 - ease))   # slides up from below

        canvas = np.zeros((card_h, W, 4), dtype=np.uint8)
        src_y  = offset
        dst_y  = 0
        copy_h = card_h - offset
        if copy_h > 0:
            canvas[dst_y:dst_y + copy_h] = card_arr[src_y:src_y + copy_h]
        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    return clip.with_position(("center", y_pos))


# ---------------------------------------------------------------------------
# 16. Text pop-in — single stat/label that scales in with spring bounce
# ---------------------------------------------------------------------------

def text_pop_in(text, duration, W=1080, font_size=80,
                x=540, y=900, fps=30,
                text_color=(255, 220, 50),
                bg_color=None, bg_alpha=200,
                delay=0.0,
                font_path="C:/Windows/Fonts/arialbd.ttf"):
    """
    A single line of text that pops in with a spring-bounce scale from 0→1.
    Ideal for key stats, score reveals, percentage callouts.
    bg_color: optional tuple (R,G,B) for pill background; None = transparent.
    """
    if not MOVIEPY_OK:
        raise RuntimeError("MoviePy not available")

    from moviepy import VideoClip as VC
    import os

    spring_dur = 0.40

    # pre-render at 1× size
    try:
        from PIL import Image, ImageDraw, ImageFont
        _f = ImageFont.truetype(font_path, font_size) if os.path.exists(font_path) \
            else ImageFont.load_default()
        _tmp = Image.new("RGBA", (1, 1))
        _d   = ImageDraw.Draw(_tmp)
        try:
            _bb  = _d.textbbox((0, 0), text, font=_f)
            tw, th = _bb[2]-_bb[0], _bb[3]-_bb[1]
        except Exception:
            tw, th = font_size * len(text) // 2, font_size

        pad = 20
        cw  = tw + pad * 2
        ch  = th + pad * 2
        base_img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        bdraw    = ImageDraw.Draw(base_img)
        if bg_color:
            bdraw.rounded_rectangle([(0, 0), (cw-1, ch-1)], radius=ch//2,
                                    fill=(bg_color[0], bg_color[1], bg_color[2], bg_alpha))
        # Drop shadow
        bdraw.text((pad+2, pad+2), text, font=_f, fill=(0, 0, 0, 130))
        bdraw.text((pad, pad), text, font=_f,
                   fill=(text_color[0], text_color[1], text_color[2], 255))
        _base_arr = np.array(base_img)
        bh, bw = _base_arr.shape[:2]
    except Exception:
        bw, bh = font_size * max(len(text), 4), font_size + 30
        _base_arr = None

    canvas_size = max(bw, bh) * 2

    def make_frame(t):
        canvas = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)
        effective_t = max(0.0, t - delay)
        if effective_t <= 0 or _base_arr is None:
            return canvas

        if effective_t < spring_dur:
            scale = _ease_out_spring(effective_t / spring_dur, overshoot=0.15)
        else:
            scale = 1.0
        scale = max(0.01, min(scale, 1.2))

        try:
            from PIL import Image
            nw = max(2, int(bw * scale))
            nh = max(2, int(bh * scale))
            scaled = Image.fromarray(_base_arr).resize((nw, nh), Image.LANCZOS)
            arr    = np.array(scaled)
            dx = (canvas_size - nw) // 2
            dy = (canvas_size - nh) // 2
            x0 = max(0, dx);  y0 = max(0, dy)
            x1 = min(canvas_size, dx+nw); y1 = min(canvas_size, dy+nh)
            sx = x0-dx; sy = y0-dy
            canvas[y0:y1, x0:x1] = arr[sy:sy+(y1-y0), sx:sx+(x1-x0)]
        except Exception:
            pass
        return canvas

    clip = VC(make_frame, duration=duration)
    clip.fps = fps
    half = canvas_size // 2
    return clip.with_position((x - half, y - half))
