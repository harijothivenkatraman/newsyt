"""
trending_filter.py
==================
Scores and filters news articles by relevance to a list of trending topics.

Usage
-----
    from trending_filter import TrendingFilter

    tf = TrendingFilter(["Supreme Court bail", "Sensex surge", "IPL 2026"])
    filtered = tf.filter_articles(articles, threshold=0.35, top_n=35)
    # Returns articles sorted by relevance score, highest first
"""

from __future__ import annotations

import re
import math
from typing import Optional
from dataclasses import dataclass

from loguru import logger


# ── Text helpers ───────────────────────────────────────────────────────────────

_NOISE   = re.compile(r"[^a-zA-Z0-9\s]")
_SPACES  = re.compile(r"\s+")

# Common English + Indian-news stop words — not useful for matching
_STOP = {
    "a","an","the","in","on","at","by","for","of","to","and","or","is","are",
    "was","were","be","been","has","have","had","this","that","with","from",
    "its","it","as","into","how","why","what","who","when","up","out","over",
    "after","before","news","latest","breaking","today","live","watch","full",
    "new","top","big","most","more","also","than","about","vs","amp","says",
    "said","will","can","now","just","his","her","their","our","your","we",
    "he","she","they","not","no","do","did","but","so","if","all","one","two",
    "three","year","years","day","days","time","india","indian","report",
    "government","minister","state","national","local","amid","while","after",
    "first","last","next","pm","am","rs","crore","lakh",
}


def _tokenise(text: str) -> list[str]:
    """Lowercase, remove punctuation, split on whitespace, drop stop words."""
    clean = _SPACES.sub(" ", _NOISE.sub(" ", text.lower())).strip()
    return [t for t in clean.split() if t not in _STOP and len(t) > 2]


def _ngrams(tokens: list[str], n: int) -> set[str]:
    return {" ".join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)}


# ── Scorer ─────────────────────────────────────────────────────────────────────

@dataclass
class ScoredArticle:
    article: object          # NewsArticle dataclass
    score: float             # 0.0 – 1.0
    matched_topics: list[str]


