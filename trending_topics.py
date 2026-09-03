"""
trending_topics.py
==================
Fetches what topics are currently MOST trending globally — across ALL categories,
not just news. Sports events (FIFA, IPL), entertainment (movies, celebs), tech
launches, viral moments — all are considered.

Signal priority order:
  1. YouTube Trending Chart   — videos.list(chart=mostPopular) per region
                                This IS the YouTube homepage trending page.
                                Covers sports, music, gaming, entertainment, news.
  2. YouTube Category Sweeps  — search.list across 8 high-traffic category IDs
  3. Google Trends RSS         — all search categories, multi-region
  4. Currents API / TheNewsAPI — key-based news trending signals (if keys set)
  5. Curated seed list         — global fallback keywords

Results are merged, cross-signal topics ranked higher, cached 30 min.
"""

from __future__ import annotations

import json
import os
import re
import time
import math
import requests
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Constants ─────────────────────────────────────────────────────────────────

CACHE_PATH        = Path("./logs/trending_cache.json")
CACHE_TTL_MINUTES = 30
MIN_TOPICS        = 10
MAX_TOPICS        = 40

DEFAULT_REGIONS   = ["IN", "US", "GB", "AU", "CA"]

# Google Trends base — use the realtime trending stories endpoint (not the deprecated RSS)
GOOGLE_TRENDS_BASE      = "https://trends.google.com/trends/trendingsearches/daily/rss?geo="
GOOGLE_TRENDS_REALTIME  = "https://trends.google.com/trends/api/realtimetrends"

# YouTube video category IDs to sweep (all high-traffic categories)
YT_CATEGORY_SWEEPS = {
    "20": "Gaming",
    "17": "Sports",
    "10": "Music",
    "24": "Entertainment",
    "25": "News & Politics",
    "28": "Science & Technology",
    "22": "People & Blogs",
    "23": "Comedy",
    "19": "Travel",
    "26": "How-to & Style",
}

# Seed — global viral event keywords; updated frequently
SEED_TOPICS: list[str] = [
    "FIFA World Cup 2026", "IPL cricket 2025", "Champions League",
    "Formula 1 race", "UFC fight", "Olympic Games",
    "Bitcoin price", "stock market crash", "inflation economy",
    "artificial intelligence ChatGPT", "iPhone launch", "Samsung Galaxy",
    "Bollywood box office", "Hollywood blockbuster", "Netflix series",
    "election results", "Supreme Court verdict", "government policy",
    "climate change", "NASA space discovery", "COVID health",
    "celebrity controversy", "viral video", "breaking news",
]


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> Optional[list[str]]:
    try:
        if not CACHE_PATH.exists():
            return None
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        age_min = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60
        if age_min < CACHE_TTL_MINUTES:
            topics = data.get("topics", [])
            if topics:
                logger.debug(
                    f"[Trending] Cache hit — {len(topics)} topics, "
                    f"{age_min:.0f} min old"
                )
                return topics
    except Exception as e:
        logger.debug(f"[Trending] Cache read failed: {e}")
    return None


def _save_cache(topics: list[str], source: str) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "topics": topics,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"[Trending] Saved {len(topics)} topics (source={source})")
    except Exception as e:
        logger.debug(f"[Trending] Cache write failed: {e}")


# ── Keyword extraction ─────────────────────────────────────────────────────────

_STOP = {
    "a", "an", "the", "in", "on", "at", "by", "for", "of", "to", "and",
    "or", "is", "are", "was", "were", "be", "been", "has", "have", "had",
    "this", "that", "with", "from", "its", "it", "as", "into", "how",
    "why", "what", "who", "when", "up", "out", "over", "after", "before",
    "latest", "today", "live", "watch", "full", "new", "top", "big",
    "most", "more", "also", "than", "about", "vs", "amp", "ft", "ft.",
    "official", "video", "highlights", "review", "reaction", "shorts",
}
_NOISE_PAT = re.compile(r"[^a-zA-Z0-9\s\-'àáâãäåèéêëìíîïòóôõöùúûü]")


