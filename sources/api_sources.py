"""
sources/api_sources.py
======================
Fetches live data from free public APIs and converts each result into a
NewsArticle-compatible object that can be enqueued and processed by the
existing pipeline (TTS → Short → Upload).

Supported sources (all free, most need zero API key):
  - CoinGecko      : Crypto price movers     (no key)
  - TheSportsDB    : Sports events & scores  (no key)
  - Frankfurter    : Currency exchange rates  (no key)
  - NASA APOD      : Astronomy picture + fact (free key via NASA_API_KEY env)
  - HackerNews     : Top tech stories        (no key)
  - Open-Meteo     : Global weather alerts   (no key)
  - REST Countries : World facts / rankings  (no key)
  - TMDB           : Trending movies/TV      (free key via TMDB_API_KEY env)
  - NewsAPI        : Global headlines        (free key via NEWS_API_KEY env)

Each source yields a list of `APIArticle` objects that mimic NewsArticle's
interface (title, content, url, source, category, image_url, id).
"""

from __future__ import annotations

import os
import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from loguru import logger

# ── Shared helpers ─────────────────────────────────────────────────────────────

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "NewsBot/2.0 (+https://github.com/newsbot)",
    "Accept":     "application/json",
})
_TIMEOUT = 8


def _get(url: str, params: dict = None, headers: dict = None) -> Optional[dict]:
    try:
        r = _SESSION.get(url, params=params, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"[APISource] GET {url} failed: {e}")
        return None


def _article_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


# ── APIArticle dataclass (NewsArticle-compatible) ─────────────────────────────

@dataclass
class APIArticle:
    title:        str
    content:      str
    url:          str
    source:       str
    category:     str
    image_url:    str  = ""
    author:       str  = "API Bot"
    published_at: str  = field(default_factory=lambda: datetime.now().isoformat())
    id:           str  = ""

    def __post_init__(self):
        if not self.id:
            self.id = _article_id(self.title)


# ── 1. CoinGecko — Crypto movers ──────────────────────────────────────────────

def fetch_coingecko(top_n: int = 5) -> list[APIArticle]:
    """Top N crypto coins by 24h price change (both gainers and losers)."""
    data = _get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "order":       "market_cap_desc",
            "per_page":    50,
            "page":        1,
            "sparkline":   "false",
            "price_change_percentage": "24h",
        },
    )
    if not data:
        return []

    articles = []
    # Sort by absolute % change — biggest movers
    movers = sorted(data, key=lambda c: abs(c.get("price_change_percentage_24h") or 0), reverse=True)
    for coin in movers[:top_n]:
        name     = coin.get("name", "")
        symbol   = coin.get("symbol", "").upper()
        price    = coin.get("current_price", 0)
        change   = coin.get("price_change_percentage_24h") or 0
        mktcap   = coin.get("market_cap", 0)
        img      = coin.get("image", "")
        direction = "surges" if change > 0 else "drops"
        arrow     = "▲" if change > 0 else "▼"

        title = (
            f"{name} ({symbol}) {direction} {abs(change):.1f}% "
            f"— now at ${price:,.2f}"
        )
        content = (
            f"{name} ({symbol}) has {direction} {abs(change):.1f}% in the past 24 hours, "
            f"trading at ${price:,.4f}. "
            f"Market cap stands at ${mktcap/1e9:.2f} billion. "
            f"Crypto markets saw {arrow} {abs(change):.1f}% movement for {name} today, "
            f"making it one of the biggest movers in the top 50 coins. "
            f"Traders and investors are closely watching the {"rally" if change > 0 else "selloff"} "
            f"as it reflects broader {"bullish" if change > 0 else "bearish"} sentiment."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url=f"https://www.coingecko.com/en/coins/{coin.get('id','')}",
            source="CoinGecko",
            category="cryptocurrency",
            image_url=img,
        ))

    logger.info(f"[CoinGecko] Fetched {len(articles)} crypto mover articles")
    return articles


# ── 2. TheSportsDB — Sports events ────────────────────────────────────────────

_SPORTS_LEAGUES = {
    "Cricket IPL": "4341",
    "Football Premier League": "4328",
    "Football La Liga": "4335",
    "Basketball NBA": "4387",
    "Football Serie A": "4332",
}