class TrendingFilter:
    """
    Scores articles against a set of trending topic strings.

    Scoring logic (per article):
      - Build a "haystack" from article title + first 300 chars of content
      - For each trending topic:
          * Exact phrase match in haystack      → 1.0 points
          * All topic tokens present            → 0.7 points
          * ≥ 50% topic tokens present          → 0.4 points
          * Any topic token present             → 0.15 points
      - Article score = max(matched topic scores), boosted by:
          * title match (×1.4 bonus)
          * multiple topic hits (+0.1 per extra topic above 1, capped at +0.3)
      - Final score is clamped to [0.0, 1.0]
    """

    def __init__(self, topics: list[str]):
        self.topics = topics
        # Pre-tokenise every topic into its tokens
        self._topic_tokens: list[list[str]] = [_tokenise(t) for t in topics]
        # Pre-compute phrase versions (lowered, space-normalised)
        self._topic_phrases: list[str] = [
            _SPACES.sub(" ", _NOISE.sub(" ", t.lower())).strip()
            for t in topics
        ]

    # ── Internal scoring ──────────────────────────────────────────────────────

    def _score_one(self, article) -> tuple[float, list[str]]:
        title_raw   = getattr(article, "title",   "") or ""
        content_raw = getattr(article, "content", "") or ""

        title_hay   = _SPACES.sub(" ", _NOISE.sub(" ", title_raw.lower()))
        content_hay = _SPACES.sub(" ", _NOISE.sub(" ", content_raw[:400].lower()))
        full_hay    = title_hay + " " + content_hay

        title_tokens   = set(_tokenise(title_raw))
        full_tokens    = set(_tokenise(full_hay))
        full_bigrams   = _ngrams(list(full_tokens), 2)
        title_bigrams  = _ngrams(list(title_tokens), 2)

        best_score   = 0.0
        matched      = []

        for phrase, tok_list in zip(self._topic_phrases, self._topic_tokens):
            if not tok_list:
                continue

            topic_set = set(tok_list)
            n_topic   = len(topic_set)

            # ── Exact phrase match ────────────────────────────────────────────
            in_title   = phrase in title_hay
            in_content = phrase in full_hay
            if in_title or in_content:
                raw = 1.0 if in_title else 0.85
                best_score = max(best_score, raw)
                matched.append(phrase)
                continue

            # ── Token overlap ─────────────────────────────────────────────────
            overlap      = topic_set & full_tokens
            title_overlap = topic_set & title_tokens
            frac         = len(overlap) / n_topic

            if frac == 0:
                continue

            # Bigram boost: if a bigram from the topic appears in text
            topic_bigrams = _ngrams(tok_list, 2) if len(tok_list) >= 2 else set()
            bigram_hit    = bool(topic_bigrams & full_bigrams)
            title_bigram  = bool(topic_bigrams & title_bigrams)

            if frac >= 1.0:
                raw = 0.75
            elif frac >= 0.66:
                raw = 0.55
            elif frac >= 0.5:
                raw = 0.40
            else:
                raw = 0.18

            # Title match bonus
            if title_overlap:
                title_frac = len(title_overlap) / n_topic
                raw += title_frac * 0.25

            # Bigram bonus
            if bigram_hit:
                raw += 0.10
            if title_bigram:
                raw += 0.10

            raw = min(raw, 1.0)
            if raw >= 0.15:
                best_score = max(best_score, raw)
                matched.append(phrase)

        # Multi-topic bonus
        n_matched = len(matched)
        if n_matched > 1:
            best_score = min(1.0, best_score + 0.08 * min(n_matched - 1, 3))

        return round(best_score, 4), matched

    # ── Public API ────────────────────────────────────────────────────────────

    def score(self, article) -> tuple[float, list[str]]:
        """Return (score, matched_topics) for a single article."""
        return self._score_one(article)

    def filter_articles(
        self,
        articles: list,
        threshold: float = 0.35,
        top_n: int = 35,
        min_articles: int = 8,
    ) -> list:
        """
        Score all articles against trending topics, return top_n above threshold.

        If fewer than min_articles pass the threshold, the threshold is
        automatically lowered to 0.15 to guarantee a minimum output.
        """
        if not self.topics:
            logger.warning("[TrendingFilter] No topics — returning all articles unfiltered")
            return articles[:top_n]

        scored = [ScoredArticle(a, *self._score_one(a)) for a in articles]
        scored.sort(key=lambda x: x.score, reverse=True)

        above = [s for s in scored if s.score >= threshold]

        # Safety net: if too few pass, lower the bar
        if len(above) < min_articles:
            logger.info(
                f"[TrendingFilter] Only {len(above)} articles above {threshold:.2f} — "
                f"lowering threshold to 0.15"
            )
            above = [s for s in scored if s.score >= 0.15]

        # Hard fallback: if still too few (very niche topics), return top scored
        if len(above) < max(3, min_articles // 2):
            logger.warning(
                "[TrendingFilter] Trending topics too niche — returning top scored articles"
            )
            above = scored[:top_n]

        result = [s.article for s in above[:top_n]]

        # Log summary
        logger.info(
            f"[TrendingFilter] {len(articles)} scraped → "
            f"{len(result)} passed filter "
            f"(top score={scored[0].score:.2f} for '{getattr(scored[0].article, 'title', '')[:50]}')"
        )
        if above:
            logger.debug(
                "[TrendingFilter] Top 5 matched articles:\n" +
                "\n".join(
                    f"  [{s.score:.2f}] {getattr(s.article,'title','')[:60]} "
                    f"← {s.matched_topics[:3]}"
                    for s in above[:5]
                )
            )

        return result


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from dataclasses import dataclass as dc

    @dc
    class FakeArticle:
        title: str
        content: str
        source: str = "Test"
        category: str = "general"

    topics = [
        "Supreme Court bail",
        "Sensex surge",
        "IPL 2026 cricket",
        "UGC NET exam",
        "election results",
    ]
    articles = [
        FakeArticle("Supreme Court denies bail to cyber criminal", "The apex court rejected the plea..."),
        FakeArticle("Sensex surges 500 points on positive global cues", "Markets hit record high..."),
        FakeArticle("IPL 2026: Mumbai Indians win thrilling match", "MI defeated CSK by 4 wickets..."),
        FakeArticle("UGC NET 2026 exam guidelines released", "Candidates must report 90 min early..."),
        FakeArticle("School bans lipstick in Kerala", "A school in Kollam declared war on makeup..."),
        FakeArticle("Random celebrity gossip story", "Actor spotted at airport..."),
        FakeArticle("Budget 2026 analysis", "Finance minister announced key changes..."),
    ]

    tf = TrendingFilter(topics)
    print(f"Topics: {topics}\n")
    for a in articles:
        score, matched = tf.score(a)
        print(f"  [{score:.2f}] {a.title[:55]:55s}  <- {matched}")

    print("\n--- Filtered (threshold=0.35) ---")
    filtered = tf.filter_articles(articles, threshold=0.35, top_n=10)
    for a in filtered:
        print(f"  [OK] {a.title}")
