"""
content/ai_generator.py

Contains:
  - VideoContent dataclass  (shared by all generators)
  - AIContentGenerator      (Claude / Anthropic API — optional, legacy)
  - get_generator()         (factory — selects local ML or Claude based on env)

Set USE_LOCAL_ML=true in .env to use the free, offline Flan-T5 generator.
Set USE_LOCAL_ML=false (and provide ANTHROPIC_API_KEY) to use Claude.
"""

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from loguru import logger

# anthropic is imported lazily inside AIContentGenerator.__init__
# so this module loads cleanly when USE_LOCAL_ML=true (no anthropic package needed).


@dataclass
class VideoContent:
    article_id: str
    title: str                      # YouTube video title (≤ 100 chars)
    script: str                     # Full narration script (~300-500 words)
    short_script: str               # Short ~60-word script for Shorts
    script_segments: list[dict]     # [{text, duration_hint, style}]
    description: str                # YouTube description (≤ 5000 chars)
    tags: list[str]                 # YouTube tags
    thumbnail_headline: str         # Short punchy headline for thumbnail
    thumbnail_subtext: str          # 1-line subtext on thumbnail
    category: str
    estimated_duration: int         # seconds
    source_url: str = ""
    source_name: str = ""

    def to_dict(self):
        return asdict(self)


class AIContentGenerator:
    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: Optional[str] = None):
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is not installed. "
                "Install it with: pip install anthropic\n"
                "Or switch to the free local ML generator by setting USE_LOCAL_ML=true in .env"
            )
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Either set the key or switch to the free local ML generator "
                "by adding USE_LOCAL_ML=true to your .env file."
            )
        self.client = _anthropic.Anthropic(api_key=key)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, article) -> Optional[VideoContent]:
        """Generate full YouTube video content from a NewsArticle."""
        logger.info(f"Generating content for: {article.title[:60]}...")
        try:
            raw = self._call_claude(article)
            parsed = self._parse_response(raw, article)
            return parsed
        except Exception as e:
            logger.error(f"AI generation failed: {e}")
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _call_claude(self, article) -> str:
        system = """You are an expert YouTube news channel producer for an Indian news channel.
Your job is to transform raw news articles into professional broadcast-quality video content.
You write in an authoritative yet engaging news anchor style similar to NDTV, Times Now, or BBC News.
Always respond ONLY with a valid JSON object — no markdown, no backticks, no preamble."""

        prompt = f"""
Transform this news article into complete YouTube video content.

ARTICLE:
Source: {article.source}
Category: {article.category}
Title: {article.title}
Author: {article.author}
Published: {article.published_at}
URL: {article.url}

CONTENT:
{article.content[:3000]}

Generate a JSON object with EXACTLY these fields:

{{
  "title": "YouTube video title — max 90 chars, SEO-optimized, no clickbait, professional news style. Include year/date context if relevant.",
  
  "script": "Full narration script 300-500 words. Professional TV news anchor tone. Start with a strong hook. Use short punchy sentences. Structure: Hook → Context → Details → Implications → Closing. No emojis.",

  "short_script": "EXACTLY 95–110 words. This is spoken aloud at normal pace = 40–50 seconds. Structure: 1) Hook sentence (attention-grabbing, 10 words max). 2) Important details covering who, what, why it matters in depth (75-85 words total). 3) CTA: 'Like and subscribe for more news updates.' Fast, direct, no filler words. No emojis.",
  
  "script_segments": [
    {{
      "id": 1,
      "text": "Opening hook sentence (10-15 words)",
      "style": "HEADLINE",
      "duration_hint": 4
    }},
    {{
      "id": 2,
      "text": "Context paragraph...",
      "style": "NARRATION",
      "duration_hint": 20
    }}
    // continue for all segments... styles: HEADLINE | NARRATION | EMPHASIS | CLOSING
  ],
  
  "description": "Full YouTube description. Include: 1) 2-3 line summary, 2) Key points as bullet list, 3) Timestamps (00:00 Intro, etc.), 4) Source attribution, 5) Subscribe CTA, 6) Relevant hashtags at end. Max 4500 chars.",
  
  "tags": ["tag1", "tag2", ...],  // 15-25 tags. Mix: specific (article topic) + broad (India news, breaking news) + trending. Each tag max 30 chars.
  
  "thumbnail_headline": "5-8 word PUNCHY headline for thumbnail graphic. ALL CAPS OK.",
  
  "thumbnail_subtext": "One line subtext under headline, max 40 chars.",
  
  "estimated_duration": 50  // target individual short duration in seconds (45-55)
}}

Rules:
- Title must NOT be clickbait but MUST be compelling
- Script must read naturally when spoken aloud
- Tags should include the source publication name
- Description hashtags at the END only
- thumbnail_headline should create urgency/curiosity WITHOUT being misleading
"""
        
        seo_context = os.environ.get("CHANNEL_SEO_CONTEXT", "")
        if seo_context:
            prompt += f"\n\n--- DYNAMIC SEO CONTEXT ---\n{seo_context}\n---------------------------\n"

        response = self.client.messages.create(
            model=self.MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _parse_response(self, raw: str, article) -> VideoContent:
        # Strip any accidental markdown fences
        cleaned = re.sub(r"```json|```", "", raw).strip()
        data = json.loads(cleaned)

        return VideoContent(
            article_id=article.id,
            title=data.get("title", article.title)[:100],
            script=data.get("script", ""),
            short_script=data.get("short_script", data.get("script", "")[:400]),
            script_segments=data.get("script_segments", []),
            description=data.get("description", ""),
            tags=data.get("tags", [])[:30],
            thumbnail_headline=data.get("thumbnail_headline", article.title[:40].upper()),
            thumbnail_subtext=data.get("thumbnail_subtext", article.source),
            category=article.category,
            estimated_duration=int(data.get("estimated_duration", 180)),
            source_url=article.url,
            source_name=article.source,
        )


# ── Generator factory ─────────────────────────────────────────────────────────

def get_generator(use_local: Optional[bool] = None):
    """
    Factory function — returns the appropriate content generator.

    Priority:
      1. Explicit ``use_local`` argument.
      2. USE_LOCAL_ML environment variable ("true" / "false").
      3. Default: LocalMLGenerator (no API key required).

    Returns:
      LocalMLGenerator  — if use_local is True or USE_LOCAL_ML=true
      AIContentGenerator — if use_local is False and ANTHROPIC_API_KEY is set
    """
    if use_local is None:
        env_val = os.environ.get("USE_LOCAL_ML", "true").strip().lower()
        use_local = env_val not in ("false", "0", "no")

    if use_local:
        from content.local_ml_generator import LocalMLGenerator
        return LocalMLGenerator()
    else:
        return AIContentGenerator()