def fetch_thesportsdb(max_events: int = 4) -> list[APIArticle]:
    """Recent sports events from TheSportsDB (free tier, no key needed)."""
    articles = []
    for league_name, league_id in _SPORTS_LEAGUES.items():
        if len(articles) >= max_events:
            break
        data = _get(
            f"https://www.thesportsdb.com/api/v1/json/3/eventspastleague.php",
            params={"id": league_id},
        )
        if not data or not data.get("events"):
            continue
        event = data["events"][0]  # most recent
        home  = event.get("strHomeTeam", "")
        away  = event.get("strAwayTeam", "")
        score = f"{event.get('intHomeScore','?')}–{event.get('intAwayScore','?')}"
        date  = event.get("dateEvent", "")
        thumb = event.get("strThumb", "") or event.get("strBanner", "")
        sport = event.get("strSport", "Sports")

        winner = ""
        try:
            hs, as_ = int(event.get("intHomeScore",0)), int(event.get("intAwayScore",0))
            winner = home if hs > as_ else (away if as_ > hs else "Draw")
        except Exception:
            pass

        title = f"{home} vs {away}: Final Score {score} | {league_name}"
        content = (
            f"In a thrilling {league_name} match, {home} faced {away} on {date}, "
            f"with the final score ending {score}. "
            + (f"{winner} emerged victorious in this contest. " if winner and winner != "Draw" else "The match ended in a draw. ")
            + f"This result has significant implications for the {league_name} standings. "
            f"Fans across the globe tuned in to watch this exciting {sport} fixture."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url=f"https://www.thesportsdb.com/event/{event.get('idEvent','')}",
            source="TheSportsDB",
            category="sports",
            image_url=thumb,
        ))

    logger.info(f"[TheSportsDB] Fetched {len(articles)} sports event articles")
    return articles


# ── 3. Frankfurter — Currency rates ───────────────────────────────────────────

_CURRENCY_PAIRS = [
    ("USD", "INR", "US Dollar to Indian Rupee"),
    ("EUR", "USD", "Euro to US Dollar"),
    ("GBP", "USD", "British Pound to US Dollar"),
    ("USD", "JPY", "US Dollar to Japanese Yen"),
    ("BTC", "USD", "Bitcoin to US Dollar"),
]

def fetch_frankfurter(max_pairs: int = 3) -> list[APIArticle]:
    """Live currency exchange rates from Frankfurter (ECB data, free, no key)."""
    data = _get("https://api.frankfurter.app/latest", params={"from": "USD"})
    if not data:
        return []

    rates = data.get("rates", {})
    date  = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    articles = []

    pairs = [("USD", "INR", "US Dollar vs Indian Rupee"),
             ("USD", "EUR", "US Dollar vs Euro"),
             ("USD", "GBP", "US Dollar vs British Pound"),
             ("USD", "JPY", "US Dollar vs Japanese Yen"),
             ("USD", "CNY", "US Dollar vs Chinese Yuan")]

    for base, target, label in pairs[:max_pairs]:
        if target not in rates:
            continue
        rate = rates[target]
        title = f"{label}: 1 {base} = {rate:.4f} {target} today"
        content = (
            f"The {label} exchange rate stands at 1 {base} equals {rate:.4f} {target} "
            f"as of {date}. "
            f"Currency markets are reflecting current global economic conditions. "
            f"The {base}/{target} pair is closely watched by traders, importers, and exporters. "
            f"Financial analysts track this rate as an indicator of economic health and "
            f"monetary policy direction from central banks."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url="https://www.frankfurter.app",
            source="Frankfurter (ECB)",
            category="finance",
        ))

    logger.info(f"[Frankfurter] Fetched {len(articles)} currency rate articles")
    return articles


# ── 4. NASA APOD — Astronomy ──────────────────────────────────────────────────

def fetch_nasa_apod() -> list[APIArticle]:
    """NASA Astronomy Picture of the Day — stunning science content."""
    api_key = os.getenv("NASA_API_KEY", "DEMO_KEY")
    data = _get(
        "https://api.nasa.gov/planetary/apod",
        params={"api_key": api_key, "thumbs": "true"},
    )
    if not data or data.get("code"):
        return []

    title   = data.get("title", "NASA Astronomy Picture of the Day")
    expl    = data.get("explanation", "")
    img     = data.get("url", "") if data.get("media_type") == "image" else data.get("thumbnail_url", "")
    date    = data.get("date", "")
    credit  = data.get("copyright", "NASA")

    article_title = f"NASA Picture of the Day: {title}"
    content = (
        f"NASA's Astronomy Picture of the Day for {date} features: {title}. "
        f"{expl[:500]} "
        f"This stunning image captured by {credit} showcases the wonders of our universe. "
        f"Space enthusiasts and scientists alike are amazed by this cosmic spectacle shared by NASA."
    )

    logger.info(f"[NASA APOD] Fetched: {title[:50]}")
    return [APIArticle(
        title=article_title,
        content=content,
        url=f"https://apod.nasa.gov/apod/astropix.html",
        source="NASA",
        category="science",
        image_url=img,
        author=credit,
    )]


