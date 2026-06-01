"""
article_ranker.py
Scores and ranks articles from the queue for daily and weekly bundle Shorts.

Scoring formula (0–100):
  - Recency      50 pts  — linear decay over 7 days (24 h = 50, 7 d = 0)
  - Source tier  30 pts  — major outlets score 30, mid-tier 18, others 8
  - Keywords     20 pts  — importance keyword bonus (cumulative, capped at 20)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from loguru import logger

import article_queue as queue


# ── Source tier map ──────────────────────────────────────────────────────────

SOURCE_TIERS: dict[str, int] = {
    # Tier 1 — 30 pts
    "the hindu":          30,
    "ndtv":               30,
    "times of india":     30,
    "hindustan times":    30,
    "bbc":                30,
    "reuters":            30,
    "the wire":           28,
    # Tier 2 — 18 pts
    "india today":        18,
    "economic times":     18,
    "the print":          18,
    "mint":               18,
    "scroll":             18,
    "the quint":          18,
    "al jazeera":         18,
    # Tier 3 — 8 pts (default)
}

DEFAULT_SOURCE_SCORE = 8

# ── Importance keywords ──────────────────────────────────────────────────────

KEYWORD_SCORES: list[tuple[list[str], int]] = [
    # (keywords, bonus_points)
    (["breaking", "exclusive", "just in", "urgent"], 8),
    (["prime minister", "pm modi", "president", "supreme court", "parliament"], 6),
    (["economy", "gdp", "inflation", "rbi", "budget", "fiscal"], 5),
    (["war", "conflict", "ceasefire", "attack", "explosion", "terror"], 5),
    (["election", "vote", "poll", "manifesto", "campaign"], 4),
    (["india", "bharatiya", "bharat"], 3),
    (["climate", "earthquake", "flood", "cyclone", "disaster"], 4),
    (["technology", "ai", "artificial intelligence", "space", "isro"], 3),
]

MAX_KEYWORD_SCORE = 20


class ArticleRanker:
    """Ranks articles from the persistent queue for bundle Shorts."""

    def __init__(self, now: Optional[datetime] = None):
        self._now = now or datetime.now(timezone.utc)

    # ── Public API ────────────────────────────────────────────────────────────

    def rank_daily(self, n: int = 30) -> list:
        """
        Return the top-n articles published in the last 24 hours,
        sorted by composite score (highest first).
        Returns a list of article stubs (dicts from the queue).
        """
        cutoff = self._now - timedelta(hours=24)
        return self._rank(cutoff=cutoff, n=n)

    def rank_weekly(self, n: int = 100) -> list:
        """
        Return the top-n articles published in the last 7 days,
        sorted by composite score (highest first).
        """
        cutoff = self._now - timedelta(days=7)
        return self._rank(cutoff=cutoff, n=n)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rank(self, cutoff: datetime, n: int) -> list:
        all_stubs = queue.get_all_stubs()  # returns list[dict]
        if not all_stubs:
            logger.warning("ArticleRanker: queue is empty.")
            return []

        scored: list[tuple[float, dict]] = []
        for stub in all_stubs:
            pub_str = stub.get("published_at", "") or stub.get("scraped_at", "")
            pub_dt = self._parse_dt(pub_str)
            if pub_dt and pub_dt < cutoff:
                continue  # outside the time window

            score = self._score(stub, pub_dt)
            scored.append((score, stub))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        result = [stub for _, stub in scored[:n]]
        logger.info(
            f"ArticleRanker: ranked {len(all_stubs)} articles → "
            f"kept {len(result)} (cutoff={cutoff.isoformat()}, n={n})"
        )
        return result

    def _score(self, stub: dict, pub_dt: Optional[datetime]) -> float:
        total = 0.0

        # 1. Recency (0–50)
        if pub_dt:
            age_hours = (self._now - pub_dt).total_seconds() / 3600
            # Linear decay: 0 h → 50 pts, 168 h (7 d) → 0 pts
            recency = max(0.0, 50.0 * (1 - age_hours / 168))
        else:
            recency = 0.0
        total += recency

        # 2. Source tier (0–30)
        source_raw = (stub.get("source") or "").lower().strip()
        source_score = DEFAULT_SOURCE_SCORE
        for name, pts in SOURCE_TIERS.items():
            if name in source_raw:
                source_score = pts
                break
        total += source_score

        # 3. Keyword importance (0–20)
        text = " ".join([
            stub.get("title", ""),
            stub.get("content", "")[:500],
        ]).lower()
        kw_score = 0
        for keywords, pts in KEYWORD_SCORES:
            if any(kw in text for kw in keywords):
                kw_score += pts
        total += min(kw_score, MAX_KEYWORD_SCORE)

        return total

    def _parse_dt(self, dt_str: str) -> Optional[datetime]:
        if not dt_str:
            return None
        # Try common ISO formats
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(dt_str[:19], fmt[:len(fmt)])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None