def _extract_keywords(titles: list[str]) -> list[str]:
    """
    Extract trending keyword phrases from titles.
    Returns deduplicated list ordered by frequency.
    Also preserves FULL short titles (<=5 words) as-is for viral events
    like 'FIFA World Cup 2026' or 'Champions League Final'.
    """
    phrase_counts: Counter = Counter()

    for title in titles:
        # Keep short titles verbatim (these are likely viral event names)
        words = title.strip().split()
        if 2 <= len(words) <= 5:
            phrase_counts[title.strip()] += 2  # boost weight

        clean = _NOISE_PAT.sub(" ", title).lower()
        tokens = [t for t in clean.split() if t and t not in _STOP and len(t) > 2]

        for tok in tokens:
            phrase_counts[tok] += 1
        for i in range(len(tokens) - 1):
            phrase_counts[f"{tokens[i]} {tokens[i+1]}"] += 1
        for i in range(len(tokens) - 2):
            phrase_counts[f"{tokens[i]} {tokens[i+1]} {tokens[i+2]}"] += 1

    min_freq = 2 if len(titles) >= 5 else 1
    keywords = [
        phrase for phrase, cnt in phrase_counts.most_common(MAX_TOPICS * 3)
        if cnt >= min_freq and len(phrase.split()) <= 5
    ]

    # Remove sub-phrases already covered by a longer phrase
    final = []
    for kw in keywords:
        if not any(kw in other and kw != other for other in final):
            final.append(kw)
        if len(final) >= MAX_TOPICS:
            break

    return final


def _clean_title(title: str, max_words: int = 10) -> str:
    """
    Trim a long article title to a meaningful, concise phrase.
    - Splits at common structural separators first (colon, pipe, dash)
    - Falls back to truncating at max_words words
    Ensures topics shown in the dashboard / used as pipeline tags stay readable.
    """
    title = title.strip()
    # Split at structural separators — take the lead clause (most informative part)
    for sep in (" | ", " - ", " — ", " · ", " : "):
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if len(p.strip()) > 10]
            if parts:
                title = parts[0]
                break
    # Hard truncate if still too long
    words = title.split()
    if len(words) > max_words:
        title = " ".join(words[:max_words]) + "…"
    return title


# Spam patterns — financial wire releases, ticker symbols, fund filings etc.
_SPAM_PATTERNS = re.compile(
    r"(?i)(\$[A-Z]{2,5}\b"   # stock tickers e.g. $AAPL
    r"|\bLP\b|\bLLC\b|\bInc\.?\b|\bCorp\.?\b"  # company suffixes in headlines
    r"|grows? (stake|holdings?|position)"        # fund filing language
    r"|(?:buys?|sells?|purchases?|cuts? stake|boosts? (stake|holdings?))\s+\d"  # share trades
    r"|\bshares? (sold|bought|held)\b"           # SEC-style filings
    r"|million (position|stake|holdings?)"        # fund amounts
    r"|quote of the day"                          # filler content
    r"|crossword"                                 # puzzle content
    r")",
    re.IGNORECASE,
)

def _is_spam_title(title: str) -> bool:
    """Return True if the title looks like a financial wire/press release/filler."""
    if _SPAM_PATTERNS.search(title):
        return True
    words = title.split()
    # Reject if majority of capitalised words look like company/fund names
    cap_words = [w for w in words if w and w[0].isupper() and len(w) > 2]
    if len(cap_words) >= 5 and len(cap_words) / max(len(words), 1) > 0.7:
        return True
    return False


# ── Source 1: YouTube Trending Chart (ALL categories) ─────────────────────────