# ── 5. HackerNews — Tech trending ────────────────────────────────────────────

def fetch_hackernews(top_n: int = 3) -> list[APIArticle]:
    """Top stories from Hacker News — tech/startup trending."""
    ids = _get("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not ids:
        return []

    articles = []
    for story_id in ids[:15]:  # check top 15 to find stories with URLs
        if len(articles) >= top_n:
            break
        story = _get(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        if not story or story.get("type") != "story":
            continue
        url   = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
        title = story.get("title", "")
        score = story.get("score", 0)
        by    = story.get("by", "HN User")
        comments = story.get("descendants", 0)
        if not title or len(title) < 10:
            continue

        content = (
            f"Trending on Hacker News: {title}. "
            f"This story has received {score} upvotes and {comments} comments from the tech community. "
            f"Shared by {by}, it is sparking discussion among developers, engineers and entrepreneurs worldwide. "
            f"The tech community on Hacker News is actively debating the implications of this development "
            f"for the future of technology and innovation."
        )
        articles.append(APIArticle(
            title=f"Tech Trending: {title}",
            content=content,
            url=url,
            source="Hacker News",
            category="technology",
        ))

    logger.info(f"[HackerNews] Fetched {len(articles)} trending tech articles")
    return articles


# ── 6. Open-Meteo — Weather alerts ───────────────────────────────────────────

_CITIES = [
    ("New York",   40.7128,  -74.0060, "US"),
    ("London",     51.5074,   -0.1278, "UK"),
    ("Mumbai",     19.0760,   72.8777, "IN"),
    ("Tokyo",      35.6762,  139.6503, "JP"),
    ("Sydney",    -33.8688,  151.2093, "AU"),
    ("Dubai",      25.2048,   55.2708, "AE"),
    ("Paris",      48.8566,    2.3522, "FR"),
]

def fetch_weather_alerts(max_cities: int = 2) -> list[APIArticle]:
    """Extreme weather conditions from Open-Meteo (no key required)."""
    articles = []
    cities = random.sample(_CITIES, min(max_cities * 3, len(_CITIES)))

    for city, lat, lon, country in cities:
        if len(articles) >= max_cities:
            break
        data = _get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  lat,
                "longitude": lon,
                "current":   "temperature_2m,weathercode,windspeed_10m,precipitation",
                "timezone":  "auto",
            },
        )
        if not data:
            continue

        curr = data.get("current", {})
        temp = curr.get("temperature_2m")
        wind = curr.get("windspeed_10m")
        rain = curr.get("precipitation", 0)
        code = curr.get("weathercode", 0)
        if temp is None:
            continue

        # Only report notable weather (extreme heat/cold, heavy wind, heavy rain)
        notable = (
            temp > 38 or temp < -5 or
            wind > 60 or rain > 10 or
            code in (95, 96, 99)  # thunderstorm codes
        )
        if not notable:
            continue

        condition = _weather_code_to_text(code)
        title = f"Weather Alert: {condition} in {city}, {country} — {temp}°C"
        content = (
            f"Extreme weather conditions are being reported in {city}, {country}. "
            f"Current temperature is {temp}°C with {condition.lower()} conditions. "
            f"Wind speeds are reaching {wind} km/h"
            + (f" and precipitation of {rain} mm is recorded." if rain > 0 else ".")
            + f" Meteorologists are advising residents in {city} to take necessary precautions. "
            f"The extreme weather is affecting daily life and transport in the region."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url="https://open-meteo.com",
            source="Open-Meteo",
            category="weather",
        ))

    logger.info(f"[Open-Meteo] Fetched {len(articles)} weather alert articles")
    return articles


def _weather_code_to_text(code: int) -> str:
    mapping = {
        0: "Clear Sky", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
        45: "Foggy", 48: "Icy Fog", 51: "Light Drizzle", 53: "Drizzle",
        61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
        71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
        80: "Rain Showers", 81: "Heavy Showers", 82: "Violent Showers",
        95: "Thunderstorm", 96: "Thunderstorm with Hail", 99: "Severe Thunderstorm",
    }
    return mapping.get(code, "Severe Weather")


