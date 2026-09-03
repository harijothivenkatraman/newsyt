"""
scraper/news_scraper.py
Scrapes news from The Hindu, India Today, NDTV, Times of India, etc.
"""

import time
import random
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field, asdict
import json
import os

import requests
from bs4 import BeautifulSoup
import feedparser
from loguru import logger


# ── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class NewsArticle:
    id: str = ""
    title: str = ""
    content: str = ""
    summary: str = ""
    url: str = ""
    source: str = ""
    author: str = ""
    published_at: str = ""
    category: str = ""
    image_url: str = ""
    tags: list = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        if not self.id:
            self.id = hashlib.md5(self.url.encode()).hexdigest()[:12]

    def to_dict(self):
        return asdict(self)


# ── Base Scraper ──────────────────────────────────────────────────────────────

from urllib.parse import urljoin

class BaseScraper:
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def get(self, url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
        try:
            time.sleep(random.uniform(1.5, 3.0))  # polite delay
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.debug(f"[{self.name}] Failed to fetch {url}: {e}")
            return None

    def fetch_rss(self, rss_url: str) -> list[dict]:
        try:
            feed = feedparser.parse(rss_url)
            return feed.entries
        except Exception as e:
            logger.warning(f"[{self.name}] RSS fetch failed: {e}")
            return []

    def extract_entry_image(self, entry) -> str:
        """Extract an image URL from an RSS feed entry."""
        # 1. Check media_content
        if "media_content" in entry and isinstance(entry["media_content"], list):
            for mc in entry["media_content"]:
                if isinstance(mc, dict) and mc.get("url"):
                    return mc["url"]
        # 2. Check media_thumbnail
        if "media_thumbnail" in entry and isinstance(entry["media_thumbnail"], list):
            for mt in entry["media_thumbnail"]:
                if isinstance(mt, dict) and mt.get("url"):
                    return mt["url"]
        # 3. Check links
        for link in entry.get("links", []):
            if isinstance(link, dict):
                href = link.get("href", "")
                type_ = link.get("type", "")
                if type_.startswith("image/") or any(href.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                    return href
        # 4. Check summary/description HTML img tag
        for key in ["summary", "description"]:
            val = entry.get(key, "")
            if val and "<img" in val.lower():
                try:
                    soup = BeautifulSoup(val, "lxml")
                    img = soup.find("img")
                    if img and img.get("src"):
                        return img.get("src")
                except Exception:
                    pass
        return ""

    def scrape(self) -> list[NewsArticle]:
        raise NotImplementedError





# ── The Hindu ────────────────────────────────────────────────────────────────

class TheHinduScraper(BaseScraper):
    RSS_FEEDS = {
        "national":      "https://www.thehindu.com/news/national/feeder/default.rss",
        "business":      "https://www.thehindu.com/business/feeder/default.rss",
        "technology":    "https://www.thehindu.com/sci-tech/technology/feeder/default.rss",
        "science":       "https://www.thehindu.com/sci-tech/science/feeder/default.rss",
        "sports":        "https://www.thehindu.com/sport/feeder/default.rss",
        "cricket":       "https://www.thehindu.com/sport/cricket/feeder/default.rss",
        "international": "https://www.thehindu.com/news/international/feeder/default.rss",
        "entertainment": "https://www.thehindu.com/entertainment/feeder/default.rss",
        "health":        "https://www.thehindu.com/sci-tech/health/feeder/default.rss",
        "education":     "https://www.thehindu.com/education/feeder/default.rss",
    }

    def __init__(self):
        super().__init__("The Hindu", "https://www.thehindu.com")

    def parse_article(self, url: str, category: str) -> Optional[NewsArticle]:
        soup = self.get(url)
        if not soup:
            return None

        title_tag = soup.select_one("h1.title, h1[data-testid='article-title'], h1")
        body_div   = soup.select_one("div.articleBody, div[data-testid='article-body'], div.article-content")
        image_tag  = soup.select_one("div.picture-wrapper img, figure img")
        author_tag = soup.select_one("a.author-name, span.author-name")
        date_tag   = soup.select_one("span[data-testid='date-updated'], span.publish-time")

        if not title_tag or not body_div:
            return None

        paragraphs = body_div.find_all("p")
        content = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if len(content) < 150:
            return None

        img_url = ""
        if image_tag and image_tag.get("src"):
            img_url = urljoin(self.base_url, image_tag.get("src"))

        return NewsArticle(
            title=title_tag.get_text(strip=True),
            content=content,
            url=url,
            source="The Hindu",
            author=author_tag.get_text(strip=True) if author_tag else "The Hindu Staff",
            published_at=date_tag.get_text(strip=True) if date_tag else datetime.now().isoformat(),
            category=category,
            image_url=img_url,
        )

    def scrape(self) -> list[NewsArticle]:
        articles = []
        for category, rss_url in self.RSS_FEEDS.items():
            entries = self.fetch_rss(rss_url)
            logger.info(f"[The Hindu] {category}: {len(entries)} RSS entries")
            for entry in entries[:3]:
                url = entry.get("link", "")
                if not url:
                    continue
                article = self.parse_article(url, category)
                if not article:
                    summary = entry.get('summary', '') or entry.get('description', '')
                    if summary:
                        from bs4 import BeautifulSoup
                        summary = BeautifulSoup(summary, 'lxml').get_text(strip=True)
                        if len(summary) > 40:
                            from datetime import datetime
                            from scraper.news_scraper import NewsArticle
                            article = NewsArticle(
                                title=entry.get('title', ''),
                                content=summary,
                                url=url,
                                source=self.name,
                                author='Staff',
                                published_at=datetime.now().isoformat(),
                                category=category,
                                image_url=self.extract_entry_image(entry)
                            )
                if article:
                    articles.append(article)
        return articles


# ── India Today ──────────────────────────────────────────────────────────────

class IndiaTodayScraper(BaseScraper):
    RSS_FEEDS = {
        "india":         "https://www.indiatoday.in/rss/1206578",
        "world":         "https://www.indiatoday.in/rss/1206614",
        "business":      "https://www.indiatoday.in/rss/1206644",
        "technology":    "https://www.indiatoday.in/rss/1206604",
        "sports":        "https://www.indiatoday.in/rss/1206590",
        "cricket":       "https://www.indiatoday.in/rss/1206592",
        "entertainment": "https://www.indiatoday.in/rss/1206579",
        "movies":        "https://www.indiatoday.in/rss/1206669",
        "health":        "https://www.indiatoday.in/rss/1206607",
        "education":     "https://www.indiatoday.in/rss/1206588",
        "lifestyle":     "https://www.indiatoday.in/rss/1206612",
        "auto":          "https://www.indiatoday.in/rss/1206603",
    }

    def __init__(self):
        super().__init__("India Today", "https://www.indiatoday.in")

    def parse_article(self, url: str, category: str) -> Optional[NewsArticle]:
        soup = self.get(url)
        if not soup:
            return None

        title_tag  = soup.select_one("h1.story__title, h1.field-title, h1")
        body_div   = soup.select_one("div.story__content, div.jsx-story__content, div#storyContent")
        image_tag  = soup.select_one("div.story-header__img img, div.lead-media img")
        author_tag = soup.select_one("a.authorName, div.author-name")
        date_tag   = soup.select_one("span.publish-time, time")

        if not title_tag or not body_div:
            return None

        paragraphs = body_div.find_all("p")
        content = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if len(content) < 150:
            return None

        img_url = ""
        if image_tag and image_tag.get("src"):
            img_url = urljoin(self.base_url, image_tag.get("src"))

        return NewsArticle(
            title=title_tag.get_text(strip=True),
            content=content,
            url=url,
            source="India Today",
            author=author_tag.get_text(strip=True) if author_tag else "India Today Staff",
            published_at=date_tag.get("datetime", datetime.now().isoformat()) if date_tag else datetime.now().isoformat(),
            category=category,
            image_url=img_url,
        )

    def scrape(self) -> list[NewsArticle]:
        articles = []
        for category, rss_url in self.RSS_FEEDS.items():
            entries = self.fetch_rss(rss_url)
            logger.info(f"[India Today] {category}: {len(entries)} RSS entries")
            for entry in entries[:3]:
                url = entry.get("link", "")
                if not url:
                    continue
                article = self.parse_article(url, category)
                if not article:
                    summary = entry.get('summary', '') or entry.get('description', '')
                    if summary:
                        from bs4 import BeautifulSoup
                        summary = BeautifulSoup(summary, 'lxml').get_text(strip=True)
                        if len(summary) > 40:
                            from datetime import datetime
                            from scraper.news_scraper import NewsArticle
                            article = NewsArticle(
                                title=entry.get('title', ''),
                                content=summary,
                                url=url,
                                source=self.name,
                                author='Staff',
                                published_at=datetime.now().isoformat(),
                                category=category,
                                image_url=self.extract_entry_image(entry)
                            )
                if article:
                    articles.append(article)
        return articles


# ── NDTV ─────────────────────────────────────────────────────────────────────

class NDTVScraper(BaseScraper):
    RSS_FEEDS = {
        "india":         "https://feeds.feedburner.com/ndtvnews-india-news",
        "world":         "https://feeds.feedburner.com/ndtvnews-world-news",
        "business":      "https://feeds.feedburner.com/ndtvprofit-latest-news",
        "sports":        "https://feeds.feedburner.com/ndtvsports-latest",
        "cricket":       "https://feeds.feedburner.com/ndtvsports-cricket",
        "tech":          "https://feeds.feedburner.com/ndtvnews-tech-media-gadgets",
        "entertainment": "https://feeds.feedburner.com/ndtvmovies-latest",
        "health":        "https://feeds.feedburner.com/ndtvdoctor-latest",
        "education":     "https://feeds.feedburner.com/ndtveducation-latest",
        "auto":          "https://feeds.feedburner.com/ndtvauto-latest",
    }

    def __init__(self):
        super().__init__("NDTV", "https://www.ndtv.com")

    def parse_article(self, url: str, category: str) -> Optional[NewsArticle]:
        soup = self.get(url)
        if not soup:
            return None

        title_tag  = soup.select_one("h1.sp-ttl, h1.story__head, h1")
        body_div   = soup.select_one("div.sp-cn, div.story__content, article")
        image_tag  = soup.select_one("div.story-lede img, div.ins_storybody img")
        author_tag = soup.select_one("span.pst-by_nm, a.story__byline")
        date_tag   = soup.select_one("span.pst-by_info, time")

        if not title_tag or not body_div:
            return None

        paragraphs = body_div.find_all("p")
        content = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if len(content) < 150:
            return None

        img_url = ""
        if image_tag and image_tag.get("src"):
            img_url = urljoin(self.base_url, image_tag.get("src"))

        return NewsArticle(
            title=title_tag.get_text(strip=True),
            content=content,
            url=url,
            source="NDTV",
            author=author_tag.get_text(strip=True) if author_tag else "NDTV Staff",
            published_at=date_tag.get_text(strip=True) if date_tag else datetime.now().isoformat(),
            category=category,
            image_url=img_url,
        )

    def scrape(self) -> list[NewsArticle]:
        articles = []
        for category, rss_url in self.RSS_FEEDS.items():
            entries = self.fetch_rss(rss_url)
            logger.info(f"[NDTV] {category}: {len(entries)} RSS entries")
            for entry in entries[:3]:
                url = entry.get("link", "")
                if not url:
                    continue
                article = self.parse_article(url, category)
                if not article:
                    summary = entry.get('summary', '') or entry.get('description', '')
                    if summary:
                        from bs4 import BeautifulSoup
                        summary = BeautifulSoup(summary, 'lxml').get_text(strip=True)
                        if len(summary) > 40:
                            from datetime import datetime
                            from scraper.news_scraper import NewsArticle
                            article = NewsArticle(
                                title=entry.get('title', ''),
                                content=summary,
                                url=url,
                                source=self.name,
                                author='Staff',
                                published_at=datetime.now().isoformat(),
                                category=category,
                                image_url=self.extract_entry_image(entry)
                            )
                if article:
                    articles.append(article)
        return articles


# ── Times of India ────────────────────────────────────────────────────────────

class TimesOfIndiaScraper(BaseScraper):
    RSS_FEEDS = {
        "top":           "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "india":         "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",
        "world":         "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
        "business":      "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",
        "tech":          "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",
        "entertainment": "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms",
        "movies":        "https://timesofindia.indiatimes.com/rssfeeds/1081479874.cms",
        "sports":        "https://timesofindia.indiatimes.com/rssfeeds/4719161.cms",
        "cricket":       "https://timesofindia.indiatimes.com/rssfeeds/54829575.cms",
        "science":       "https://timesofindia.indiatimes.com/rssfeeds/2647163.cms",
        "health":        "https://timesofindia.indiatimes.com/rssfeeds/3908999.cms",
        "auto":          "https://timesofindia.indiatimes.com/rssfeeds/242781543.cms",
        "lifestyle":     "https://timesofindia.indiatimes.com/rssfeeds/2886704.cms",
        "education":     "https://timesofindia.indiatimes.com/rssfeeds/913168846.cms",
    }

    def __init__(self):
        super().__init__("Times of India", "https://timesofindia.indiatimes.com")

    def parse_article(self, url: str, category: str) -> Optional[NewsArticle]:
        soup = self.get(url)
        if not soup:
            return None

        title_tag = soup.select_one("h1.HNMDR, h1[class*='heading'], h1")
        body_div  = soup.select_one("div.ga-headlines, div._s30J, div.artText, article")
        image_tag = soup.select_one("figure img, div.lead-img img")
        author_tag = soup.select_one("div.ZxBIG a, span.author")

        if not title_tag or not body_div:
            return None

        paragraphs = body_div.find_all("p")
        content = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if len(content) < 150:
            return None

        img_url = ""
        if image_tag and image_tag.get("src"):
            img_url = urljoin(self.base_url, image_tag.get("src"))

        return NewsArticle(
            title=title_tag.get_text(strip=True),
            content=content,
            url=url,
            source="Times of India",
            author=author_tag.get_text(strip=True) if author_tag else "TOI Staff",
            published_at=datetime.now().isoformat(),
            category=category,
            image_url=img_url,
        )

    def scrape(self) -> list[NewsArticle]:
        articles = []
        for category, rss_url in self.RSS_FEEDS.items():
            entries = self.fetch_rss(rss_url)
            logger.info(f"[TOI] {category}: {len(entries)} RSS entries")
            for entry in entries[:2]:
                url = entry.get("link", "")
                if not url:
                    continue
                article = self.parse_article(url, category)
                if not article:
                    summary = entry.get('summary', '') or entry.get('description', '')
                    if summary:
                        from bs4 import BeautifulSoup
                        summary = BeautifulSoup(summary, 'lxml').get_text(strip=True)
                        if len(summary) > 40:
                            from datetime import datetime
                            from scraper.news_scraper import NewsArticle
                            article = NewsArticle(
                                title=entry.get('title', ''),
                                content=summary,
                                url=url,
                                source=self.name,
                                author='Staff',
                                published_at=datetime.now().isoformat(),
                                category=category,
                                image_url=self.extract_entry_image(entry)
                            )
                if article:
                    articles.append(article)
        return articles


# ── Hindustan Times ───────────────────────────────────────────────────────────

class HindustanTimesScraper(BaseScraper):
    RSS_FEEDS = {
        "india":         "https://www.hindustantimes.com/rss/india-news/rssfeed.xml",
        "world":         "https://www.hindustantimes.com/rss/world-news/rssfeed.xml",
        "business":      "https://www.hindustantimes.com/rss/business/rssfeed.xml",
        "tech":          "https://www.hindustantimes.com/rss/technology/rssfeed.xml",
        "entertainment": "https://www.hindustantimes.com/rss/entertainment/rssfeed.xml",
        "sports":        "https://www.hindustantimes.com/rss/sports/rssfeed.xml",
        "cricket":       "https://www.hindustantimes.com/rss/cricket/rssfeed.xml",
        "lifestyle":     "https://www.hindustantimes.com/rss/lifestyle/rssfeed.xml",
        "health":        "https://www.hindustantimes.com/rss/health/rssfeed.xml",
        "auto":          "https://www.hindustantimes.com/rss/auto/rssfeed.xml",
        "education":     "https://www.hindustantimes.com/rss/education/rssfeed.xml",
    }

    def __init__(self):
        super().__init__("Hindustan Times", "https://www.hindustantimes.com")

    def parse_article(self, url: str, category: str) -> Optional[NewsArticle]:
        soup = self.get(url)
        if not soup:
            return None

        title_tag  = soup.select_one("h1.hdg1, h1.storyPage_storyHeadline__3mMGq, h1")
        body_div   = soup.select_one("div.storyDetails, div.storyPage_storyDetails__Otbzj, article")
        image_tag  = soup.select_one("div.storyPage_storyImg__e_fqk img, figure img")
        author_tag = soup.select_one("div.authorName, span.authorName")

        if not title_tag or not body_div:
            return None

        paragraphs = body_div.find_all("p")
        content = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if len(content) < 150:
            return None

        img_url = ""
        if image_tag and image_tag.get("src"):
            img_url = urljoin(self.base_url, image_tag.get("src"))

        return NewsArticle(
            title=title_tag.get_text(strip=True),
            content=content,
            url=url,
            source="Hindustan Times",
            author=author_tag.get_text(strip=True) if author_tag else "HT Staff",
            published_at=datetime.now().isoformat(),
            category=category,
            image_url=img_url,
        )

    def scrape(self) -> list[NewsArticle]:
        articles = []
        for category, rss_url in self.RSS_FEEDS.items():
            entries = self.fetch_rss(rss_url)
            logger.info(f"[HT] {category}: {len(entries)} RSS entries")
            for entry in entries[:2]:
                url = entry.get("link", "")
                if not url:
                    continue
                article = self.parse_article(url, category)
                if not article:
                    summary = entry.get('summary', '') or entry.get('description', '')
                    if summary:
                        from bs4 import BeautifulSoup
                        summary = BeautifulSoup(summary, 'lxml').get_text(strip=True)
                        if len(summary) > 40:
                            from datetime import datetime
                            from scraper.news_scraper import NewsArticle
                            article = NewsArticle(
                                title=entry.get('title', ''),
                                content=summary,
                                url=url,
                                source=self.name,
                                author='Staff',
                                published_at=datetime.now().isoformat(),
                                category=category,
                                image_url=self.extract_entry_image(entry)
                            )
                if article:
                    articles.append(article)
        return articles


# ── Aggregator ────────────────────────────────────────────────────────────────

class NewsAggregator:
    def __init__(self, sources: list[str] = None):
        self.scrapers = {
            "the_hindu":        TheHinduScraper(),
            "india_today":      IndiaTodayScraper(),
            "ndtv":             NDTVScraper(),
            "times_of_india":   TimesOfIndiaScraper(),
            "hindustan_times":  HindustanTimesScraper(),
        }
        self.enabled_sources = sources or list(self.scrapers.keys())
        self._seen_ids: set = set()
        self._load_seen_ids()

    def _seen_ids_path(self) -> str:
        return os.path.join(os.path.dirname(__file__), "..", "logs", "seen_ids.json")

    def _load_seen_ids(self):
        try:
            path = self._seen_ids_path()
            if os.path.exists(path):
                with open(path) as f:
                    self._seen_ids = set(json.load(f))
        except Exception:
            self._seen_ids = set()

    def _save_seen_ids(self):
        try:
            path = self._seen_ids_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Keep only last 5000 IDs
            ids = list(self._seen_ids)[-5000:]
            with open(path, "w") as f:
                json.dump(ids, f)
        except Exception:
            pass

    def fetch_all(self, max_per_source: int = 3) -> list[NewsArticle]:
        all_articles: list[NewsArticle] = []

        for key, scraper in self.scrapers.items():
            if key not in self.enabled_sources:
                continue
            try:
                logger.info(f"Scraping {scraper.name}...")
                articles = scraper.scrape()
                # Filter already-seen
                new_articles = [a for a in articles if a.id not in self._seen_ids]
                new_articles = new_articles[:max_per_source]
                for a in new_articles:
                    self._seen_ids.add(a.id)
                all_articles.extend(new_articles)
                logger.success(f"[{scraper.name}] Got {len(new_articles)} new articles")
            except Exception as e:
                logger.error(f"[{scraper.name}] Scraper failed: {e}")

        self._save_seen_ids()
        logger.info(f"Total new articles: {len(all_articles)}")
        return all_articles