def _fetch_youtube_trending_chart(service) -> list[str]:
    """
    Fetch the ACTUAL YouTube Trending page data using videos.list(chart=mostPopular).
    This covers ALL categories — sports, music, gaming, news, entertainment, tech.
    Costs 1 quota unit per region call.
    """
    raw_regions = os.getenv("TREND_REGIONS", ",".join(DEFAULT_REGIONS))
    regions = [r.strip().upper() for r in raw_regions.split(",") if r.strip()]

    all_titles: list[str] = []
    category_hits: Counter = Counter()

    try:
        for region in regions[:4]:
            try:
                resp = service.videos().list(
                    part="snippet,statistics",
                    chart="mostPopular",
                    regionCode=region,
                    maxResults=50,   # max allowed
                    hl="en",
                ).execute()
                items = resp.get("items", [])
                for item in items:
                    snip = item.get("snippet", {})
                    title = snip.get("title", "").strip()
                    cat_id = snip.get("categoryId", "")
                    cat_name = YT_CATEGORY_SWEEPS.get(cat_id, "Other")
                    views = int(item.get("statistics", {}).get("viewCount", 0))

                    if title:
                        # Weight by view count: more views = more repetitions in counter
                        weight = min(int(math.log10(views + 1)), 6) if views else 1
                        all_titles.extend([title] * weight)
                        category_hits[cat_name] += 1

                logger.info(
                    f"[Trending/YT-Chart] {region}: {len(items)} trending videos"
                )
            except Exception as e:
                logger.debug(f"[Trending/YT-Chart] {region} failed: {e}")

        if not all_titles:
            return []

        # Log category breakdown
        logger.info(
            f"[Trending/YT-Chart] Category breakdown: "
            + ", ".join(f"{k}:{v}" for k, v in category_hits.most_common(6))
        )

        keywords = _extract_keywords(all_titles)
        return keywords[:MAX_TOPICS]

    except Exception as e:
        logger.warning(f"[Trending/YT-Chart] Failed: {e}")
        return []


# ── Source 2: YouTube Category Sweeps ─────────────────────────────────────────