# ── 7. REST Countries — World facts ───────────────────────────────────────────

def fetch_world_facts(max_facts: int = 2) -> list[APIArticle]:
    """Interesting world facts — population rankings, largest countries, etc."""
    data = _get(
        "https://restcountries.com/v3.1/all",
        params={"fields": "name,population,area,capital,flags,region,currencies"},
    )
    if not data or not isinstance(data, list):
        return []

    articles = []

    # Guard: filter to only valid dicts with population field
    valid = [c for c in data if isinstance(c, dict) and c.get("population", 0) > 1_000_000]

    # Story 1: World's most populous nations
    by_pop = sorted(valid, key=lambda c: c.get("population", 0), reverse=True)
    if by_pop:
        top5  = by_pop[:5]
        names = [c.get("name", {}).get("common", "Unknown") for c in top5]
        pops  = [c.get("population", 0) for c in top5]
        title = (
            f"World Population Rankings 2026: {names[0]} leads "
            f"with {pops[0]/1e9:.2f} Billion people"
        )
        content = (
            f"The world's most populous nations in 2026 are: "
            + ", ".join(f"{n} ({p/1e6:.0f}M)" for n, p in zip(names, pops))
            + ". "
            f"These five countries collectively account for over half the world's population. "
            f"Population growth, urbanisation and resource allocation are key challenges "
            f"as these nations continue to grow. Global demographers forecast significant "
            f"shifts in rankings by 2050 due to fertility rate changes."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url="https://restcountries.com",
            source="REST Countries",
            category="world",
            image_url=top5[0].get("flags", {}).get("png", "") if isinstance(top5[0].get("flags"), dict) else "",
        ))

    # Story 2: Largest nations by area
    by_area = sorted(
        [c for c in valid if c.get("area", 0) > 100_000],
        key=lambda c: c.get("area", 0),
        reverse=True,
    )
    if len(by_area) >= 5 and len(articles) < max_facts:
        top5a  = by_area[:5]
        names  = [c.get("name", {}).get("common", "Unknown") for c in top5a]
        areas  = [c.get("area", 0) for c in top5a]
        title  = f"World's Largest Countries: {names[0]} spans {areas[0]/1e6:.1f} million km²"
        content = (
            f"By land area, the world's largest countries are: "
            + ", ".join(f"{n} ({a/1e6:.2f}M km²)" for n, a in zip(names, areas))
            + ". "
            f"{names[0]} is the largest nation on Earth, covering {areas[0]/1e6:.1f} million "
            f"square kilometres. Together the top 5 nations cover nearly half of Earth's "
            f"total land surface. This geographic dominance has major geopolitical and "
            f"economic implications for global trade, resources and climate."
        )
        articles.append(APIArticle(
            title=title,
            content=content,
            url="https://restcountries.com",
            source="REST Countries",
            category="world",
        ))

    logger.info(f"[RESTCountries] Fetched {len(articles)} world fact articles")
    return articles[:max_facts]


# ── 8. TMDB — Trending movies/TV ─────────────────────────────────────────────

def fetch_tmdb_trending(max_items: int = 3) -> list[APIArticle]:
    """Trending movies and TV shows from TMDB (requires free TMDB_API_KEY)."""
    api_key = os.getenv("TMDB_API_KEY", "")
    if not api_key:
        logger.debug("[TMDB] No TMDB_API_KEY set — skipping")
        return []

    data = _get(
        "https://api.themoviedb.org/3/trending/all/day",
        params={"api_key": api_key, "language": "en-US"},
    )
    if not data or not data.get("results"):
        return []

    articles = []
    for item in data["results"][:max_items]:
        media  = item.get("media_type", "movie")
        title  = item.get("title") or item.get("name", "")
        rating = item.get("vote_average", 0)
        votes  = item.get("vote_count", 0)
        overview = item.get("overview", "")
        poster = f"https://image.tmdb.org/t/p/w500{item.get('poster_path','')}" if item.get("poster_path") else ""
        release = item.get("release_date") or item.get("first_air_date", "")

        article_title = f"Trending {media.title()}: '{title}' — Rated {rating:.1f}/10"
        content = (
            f"'{title}' is currently trending globally as a top {media}. "
            f"With a rating of {rating:.1f} out of 10 from {votes:,} votes, "
            f"it has captured audiences worldwide. "
            f"{overview[:300]} "
            f"Released in {release[:4] if release else 'recent times'}, this {media} "
            f"continues to dominate streaming platforms and box office charts."
        )
        articles.append(APIArticle(
            title=article_title,
            content=content,
            url=f"https://www.themoviedb.org/{media}/{item.get('id','')}",
            source="TMDB",
            category="entertainment",
            image_url=poster,
        ))

    logger.info(f"[TMDB] Fetched {len(articles)} trending entertainment articles")
    return articles


