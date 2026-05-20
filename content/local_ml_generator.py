"""
content/local_ml_generator.py

Local ML replacement for the Claude API content generator.
Uses Flan-T5 models running entirely on CPU — no API keys, no internet at inference time.

Models used:
  - google/flan-t5-large  → script / narration (780 MB, ~2 GB RAM)
  - google/flan-t5-base   → title, description, tags (250 MB, ~1 GB RAM)
  - yake                  → fast keyword extraction for tags (pure Python, <1 MB)

First run: models are downloaded from HuggingFace and cached to ML_CACHE_DIR.
Subsequent runs: fully offline.
"""

import os
import re
import textwrap
from dataclasses import dataclass
from typing import Optional, List

from loguru import logger

# ── Lazy imports (heavy) ──────────────────────────────────────────────────────
# Imported inside functions/class __init__ so the module can be imported even
# if transformers/torch are not yet installed (e.g. during setup.py inspection).

# ---------------------------------------------------------------------------
# Re-export VideoContent so callers that import from here also get the class.
# ---------------------------------------------------------------------------
from content.ai_generator import VideoContent  # noqa: F401  (re-export)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Strip whitespace artifacts and normalize line breaks."""
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, max_chars: int) -> str:
    """Hard-truncate to max_chars, ending at a word boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    return truncated[:last_space].rstrip(".,:;") if last_space > 0 else truncated


def _split_into_segments(script: str) -> list[dict]:
    """
    Break the script into display segments for the video composer.
    Splits on sentence boundaries and labels by position.
    """
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    segments = []
    for i, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue
        if i == 0:
            style = "HEADLINE"
        elif i >= len(sentences) - 2:
            style = "CLOSING"
        elif i % 4 == 0:
            style = "EMPHASIS"
        else:
            style = "NARRATION"

        word_count = len(sent.split())
        duration_hint = max(3, round(word_count / 2.5))  # ~2.5 words/sec

        segments.append({
            "id": i + 1,
            "text": sent,
            "style": style,
            "duration_hint": duration_hint,
        })
    return segments


def _extract_tags_yake(text: str, source: str, category: str) -> list[str]:
    """
    Extract keyword tags using YAKE (no model needed — pure Python NLP).
    Falls back to simple word-frequency extraction if YAKE is not installed.
    """
    broad_tags = [
        "India news", "breaking news", "latest news", "news today",
        "Indian news", source, category,
    ]

    try:
        import yake
        kw_extractor = yake.KeywordExtractor(
            lan="en",
            n=2,           # up to bigrams
            dedupLim=0.7,
            top=20,
            features=None,
        )
        keywords = kw_extractor.extract_keywords(text)
        kw_tags = [kw for kw, _ in keywords if len(kw) <= 30]
    except ImportError:
        logger.warning("yake not installed — falling back to word-frequency tags.")
        words = re.findall(r"\b[A-Za-z]{4,}\b", text)
        freq: dict[str, int] = {}
        for w in words:
            freq[w.lower()] = freq.get(w.lower(), 0) + 1
        kw_tags = sorted(freq, key=freq.get, reverse=True)[:15]  # type: ignore[arg-type]

    # Combine: specific keywords first, then broad
    all_tags = kw_tags + [t for t in broad_tags if t not in kw_tags]
    # Deduplicate (case-insensitive) and enforce max-30-chars
    seen: set[str] = set()
    final: list[str] = []
    for tag in all_tags:
        key = tag.lower().strip()
        if key not in seen and len(tag) <= 30:
            seen.add(key)
            final.append(tag.strip())
        if len(final) >= 25:
            break
    return final


# ─────────────────────────────────────────────────────────────────────────────
# Sub-generators
# ─────────────────────────────────────────────────────────────────────────────

class _FlanT5Generator:
    """
    Wraps a single Flan-T5 model (pipeline) for text generation.
    Call .run(prompt, max_new_tokens) to get generated text.
    """

    def __init__(self, model_name: str, cache_dir: str, device: str = "cpu"):
        from transformers import pipeline as hf_pipeline
        logger.info(f"Loading model: {model_name}  (cache: {cache_dir})")
        self._pipe = hf_pipeline(
            "text2text-generation",
            model=model_name,
            tokenizer=model_name,
            model_kwargs={"cache_dir": cache_dir},
            device=device,
        )
        logger.success(f"Model loaded: {model_name}")

    def run(self, prompt: str, max_new_tokens: int = 256) -> str:
        result = self._pipe(
            prompt,
            max_new_tokens=max_new_tokens,
            do_sample=False,      # deterministic — consistent quality
            num_beams=2,          # reduced from 4 → saves ~50% RAM at inference
            early_stopping=True,
        )
        return _clean(result[0]["generated_text"])


# ─────────────────────────────────────────────────────────────────────────────
# Main generator class
# ─────────────────────────────────────────────────────────────────────────────

class LocalMLGenerator:
    """
    Drop-in replacement for AIContentGenerator.
    Same public interface: .generate(article) → VideoContent
    No API keys required. Runs fully on CPU.
    """

    SCRIPT_MODEL = "google/flan-t5-base"   # flan-t5-large needs >2 GB RAM; use base for 8 GB machines
    META_MODEL   = "google/flan-t5-base"

    def __init__(
        self,
        script_model: Optional[str] = None,
        meta_model: Optional[str] = None,
        cache_dir: Optional[str] = None,
        device: str = "cpu",
    ):
        self._script_model_name = (
            script_model
            or os.environ.get("ML_MODEL_SCRIPT", self.SCRIPT_MODEL)
        )
        self._meta_model_name = (
            meta_model
            or os.environ.get("ML_MODEL_META", self.META_MODEL)
        )
        self._cache_dir = (
            cache_dir
            or os.environ.get("ML_CACHE_DIR", "./models")
        )
        self._device = device or os.environ.get("ML_DEVICE", "cpu")
        self._max_script_tokens = int(os.environ.get("ML_MAX_NEW_TOKENS", "512"))

        os.makedirs(self._cache_dir, exist_ok=True)

        logger.info("Initialising local ML generator (Flan-T5)...")
        self._script_gen = _FlanT5Generator(
            self._script_model_name, self._cache_dir, self._device
        )

        # If the same model is requested for both, reuse the instance.
        if self._meta_model_name == self._script_model_name:
            self._meta_gen = self._script_gen
        else:
            self._meta_gen = _FlanT5Generator(
                self._meta_model_name, self._cache_dir, self._device
            )

        logger.success("LocalMLGenerator ready.")

    # ── Public API (matches AIContentGenerator) ───────────────────────────────

    def generate(self, article) -> Optional[VideoContent]:
        """Generate full YouTube video content from a NewsArticle."""
        logger.info(f"[LocalML] Generating content for: {article.title[:60]}...")
        try:
            title       = self._generate_title(article)
            script      = self._generate_script(article)
            description = self._generate_description(article, title)
            thumb_headline, thumb_sub = self._generate_thumbnail_text(article, title)
            tags        = _extract_tags_yake(
                article.title + " " + article.content[:800],
                article.source,
                article.category,
            )
            segments    = _split_into_segments(script)
            duration    = self._estimate_duration(script)

            vc = VideoContent(
                article_id=article.id,
                title=title,
                script=script,
                script_segments=segments,
                description=description,
                tags=tags,
                thumbnail_headline=thumb_headline,
                thumbnail_subtext=thumb_sub,
                category=article.category,
                estimated_duration=duration,
                source_url=article.url,
                source_name=article.source,
            )
            logger.success(f"[LocalML] Done — title: {title[:70]}")
            return vc

        except Exception as exc:
            logger.error(f"[LocalML] Generation failed: {exc}")
            return self._fallback(article)

    # ── Script generation ─────────────────────────────────────────────────────

    def _generate_script(self, article) -> str:
        """
        Generate a broadcast-style news narration using multi-pass section prompts.

        flan-t5-base (250 M params) works best with short, focused instructions.
        Rather than asking it to write 400 words in one shot (which causes repetition
        and truncation), we call it 5 times — once per script section — then join.

        Sections: Hook → Context → Key Facts → Implications → Closing
        """
        content_snippet = article.content[:1500].strip()
        title           = article.title
        source          = article.source
        category        = article.category.capitalize()

        sections = []

        # 1. Hook — one punchy opening sentence
        hook = self._script_gen.run(
            f"Write one dramatic opening sentence for an Indian TV news broadcast about: {title}.",
            max_new_tokens=60,
        )
        sections.append(hook)

        # 2. Background — 2-3 context sentences
        ctx = self._script_gen.run(
            f"Provide 2 sentences of background context for a news report. "
            f"Topic: {title}. Source: {source}. Category: {category}.",
            max_new_tokens=100,
        )
        sections.append(ctx)

        # 3. Key Facts — summarize the article body
        facts = self._script_gen.run(
            f"Summarize these key facts in 3 clear sentences for a TV news script:\n{content_snippet[:800]}",
            max_new_tokens=150,
        )
        sections.append(facts)

        # 4. Implications / Reactions — what does it mean?
        impl = self._script_gen.run(
            f"In 2 sentences, explain the implications or reactions to this news: {title}.",
            max_new_tokens=90,
        )
        sections.append(impl)

        # 5. Closing — sign-off
        closing = self._script_gen.run(
            f"Write one professional closing sentence for a TV news segment about: {title}. "
            f"End with 'Stay tuned for more updates.' or similar.",
            max_new_tokens=50,
        )
        sections.append(closing)

        # Join, deduplicate consecutive identical sentences, clean
        script = self._join_sections(sections)
        logger.debug(f"[LocalML] Script word count: {len(script.split())}")
        return script

    def _join_sections(self, sections: list[str]) -> str:
        """Join section outputs, removing obvious repetition between sections."""
        # Split each section into sentences
        all_sentences: list[str] = []
        seen_sentences: set[str] = set()

        for section in sections:
            section = section.strip()
            if not section:
                continue
            # Split on sentence boundaries
            sents = re.split(r'(?<=[.!?])\s+', section)
            for s in sents:
                s = s.strip()
                if not s:
                    continue
                # Dedup by normalized form (lowercase, no punct)
                norm = re.sub(r'[^a-z0-9 ]', '', s.lower()).strip()
                if norm and norm not in seen_sentences:
                    seen_sentences.add(norm)
                    all_sentences.append(s)

        # Ensure ends with a period
        script = ' '.join(all_sentences)
        if script and not script[-1] in '.!?':
            script += '.'
        return _clean(script)

    def _expand_script_fallback(self, article, short_script: str) -> str:
        """
        Emergency fallback only — called if _generate_script raises an exception.
        Concatenates article content directly into a readable script.
        """
        logger.warning("[LocalML] Using emergency rule-based script fallback.")
        parts = [
            f"Breaking news — {article.title}.",
            article.content[:600].strip(),
            f"This report comes from {article.source}.",
            "Stay tuned for more updates on this developing story.",
        ]
        return _clean(' '.join(p.strip() for p in parts if p.strip()))

    # ── Title generation ──────────────────────────────────────────────────────

    def _generate_title(self, article) -> str:
        """Generate an SEO-optimized YouTube title ≤ 90 chars."""
        prompt = (
            f"Write a YouTube video title for an Indian news channel. "
            f"Requirements: professional news style (like NDTV or Times Now), "
            f"SEO-optimized, max 90 characters, no clickbait, no emojis, compelling but factual.\n\n"
            f"News headline: {article.title}\n"
            f"Source: {article.source}\n"
            f"Category: {article.category}\n\n"
            f"YouTube title:"
        )
        raw = self._meta_gen.run(prompt, max_new_tokens=40)
        title = re.sub(r"^(youtube title:?\s*)", "", raw, flags=re.IGNORECASE).strip()
        title = _truncate(title, 90)

        # Final fallback: use original headline if model output is garbage
        if len(title) < 10:
            title = _truncate(article.title, 90)
        return title

    # ── Description generation ────────────────────────────────────────────────

    def _generate_description(self, article, title: str) -> str:
        """Generate a full YouTube description with summary, bullets, and hashtags."""
        # Generate the summary paragraph via model
        summary_prompt = (
            f"Write a 2–3 sentence YouTube video description summary for a news video titled: '{title}'. "
            f"The article is from {article.source} about {article.category}. "
            f"Be informative and professional. No emojis.\n\n"
            f"Article excerpt: {article.content[:600]}\n\n"
            f"Description summary:"
        )
        summary = self._meta_gen.run(summary_prompt, max_new_tokens=120)
        summary = re.sub(r"^(description summary:?\s*)", "", summary, flags=re.IGNORECASE).strip()

        # Build key-points bullet list from article content sentences
        sentences = re.split(r"(?<=[.!?])\s+", article.content[:1200])
        bullets = []
        for s in sentences:
            s = s.strip()
            if 30 < len(s) < 200:
                bullets.append(f"• {s}")
            if len(bullets) >= 5:
                break

        bullet_block = "\n".join(bullets) if bullets else ""

        # Compose full description
        channel_name = os.environ.get("CHANNEL_NAME", "News Channel")
        hashtags = self._build_hashtags(article)

        description = textwrap.dedent(f"""\
            {summary}

            📌 KEY POINTS:
            {bullet_block}

            ⏱️ TIMESTAMPS:
            00:00 Introduction
            00:10 Background
            00:45 Key Facts
            01:30 Analysis
            02:00 Conclusion

            📰 SOURCE: {article.source}
            🔗 Original Article: {article.url}

            ─────────────────────────────────────
            Stay informed with {channel_name} — your trusted source for the latest Indian and world news.

            👍 Like | 💬 Comment | 🔔 Subscribe for daily news updates.
            ─────────────────────────────────────

            {hashtags}
        """).strip()

        return _truncate(description, 4500)

    def _build_hashtags(self, article) -> str:
        """Build a string of YouTube hashtags from category, source, and title words."""
        tags = [
            f"#{article.category.replace(' ', '')}",
            "#IndiaNews",
            "#BreakingNews",
            "#LatestNews",
            f"#{article.source.replace(' ', '')}",
        ]
        # Add title words as hashtags (capitalized, no special chars)
        for word in article.title.split():
            word = re.sub(r"[^A-Za-z0-9]", "", word)
            if len(word) >= 4:
                tags.append(f"#{word.capitalize()}")
            if len(tags) >= 10:
                break
        return " ".join(dict.fromkeys(tags))  # deduplicated, order-preserving

    # ── Thumbnail text ────────────────────────────────────────────────────────

    def _generate_thumbnail_text(self, article, title: str) -> tuple[str, str]:
        """Generate a punchy thumbnail headline and one-line subtext."""
        prompt = (
            f"Write a very short 5–7 word thumbnail headline for a YouTube news video. "
            f"ALL CAPS. Punchy and urgent but not clickbait. Factual.\n\n"
            f"News: {article.title}\n\n"
            f"Thumbnail headline:"
        )
        raw = self._meta_gen.run(prompt, max_new_tokens=25)
        headline = re.sub(r"^(thumbnail headline:?\s*)", "", raw, flags=re.IGNORECASE).strip().upper()
        headline = _truncate(headline, 50)
        if len(headline) < 5:
            # Fallback: uppercase first 6 words of title
            headline = " ".join(article.title.split()[:6]).upper()

        # Subtext = source + category
        subtext = f"{article.source} | {article.category.capitalize()}"
        subtext = _truncate(subtext, 40)

        return headline, subtext

    # ── Duration estimate ─────────────────────────────────────────────────────

    @staticmethod
    def _estimate_duration(script: str) -> int:
        """Estimate video duration from word count at ~2.5 words/second narration speed."""
        word_count = len(script.split())
        duration = round(word_count / 2.5) + 10  # +10s for intro/outro
        return max(90, min(300, duration))  # clamp to 90–300 seconds

    # ── Fallback (no model) ───────────────────────────────────────────────────

    def _fallback(self, article) -> VideoContent:
        """
        Rule-based fallback when model generation completely fails.
        Produces usable (but basic) content from raw article data.
        """
        logger.warning("[LocalML] Using rule-based fallback for content generation.")
        title = _truncate(article.title, 90)
        script = (
            f"In today's top story — {article.title}. "
            f"{article.content[:600].strip()} "
            f"This story was reported by {article.source}. Stay tuned for more updates."
        )
        tags = _extract_tags_yake(
            article.title + " " + article.content[:400],
            article.source,
            article.category,
        )
        description = (
            f"{article.title}\n\n"
            f"Source: {article.source}\n"
            f"Category: {article.category}\n"
            f"Read more: {article.url}\n\n"
            f"#IndiaNews #BreakingNews #{article.category.capitalize()}"
        )
        return VideoContent(
            article_id=article.id,
            title=title,
            script=script,
            script_segments=_split_into_segments(script),
            description=description,
            tags=tags,
            thumbnail_headline=" ".join(article.title.split()[:6]).upper(),
            thumbnail_subtext=f"{article.source} | {article.category.capitalize()}",
            category=article.category,
            estimated_duration=self._estimate_duration(script),
            source_url=article.url,
            source_name=article.source,
        )