def _fetch_youtube_category_sweeps(service) -> list[str]:
    """
    Sweep top videos across multiple YouTube category IDs.
    Finds what's trending in Sports, Music, Gaming, Entertainment etc.
    Costs 1 quota unit per category × region combination swept.
    Limited to 3 categories max to preserve quota.
    """
    raw_regions = os.getenv("TREND_REGIONS", ",".join(DEFAULT_REGIONS))
    regions = [r.strip().upper() for r in raw_regions.split(",") if r.strip()]
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Pick the highest-traffic categories (not News — that's the old approach)
    sweep_categories = [("17", "Sports"), ("10", "Music"), ("24", "Entertainment")]
    all_titles: list[str] = []

    try:
        for cat_id, cat_name in sweep_categories:
            for region in regions[:2]:  # 2 regions per category
                try:
                    resp = service.search().list(
                        part="snippet",
                        type="video",
                        videoCategoryId=cat_id,
                        regionCode=region,
                        order="viewCount",
                        publishedAfter=since,
                        maxResults=15,
                    ).execute()
                    titles = [
                        item["snippet"]["title"]
                        for item in resp.get("items", [])
                        if item.get("snippet", {}).get("title")
                    ]
                    all_titles.extend(titles)
                    logger.debug(
                        f"[Trending/YT-Sweep] {cat_name}/{region}: "
                        f"{len(titles)} results"
                    )
                except Exception as e:
                    logger.debug(f"[Trending/YT-Sweep] {cat_name}/{region}: {e}")

        return _extract_keywords(all_titles) if all_titles else []

    except Exception as e:
        logger.warning(f"[Trending/YT-Sweep] Failed: {e}")
        return []


# ── Source 3: Google Trends RSS (all categories) ──────────────────────────────

_GT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

def _parse_gt_xml(xml_text: str) -> list[str]:
    """Extract <title> and <ht:query> entries from raw Google Trends XML."""
    titles  = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", xml_text)
    titles += re.findall(r"<title>(.*?)</title>", xml_text)
    queries = re.findall(r"<ht:query>(.*?)</ht:query>", xml_text)
    return [t.strip() for t in titles + queries if t.strip() and len(t.strip()) > 2]


def _fetch_from_google_trends() -> list[str]:
    """
    Fetch Google Trends daily trending searches for multiple regions.
    Tries the realtime API first, then falls back to the daily RSS with
    browser User-Agent (plain feedparser is often blocked by Google).
    Covers ALL categories: sports, news, entertainment, tech, etc.
    Topics trending across multiple regions are ranked higher.
    """
    raw_regions = os.getenv("TREND_REGIONS", ",".join(DEFAULT_REGIONS))
    regions = [r.strip().upper() for r in raw_regions.split(",") if r.strip()]
    topic_counts: Counter = Counter()
    fetched = 0

    for region in regions:
        # ── Attempt 1: Realtime Trends JSON API ──────────────────────────
        try:
            resp = requests.get(
                GOOGLE_TRENDS_REALTIME,
                params={
                    "hl":  "en-US",
                    "tz":  "-330",
                    "geo": region,
                    "fi":  "0",
                    "fs":  "0",
                    "ri":  "300",
                    "rs":  "10",
                    "sort": "0",
                },
                headers=_GT_HEADERS,
                timeout=8,
            )
            if resp.status_code == 200:
                # Response is: )]}' + json
                text = resp.text
                if text.startswith(")]}'\n"):
                    text = text[5:]
                data = json.loads(text)
                for story in data.get("storySummaries", {}).get("trendingStories", []):
                    title = (story.get("title") or "").strip()
                    if title and len(title) > 2:
                        topic_counts[_clean_title(title)] += 1
                    for article in story.get("articles", [])[:2]:
                        at = (article.get("articleTitle") or "").strip()
                        if at and len(at) > 2:
                            topic_counts[_clean_title(at)] += 1
                fetched += 1
                logger.debug(f"[Trending/GTrends] {region}: realtime OK ({len(topic_counts)} total so far)")
                continue  # skip RSS if realtime worked
        except Exception as e:
            logger.debug(f"[Trending/GTrends] {region} realtime failed: {e}")

        # ── Attempt 2: Daily RSS with browser headers ─────────────────────
        rss_url = GOOGLE_TRENDS_BASE + region
        try:
            resp = requests.get(rss_url, headers=_GT_HEADERS, timeout=8)
            if resp.status_code == 200:
                items = _parse_gt_xml(resp.text)
                for item in items[:30]:
                    topic_counts[item] += 1
                fetched += 1
                logger.debug(f"[Trending/GTrends] {region}: RSS OK ({len(items)} items)")
            else:
                logger.debug(f"[Trending/GTrends] {region}: HTTP {resp.status_code}")
        except Exception as e:
            logger.debug(f"[Trending/GTrends] {region} RSS failed: {e}")

    if not topic_counts:
        logger.warning("[Trending/GTrends] All region feeds empty — may be rate-limited")
        return []

    # Sort by cross-region count, deduplicate, skip RSS boilerplate
    _BOILERPLATE = {"google trends", "trending searches", "daily search trends", "rss"}
    seen, result = set(), []
    for topic, _ in topic_counts.most_common(MAX_TOPICS * 2):
        key = topic.lower().strip()
        if key in _BOILERPLATE or len(topic) < 3:
            continue
        if key not in seen:
            seen.add(key)
            result.append(topic)

    logger.info(
        f"[Trending/GTrends] {len(result)} topics from {fetched}/{len(regions)} regions"
    )
    return result[:MAX_TOPICS]


# ── Source 4: Currents API trending topics ────────────────────────────────────

def _fetch_from_currentsapi() -> list[str]:
    """
    Pull latest headlines from Currents API as trending topics.
    Returns clean full article titles directly — not broken keywords.
    Also pulls from multiple categories: sports, tech, entertainment, politics.
    Spam/wire release titles are filtered out.
    Free tier: 600 req/day (no credit card).
    """
    api_key = os.getenv("CURRENTS_API_KEY", "")
    if not api_key:
        return []

    topics: list[str] = []
    seen: set[str] = set()

    # Fetch latest news (all categories in one call)
    try:
        resp = requests.get(
            "https://api.currentsapi.services/v1/latest-news",
            params={"apiKey": api_key, "language": "en"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("news", []):
            title = (item.get("title") or "").strip()
            if not title or len(title) < 8 or _is_spam_title(title):
                continue
            clean = _clean_title(title)
            key = clean.lower()
            if key not in seen:
                seen.add(key)
                topics.append(clean)
        logger.info(f"[Trending/CurrentsAPI] {len(topics)} headline topics fetched")
    except Exception as e:
        logger.debug(f"[Trending/CurrentsAPI] latest-news failed: {e}")

    # Also fetch category-specific to surface sports/entertainment/tech
    for cat in ["sports", "technology", "entertainment", "politics"]:
        if len(topics) >= MAX_TOPICS:
            break
        try:
            resp = requests.get(
                "https://api.currentsapi.services/v1/search",
                params={"apiKey": api_key, "language": "en", "category": cat},
                timeout=6,
            )
            if resp.status_code == 200:
                for item in resp.json().get("news", [])[:5]:
                    title = (item.get("title") or "").strip()
                    if title and len(title) >= 8 and not _is_spam_title(title):
                        clean = _clean_title(title)
                        if clean.lower() not in seen:
                            seen.add(clean.lower())
                            topics.append(clean)
        except Exception:
            pass

    return topics[:MAX_TOPICS]


# ── Source 5: TheNewsAPI trending topics ──────────────────────────────────────

def _fetch_from_thenewsapi() -> list[str]:
    """
    Pull top stories from TheNewsAPI across multiple locales.
    Returns full article titles as trending topics — not broken word fragments.
    Makes per-locale calls to maximise article count (free: 3/call/locale).
    Spam/wire release titles are filtered out.
    100 req/day free tier.
    """
    api_key = os.getenv("THENEWSAPI_KEY", "")
    if not api_key:
        return []

    raw_regions = os.getenv("TREND_REGIONS", ",".join(DEFAULT_REGIONS))
    regions = [r.strip().lower() for r in raw_regions.split(",") if r.strip()]

    topics: list[str] = []
    seen: set[str] = set()

    # Call once per locale to maximise results (free tier returns ~3/call)
    for locale in regions[:5]:
        try:
            resp = requests.get(
                "https://api.thenewsapi.com/v1/news/top",
                params={
                    "api_token": api_key,
                    "locale":    locale,
                    "language":  "en",
                    "limit":     3,     # free tier max per locale
                },
                timeout=8,
            )
            if resp.status_code != 200:
                logger.debug(f"[Trending/TheNewsAPI] {locale}: HTTP {resp.status_code}")
                continue
            for item in resp.json().get("data", []):
                title = (item.get("title") or "").strip()
                if not title or len(title) < 8 or _is_spam_title(title):
                    continue
                clean = _clean_title(title)
                key = clean.lower()
                if key not in seen:
                    seen.add(key)
                    topics.append(clean)
        except Exception as e:
            logger.debug(f"[Trending/TheNewsAPI] {locale} failed: {e}")

    logger.info(f"[Trending/TheNewsAPI] {len(topics)} headline topics from {len(regions)} locales")
    return topics[:MAX_TOPICS]

# ── Cross-signal merger & ranker ──────────────────────────────────────────────

def _merge_and_rank(signal_lists: list[tuple[str, list[str]]]) -> list[str]:
    """
    Merge topics from multiple signals.
    Topics appearing in MORE signals rank HIGHER.
    Within same signal count, earlier position = higher rank.

    signal_lists: [(source_name, [topic, ...]), ...]
    """
    # Score each topic: +10 per signal it appears in, +1/rank for position
    scores: Counter = Counter()

    for source_name, topics in signal_lists:
        for rank, topic in enumerate(topics):
            key = topic.lower().strip()
            scores[key] += 10 + max(0, (MAX_TOPICS - rank))

    # Map normalised key back to best-cased form (first occurrence wins)
    key_to_display: dict[str, str] = {}
    for _, topics in signal_lists:
        for topic in topics:
            key = topic.lower().strip()
            if key not in key_to_display:
                key_to_display[key] = topic

    ranked = [
        key_to_display[key]
        for key, _ in scores.most_common(MAX_TOPICS)
        if key in key_to_display
    ]
    return ranked[:MAX_TOPICS]


# ── Public API ─────────────────────────────────────────────────────────────────

def get_trending_topics(uploader=None) -> list[str]:
    """
    Return a ranked list of currently trending topic strings — covering ALL
    categories (sports, entertainment, tech, news, gaming, music, finance).

    Topics that appear across multiple signals (YouTube trending chart,
    Google Trends, news APIs) are ranked highest — these are the true viral
    moments like FIFA World Cup, IPL Final, Oscar Winners, etc.

    uploader : YouTubeUploader instance (optional). If provided and authenticated,
               uses the YouTube Data API as primary source.
    """
    # 1. Try cache
    cached = _load_cache()
    if cached:
        return cached

    signals: list[tuple[str, list[str]]] = []
    sources_used: list[str] = []

    # 2. YouTube Trending Chart (ALL categories) — best signal
    if uploader is not None:
        try:
            service = None
            if hasattr(uploader, "_service") and uploader._service:
                service = uploader._service
            elif hasattr(uploader, "authenticate"):
                if uploader.authenticate():
                    service = uploader._service

            if service:
                chart_topics = _fetch_youtube_trending_chart(service)
                if chart_topics:
                    signals.append(("yt-chart", chart_topics))
                    sources_used.append("youtube-trending-chart")

                # 3. YouTube Category Sweeps (sports, music, entertainment)
                sweep_topics = _fetch_youtube_category_sweeps(service)
                if sweep_topics:
                    signals.append(("yt-sweep", sweep_topics))
                    sources_used.append("youtube-category-sweeps")

        except Exception as e:
            logger.warning(f"[Trending] YouTube API failed: {e}")

    # 4. Google Trends RSS (no key, all categories)
    gt_topics = _fetch_from_google_trends()
    if gt_topics:
        signals.append(("google-trends", gt_topics))
        sources_used.append("google-trends")

    # 5. Currents API
    ca_topics = _fetch_from_currentsapi()
    if ca_topics:
        signals.append(("currents-api", ca_topics))
        sources_used.append("currents-api")

    # 6. TheNewsAPI
    tna_topics = _fetch_from_thenewsapi()
    if tna_topics:
        signals.append(("thenewsapi", tna_topics))
        sources_used.append("thenewsapi")

    # Merge + rank by cross-signal frequency
    if signals:
        topics = _merge_and_rank(signals)
        source = "+".join(sources_used)
        logger.info(
            f"[Trending] Merged {len(topics)} topics from {len(signals)} signals: "
            f"{source}"
        )
    else:
        topics = []
        source = "seed"

    # 7. Seed fallback
    if len(topics) < MIN_TOPICS:
        existing_lower = {t.lower() for t in topics}
        for t in SEED_TOPICS:
            if t.lower() not in existing_lower:
                topics.append(t)
                existing_lower.add(t.lower())
        source = (source + "+seed") if source != "seed" else "seed"
        logger.info("[Trending] Supplemented with seed topics")

    topics = topics[:MAX_TOPICS]
    logger.info(f"[Trending] Final: {len(topics)} topics | Sources: {source}")
    logger.info(f"[Trending] Top 10: {topics[:10]}")
    _save_cache(topics, source)
    return topics


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    # Load env vars from .env so API keys are available
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass
    # Delete cache so we force a fresh fetch
    import os as _os
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()
        print("(Cache cleared for fresh test)")
    print("Fetching ALL-category trending topics (Google Trends + News APIs)...\n")
    topics = get_trending_topics(uploader=None)
    print(f"Got {len(topics)} trending topics:\n")
    for i, t in enumerate(topics, 1):
        print(f"  {i:2d}. {t}")