# ── 9. NewsAPI — Global headlines ────────────────────────────────────────────

def fetch_newsapi(max_articles: int = 6) -> list[APIArticle]:
    """Top global headlines from NewsAPI (requires free NEWS_API_KEY)."""
    api_key = os.getenv("NEWS_API_KEY", "")
    if not api_key:
        logger.debug("[NewsAPI] No NEWS_API_KEY set — skipping")
        return []

    data = _get(
        "https://newsapi.org/v2/top-headlines",
        params={
            "apiKey":   api_key,
            "language": "en",
            "pageSize": max_articles,
            "category": "general",
        },
    )
    if not data or not data.get("articles"):
        return []

    articles = []
    for item in data["articles"][:max_articles]:
        title   = item.get("title", "") or ""
        content = item.get("content") or item.get("description") or ""
        url     = item.get("url", "")
        img     = item.get("urlToImage", "")
        source  = item.get("source", {}).get("name", "NewsAPI")
        author  = item.get("author", source)
        if not title or "[Removed]" in title:
            continue

        if len(content) < 80:
            content = (
                f"{title}. "
                f"This story from {source} has been making headlines globally. "
                f"News analysts are closely following developments as they unfold. "
                f"Stay updated with the latest information on this breaking story."
            )

        articles.append(APIArticle(
            title=title,
            content=content,
            url=url,
            source=source,
            category="news",
            image_url=img,
            author=author or source,
        ))

    logger.info(f"[NewsAPI] Fetched {len(articles)} global headline articles")
    return articles


# ── 10. TheNewsAPI — Global top stories ───────────────────────────────────────

# Category map: TheNewsAPI categories → pipeline categories
_THENEWSAPI_CATEGORIES = [
    "general", "science", "sports", "business",
    "health", "entertainment", "technology",
]

def fetch_thenewsapi(max_articles: int = 6) -> list[APIArticle]:
    """
    Top global headlines from TheNewsAPI.
    Free tier: 100 requests/day, no credit card needed.
    Sign up at: https://www.thenewsapi.com/register
    Set THENEWSAPI_KEY in .env.
    """
    api_key = os.getenv("THENEWSAPI_KEY", "")
    if not api_key:
        logger.debug("[TheNewsAPI] No THENEWSAPI_KEY set — skipping")
        return []

    # Fetch top stories — supports locale filter (comma-separated country codes)
    locales = os.getenv("TREND_REGIONS", "in,us,gb,au,ca").lower().replace(",", ",")
    data = _get(
        "https://api.thenewsapi.com/v1/news/top",
        params={
            "api_token": api_key,
            "locale":    locales,
            "language":  "en",
            "limit":     min(max_articles, 9),  # free tier max = 3 per request, paid = 9
        },
    )
    if not data or not data.get("data"):
        return []

    articles = []
    for item in data["data"][:max_articles]:
        title    = (item.get("title") or "").strip()
        snippet  = (item.get("description") or item.get("snippet") or "").strip()
        url      = item.get("url", "")
        img      = item.get("image_url", "")
        source   = item.get("source", "TheNewsAPI")
        category = item.get("categories", ["general"])[0] if item.get("categories") else "general"
        pub_at   = item.get("published_at", "")

        if not title or len(title) < 8:
            continue

        if len(snippet) < 80:
            snippet = (
                f"{title}. "
                f"This developing story from {source} is attracting global attention. "
                f"Reporters and analysts are tracking this story as details emerge. "
                f"The full implications of this story are still being assessed by experts worldwide."
            )

        articles.append(APIArticle(
            title=title,
            content=snippet,
            url=url,
            source=source,
            category=category,
            image_url=img,
            published_at=pub_at,
        ))

    logger.info(f"[TheNewsAPI] Fetched {len(articles)} articles")
    return articles


# ── 11. Currents API — Real-time global news ───────────────────────────────────

_CURRENTS_CATEGORY_MAP = {
    "technology": "technology",
    "sports":     "sports",
    "science":    "science",
    "business":   "business",
    "health":     "health",
    "entertainment": "entertainment",
    "world":      "world",
    "politics":   "politics",
}

def fetch_currentsapi(max_articles: int = 6) -> list[APIArticle]:
    """
    Real-time global news from Currents API.
    Free tier: 600 requests/day — very generous, no credit card needed.
    Sign up at: https://currentsapi.services/en/register
    Set CURRENTS_API_KEY in .env.
    """
    api_key = os.getenv("CURRENTS_API_KEY", "")
    if not api_key:
        logger.debug("[CurrentsAPI] No CURRENTS_API_KEY set — skipping")
        return []

    data = _get(
        "https://api.currentsapi.services/v1/latest-news",
        params={
            "apiKey":   api_key,
            "language": "en",
            "limit":    min(max_articles, 200),
        },
    )
    if not data or not data.get("news"):
        return []

    articles = []
    for item in data["news"][:max_articles]:
        title    = (item.get("title") or "").strip()
        content  = (item.get("description") or "").strip()
        url      = item.get("url", "")
        img      = item.get("image", "") or ""
        pub_at   = item.get("published", "")
        author   = item.get("author", "Currents API")
        cats     = item.get("category", ["general"])
        category = cats[0] if isinstance(cats, list) and cats else "general"
        source   = "Currents API"

        if not title or len(title) < 8:
            continue
        # Skip placeholder images
        if img in ("N/A", "none", "null", None):
            img = ""

        if len(content) < 80:
            content = (
                f"{title}. "
                f"This story is gaining traction across international news platforms. "
                f"Journalists and industry experts are closely analysing this development. "
                f"Stay informed as more details continue to emerge on this important story."
            )

        articles.append(APIArticle(
            title=title,
            content=content,
            url=url,
            source=source,
            category=_CURRENTS_CATEGORY_MAP.get(category.lower(), "news"),
            image_url=img,
            author=author,
            published_at=pub_at,
        ))

    logger.info(f"[CurrentsAPI] Fetched {len(articles)} articles (600 req/day free tier)")
    return articles


# ── Master fetch function ─────────────────────────────────────────────────────

def fetch_all_api_sources(max_total: int = 20) -> list[APIArticle]:
    """
    Fetch from all enabled API sources and return a merged list.
    Sources are tried in priority order; failures are silently skipped.

    Free (zero-key) sources:
      CoinGecko, TheSportsDB, Frankfurter, HackerNews, Open-Meteo, REST Countries

    Free (key required) sources — all offer free tiers, no credit card:
      NASA_API_KEY     → NASA APOD        (free key from api.nasa.gov)
      THENEWSAPI_KEY   → TheNewsAPI       (100 req/day free)
      CURRENTS_API_KEY → Currents API     (600 req/day free — BEST free option)
      TMDB_API_KEY     → TMDB             (unlimited free)
      NEWS_API_KEY     → NewsAPI          (100 req/day free, dev only)
    """
    all_articles: list[APIArticle] = []

    sources = [
        # Zero-key sources — always active
        ("CoinGecko",       lambda: fetch_coingecko(top_n=3)),
        ("TheSportsDB",     lambda: fetch_thesportsdb(max_events=3)),
        ("Frankfurter",     lambda: fetch_frankfurter(max_pairs=2)),
        ("HackerNews",      lambda: fetch_hackernews(top_n=2)),
        ("NASA APOD",       fetch_nasa_apod),
        ("Open-Meteo",      lambda: fetch_weather_alerts(max_cities=2)),
        ("REST Countries",  lambda: fetch_world_facts(max_facts=1)),
        # Key-based sources — skipped silently if key not set
        ("Currents API",    lambda: fetch_currentsapi(max_articles=6)),   # 600/day free
        ("TheNewsAPI",      lambda: fetch_thenewsapi(max_articles=6)),    # 100/day free
        ("TMDB",            lambda: fetch_tmdb_trending(max_items=3)),
        ("NewsAPI",         lambda: fetch_newsapi(max_articles=4)),       # 100/day free
    ]

    for name, fn in sources:
        try:
            results = fn()
            all_articles.extend(results)
            if len(all_articles) >= max_total:
                break
        except Exception as e:
            logger.warning(f"[APISource] {name} fetch error: {e}")

    logger.info(f"[APISource] Total API-sourced articles: {len(all_articles)}")
    return all_articles[:max_total]


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    print("=== Testing API Sources ===\n")
    articles = fetch_all_api_sources(max_total=15)
    print(f"Total fetched: {len(articles)}\n")
    for a in articles:
        print(f"  [{a.source:18s}] [{a.category:14s}] {a.title[:65]}")
