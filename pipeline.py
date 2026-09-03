"""
pipeline.py
Main orchestrator — Shorts-only pipeline.

Flow
────
  scrape_and_enqueue()   Scrapes all sources, stores compact stubs in the queue.
  process_one()          Pops the next pending stub, generates a 25–35 s Short,
                         uploads it immediately.
  run_daily_bundle()     Ranks today's top 30 articles, composes 3×60 s parts,
                         schedules them for the next IST peak time.
  run_weekly_bundle()    Ranks this week's top 100 articles, composes 10×60 s
                         parts, schedules them for Sunday 10:00 AM IST.
  run_drip()             Called by the scheduler every PUBLISH_INTERVAL_MINUTES
                         — pops + processes ONE article from the queue.
  run()                  Legacy one-shot mode: scrapes AND immediately processes
                         up to max_articles.
"""

import os
import sys
import json
import time
import hashlib
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from scraper.news_scraper import NewsAggregator
from content.ai_generator import get_generator
from video.tts_engine import TTSEngine
from video.thumbnail_generator import ThumbnailGenerator
from video.shorts_composer import ShortsComposer
from video.bundle_shorts_composer import BundleShortsComposer
from uploader.youtube_uploader import YouTubeUploader
from article_ranker import ArticleRanker
from trending_topics import get_trending_topics
from trending_filter import TrendingFilter
from sources.api_sources import fetch_all_api_sources
from scheduler.peak_times import schedule_mode, next_peak_slot, format_slot
import article_queue as queue

console = Console()


@dataclass
class PipelineResult:
    article_id: str
    title: str
    source: str
    status: str          # success | failed | skipped
    short_path: str = ""
    thumbnail_path: str = ""
    shorts_url: str = ""
    error: str = ""
    duration_s: float = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)


class NewsPipeline:
    def __init__(self):
        # Config from env
        self.output_dir       = os.getenv("OUTPUT_DIR",    "./output")
        self.shorts_dir       = os.getenv("SHORTS_DIR",    "./output/shorts")
        self.thumbnail_dir    = os.getenv("THUMBNAIL_DIR", "./output/thumbnails")
        self.tts_engine_name  = os.getenv("TTS_ENGINE",    "kokoro")
        self.tts_language     = os.getenv("VOICE_LANGUAGE","en")
        self.max_articles     = int(os.getenv("MAX_ARTICLES_PER_RUN", "35"))
        self.privacy          = os.getenv("DEFAULT_PRIVACY", "public")
        self.channel_name     = os.getenv("CHANNEL_NAME", "News Channel")
        # How many minutes to wait between publishing individual videos
        self.publish_interval = int(os.getenv("PUBLISH_INTERVAL_MINUTES", "15"))

        # Bundle sizes
        self.daily_bundle_size  = int(os.getenv("DAILY_BUNDLE_SIZE",  "30"))
        self.weekly_bundle_size = int(os.getenv("WEEKLY_BUNDLE_SIZE", "100"))

        # Peak times (IST) for daily bundle — list of (h, m) tuples
        _daily_times_str = os.getenv("DAILY_BUNDLE_TIMES", "08:00,18:00")
        self.daily_peak_times = [
            tuple(int(x) for x in t.strip().split(":"))
            for t in _daily_times_str.split(",")
            if ":" in t
        ]  # e.g. [(8, 0), (18, 0)]

        # Weekly bundle — Sunday 10:00 AM IST by default
        _weekly_time_str = os.getenv("WEEKLY_BUNDLE_TIME", "10:00")
        _wt = _weekly_time_str.strip().split(":")
        self.weekly_peak_hour   = int(_wt[0])
        self.weekly_peak_minute = int(_wt[1]) if len(_wt) > 1 else 0

        # Component instantiation
        self.aggregator      = NewsAggregator()
        self.ai_gen          = get_generator()
        self.tts             = TTSEngine(self.tts_engine_name, self.tts_language, self.output_dir)
        self.thumbnailer     = ThumbnailGenerator(self.thumbnail_dir)
        self.shorts_composer = ShortsComposer(self.shorts_dir)
        self.bundle_composer = BundleShortsComposer(self.shorts_dir)
        self.uploader        = YouTubeUploader(channel_name=self.channel_name)
        self.ranker          = ArticleRanker()

        self.results: list[PipelineResult] = []
        self._log_path = Path("./logs/pipeline_log.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        # Fix any stubs left in 'processing' from a previous crash
        queue.requeue_stalled()

        # Re-attempt any uploads blocked by yesterday's quota limit
        self._retry_quota_uploads()
        
        # Sync channel info (logo, name) from YouTube API if authenticated
        self._sync_channel_info()

    def _retry_quota_uploads(self):
        """Re-try uploads that were quota-blocked. Safe to call at startup."""
        try:
            results = self.uploader.retry_pending_uploads()
            if results:
                n_ok  = sum(1 for r in results if r.success)
                n_qex = sum(1 for r in results if r.quota_exceeded)
                logger.info(f"Retry queue: {n_ok} succeeded, {n_qex} still quota-limited, "
                            f"{len(results)-n_ok-n_qex} failed.")
        except Exception as e:
            logger.warning(f"Retry queue check failed: {e}")

    def _sync_channel_info(self):
        """Fetch channel name, logo, and SEO context from YouTube API if authenticated."""
        if not self.uploader.is_authenticated():
            return
            
        logger.info("Syncing channel info from YouTube API...")
        info = self.uploader.get_channel_info()
        if not info:
            return
            
        title = info.get("title")
        if title and title != "Unknown":
            self.channel_name = title
            os.environ["CHANNEL_NAME"] = title
            logger.info(f"Set channel name to: {title}")
            
        thumb_url = info.get("thumbnail")
        if thumb_url:
            import requests
            try:
                resp = requests.get(thumb_url, timeout=5)
                if resp.status_code == 200:
                    assets_dir = Path("assets")
                    assets_dir.mkdir(exist_ok=True)
                    logo_path = assets_dir / "channel_logo.png"
                    with open(logo_path, "wb") as f:
                        f.write(resp.content)
                    logger.info("Channel logo synced and saved.")
            except Exception as e:
                logger.warning(f"Failed to download channel logo: {e}")

        # Fetch dynamic SEO context
        seo_context = self.uploader.get_top_performing_seo_context()
        if seo_context:
            os.environ["CHANNEL_SEO_CONTEXT"] = seo_context
            logger.info("Channel SEO context synced successfully.")

    # ── Queue-aware API ───────────────────────────────────────────────────────

    def scrape_and_enqueue(self) -> int:
        """
        1. Fetch what's trending on YouTube India right now.
        2. Scrape all RSS feeds.
        3. Score & filter articles by relevance to trending topics.
        4. Enqueue only the relevant articles.
        Returns number of articles enqueued.
        """
        console.rule("[bold red]YouTube News Bot — Trending-Driven Scrape & Enqueue")

        # ── Step 1: Get trending topics ───────────────────────────────────────
        console.print("\n[cyan]Fetching trending topics from YouTube...[/]")
        try:
            topics = get_trending_topics(self.uploader)
        except Exception as e:
            logger.warning(f"Trending fetch failed: {e} — scraping without filter")
            topics = []

        if topics:
            console.print(
                f"[green]Trending topics ({len(topics)}):[/] "
                + ", ".join(f"[yellow]{t}[/]" for t in topics[:10])
                + (f" [dim]+{len(topics)-10} more[/]" if len(topics) > 10 else "")
            )
        else:
            console.print("[yellow]No trending topics found — will enqueue top scored articles.[/]")

        # ── Step 2: Scrape RSS + fetch API sources ────────────────────────────────
        console.print("\n[cyan]Scraping news sources + fetching live API data...[/]")
        rss_articles = self.aggregator.fetch_all(max_per_source=3)

        # Fetch from free public APIs (crypto, sports, finance, tech, etc.)
        console.print("[cyan]Fetching live data from public APIs...[/]")
        try:
            api_articles = fetch_all_api_sources(max_total=20)
            console.print(f"[dim]  API sources: {len(api_articles)} articles[/]")
        except Exception as e:
            logger.warning(f"API sources fetch failed: {e}")
            api_articles = []

        articles = rss_articles + api_articles

        if not articles:
            console.print("[yellow]No new articles found.[/]")
            return 0

        console.print(
            f"[dim]Total: {len(rss_articles)} RSS + {len(api_articles)} API = "
            f"{len(articles)} raw articles[/]"
        )

        # ── Step 3: Filter by trending relevance ──────────────────────────────
        if topics:
            tf = TrendingFilter(topics)
            articles = tf.filter_articles(
                articles,
                threshold=0.35,
                top_n=self.max_articles,
                min_articles=8,
            )
            console.print(
                f"[green]After trend filter: {len(articles)} relevant articles[/] "
                f"[dim](threshold 0.35)[/]"
            )
        else:
            articles = articles[: self.max_articles]

        # ── Step 4: Enqueue ───────────────────────────────────────────────────
        added = queue.enqueue_batch(articles)
        stats = queue.queue_stats()
        console.print(
            f"[green]Enqueued {added} new articles.[/] "
            f"Queue: [yellow]{stats['pending']} pending[/] / "
            f"[green]{stats['done']} done[/] / "
            f"[red]{stats['failed']} failed[/]"
        )
        return added

    def process_one(self, dry_run: bool = False) -> Optional[PipelineResult]:
        """
        Pop the next pending article from the queue and run the full pipeline for it.
        Returns the PipelineResult, or None if the queue is empty.
        """
        stub = queue.pop_next()
        if stub is None:
            console.print("[yellow]Queue is empty — nothing to process.[/]")
            return None

        article = queue.stub_to_article(stub)
        console.rule(f"[dim]Processing from queue: {article.title[:60]}")
        result = self._process_article(article, dry_run=dry_run)
        self._save_result(result)

        if result.status == "success":
            queue.mark_done(article.id)
        else:
            queue.mark_failed(article.id, result.error)

        return result

    def run_drip(self, dry_run: bool = False) -> Optional[PipelineResult]:
        """
        Called by the drip scheduler every PUBLISH_INTERVAL_MINUTES.
        Pops and processes exactly ONE article from the queue.
        """
        stats = queue.queue_stats()
        logger.info(
            f"Drip tick — queue: {stats['pending']} pending / "
            f"{stats['done']} done / {stats['failed']} failed"
        )
        if stats["pending"] == 0:
            logger.info("Queue empty — nothing to drip-publish.")
            return None
        return self.process_one(dry_run=dry_run)

    # ── Trending topic video ──────────────────────────────────────────────────

    _TRENDING_LOG_PATH = Path("./logs/trending_video_log.json")

    def _load_trending_done_today(self) -> set:
        """Return the set of trending topics already processed today."""
        try:
            if not self._TRENDING_LOG_PATH.exists():
                return set()
            data = json.loads(self._TRENDING_LOG_PATH.read_text(encoding="utf-8"))
            if data.get("date") != date.today().isoformat():
                return set()  # stale — new day
            return set(data.get("topics", []))
        except Exception as e:
            logger.debug(f"[Trending] Could not load daily log: {e}")
            return set()

    def _save_trending_done(self, topics_done: set) -> None:
        """Persist today's processed trending topics."""
        try:
            self._TRENDING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._TRENDING_LOG_PATH.write_text(
                json.dumps(
                    {"date": date.today().isoformat(), "topics": sorted(topics_done)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"[Trending] Could not save daily log: {e}")

    def _make_trending_article(self, topic: str):
        """
        Build a synthetic NewsArticle from a trending topic string.
        The article has enough content for the AI generator to produce
        a compelling Short script about this trending subject.
        """
        from scraper.news_scraper import NewsArticle

        unique_id = hashlib.md5(f"trending::{topic}::{date.today().isoformat()}".encode()).hexdigest()[:12]
        content = (
            f"{topic} is currently one of the most trending topics globally. "
            f"This topic is capturing widespread attention across social media, YouTube, "
            f"and news platforms. Here is what people need to know about '{topic}': "
            f"It has emerged as a major conversation point today, drawing significant "
            f"public interest and engagement worldwide. Audiences are actively searching "
            f"for the latest updates, expert analysis, and breaking developments related "
            f"to this subject. Stay informed with the most recent news and insights on "
            f"'{topic}' as this story continues to develop."
        )
        return NewsArticle(
            id=unique_id,
            title=f"Trending Now: {topic}",
            content=content,
            summary=content[:300],
            url=f"https://trends.google.com/trending?geo=IN&q={topic.replace(' ', '+')}",
            source="Trending Topics",
            author="News Desk",
            published_at=datetime.now().isoformat(),
            category="trending",
            image_url="",
        )

    def run_trending_topic_video(self, dry_run: bool = False) -> Optional[PipelineResult]:
        """
        Guarantee at least one video is generated for a trending topic each cycle.

        Flow
        ────
        1. Fetch current trending topics (uses cache if fresh).
        2. Skip topics already processed today.
        3. Build a synthetic NewsArticle for the top uncovered topic.
        4. Run the full _process_article() pipeline (AI gen → TTS → Short → upload).
        5. Upload follows the same peak-time scheduling logic as regular articles.

        Returns PipelineResult or None if all topics are covered or topics empty.
        """
        console.rule("[bold magenta]Trending Topic Video — Guaranteed Coverage")

        # 1. Fetch topics (respects 30-min cache)
        try:
            topics = get_trending_topics(self.uploader)
        except Exception as e:
            logger.warning(f"[Trending] Topic fetch failed: {e}")
            topics = []

        if not topics:
            console.print("[yellow]No trending topics available — skipping trending video.[/]")
            return None

        # 2. Skip already-processed topics today
        done_today = self._load_trending_done_today()
        remaining = [t for t in topics if t.lower() not in {d.lower() for d in done_today}]

        if not remaining:
            console.print(
                f"[yellow]All {len(topics)} trending topics already covered today — skipping.[/]"
            )
            return None

        topic = remaining[0]
        console.print(
            f"[cyan]Generating trending topic video for:[/] [bold yellow]{topic}[/] "
            f"[dim]({len(done_today)} covered today, {len(remaining)-1} remaining)[/]"
        )

        # 3. Build synthetic article
        article = self._make_trending_article(topic)

        # 4. Run through the exact same pipeline
        result = self._process_article(article, dry_run=dry_run)
        self._save_result(result)

        # 5. Mark topic as done regardless of success/failure (avoid infinite retries)
        done_today.add(topic)
        self._save_trending_done(done_today)

        if result.status == "success":
            console.print(
                f"  [green]Trending video OK:[/] {result.shorts_url or 'uploaded'} "
                f"[dim]topic: {topic}[/]"
            )
        else:
            console.print(
                f"  [red]Trending video failed:[/] {result.error} [dim]topic: {topic}[/]"
            )

        return result

    # ── Legacy one-shot run (dashboard "Run Now" button) ─────────────────────

    def run(self, dry_run: bool = False) -> list[PipelineResult]:
        """
        Classic mode: scrape → enqueue → immediately process everything in the queue.
        Used by the dashboard Run Now button and the --once CLI flag.
        """
        run_start = time.time()
        console.rule("[bold red]YouTube News Bot — Full Pipeline Run")

        # 1. Scrape into queue
        added = self.scrape_and_enqueue()
        if added == 0 and queue.queue_stats()["pending"] == 0:
            console.print("[yellow]No articles to process.[/]")
            return []

        # 2. Process everything currently pending
        results = []
        pending = queue.queue_stats()["pending"]
        console.print(f"\n[cyan]Processing {pending} queued articles...[/]")
        for i in range(pending):
            console.rule(f"[dim]Article {i+1}/{pending}")
            result = self.process_one(dry_run=dry_run)
            if result is None:
                break
            results.append(result)

        self.results = results
        self._print_summary(results, time.time() - run_start)
        return results

    # ── Article processing ────────────────────────────────────────────────────

    def _process_article(self, article, dry_run: bool) -> PipelineResult:
        """
        Shorts-only pipeline for a single article:
          1. AI content generation (produces short_script: 55-65 words)
          2. TTS synthesis on short_script only
          3. Thumbnail generation (used as background for Short)
          4. Short composition (25-35 s, 9:16)
          5. Upload Short immediately
        Full 16:9 video generation is disabled.
        """
        start = time.time()
        result = PipelineResult(
            article_id=article.id,
            title=article.title,
            source=article.source,
            status="failed",
        )

        temp_files = []

        try:
            # 1. AI Content Generation
            console.print(f"[cyan]  -> Generating AI content...")
            vc = self.ai_gen.generate(article)
            if not vc:
                result.error = "AI generation returned None"
                return result

            # Attach image URL for Short background
            vc._image_url = getattr(article, "image_url", "")

            console.print(f"  [green]OK[/] Title: {vc.title[:70]}")

            # 2. Text-to-Speech (short_script only — 25-35 s)
            console.print(f"[cyan]  -> Synthesizing speech (short script)...")
            short_text = getattr(vc, "short_script", "") or vc.script[:400]
            audio_path = self.tts.synthesize(short_text, f"{article.id}_short_narration.mp3")
            if audio_path:
                temp_files.append(audio_path)
            console.print(f"  [green]OK[/] Audio: {audio_path}")

            # 3. Thumbnail (background image for Short)
            console.print(f"[cyan]  -> Generating thumbnail...")
            thumb_path = self.thumbnailer.generate(vc, article.id)
            result.thumbnail_path = thumb_path
            if thumb_path:
                temp_files.append(thumb_path)
            console.print(f"  [green]OK[/] Thumbnail: {thumb_path}")

            # Enrich description with source link
            if getattr(article, 'url', '') and article.url not in vc.description:
                vc.description = (
                    vc.description.rstrip()
                    + f"\n\n🔗 Source: {article.url}"
                    + (f"\n📰 Credit: {article.source}" if article.source else "")
                )
            vc.source_url  = getattr(article, 'url', '')
            vc.source_name = getattr(article, 'source', '')

            # 4. Short composition (25-35 s, 9:16 vertical)
            console.print(f"[cyan]  -> Composing Short (25-35 s)...")
            short_path = self.shorts_composer.compose(audio_path, vc, article.id)
            result.short_path = short_path
            if short_path:
                temp_files.append(short_path)
                console.print(f"  [green]OK[/] Short: {short_path}")
            else:
                result.error = "Short composition failed"
                return result

            if dry_run:
                console.print("  [yellow]DRY RUN -- skipping upload.[/]")
                result.status = "success"
                result.shorts_url = "DRY_RUN"
            else:
                # 5. Upload Short -- immediately OR at next global peak time
                mode = schedule_mode()
                if mode == "peak":
                    publish_at = next_peak_slot()
                    console.print(
                        f"[cyan]  -> Scheduling Short to peak slot: "
                        f"{format_slot(publish_at)}[/]"
                    )
                    short_upload = self.uploader.upload_short_scheduled(
                        short_path, vc, publish_at
                    )
                    if short_upload.success:
                        result.shorts_url = short_upload.video_url
                        result.status = "success"
                        console.print(
                            f"  [green]OK Scheduled Short:[/] {short_upload.video_url} "
                            f"[dim]-> publishes {format_slot(publish_at)}[/]"
                        )
                    elif short_upload.quota_exceeded:
                        result.shorts_url = "QUOTA_EXCEEDED"
                        result.status = "success"
                        if short_path in temp_files:
                            temp_files.remove(short_path)
                        console.print("  [yellow]Quota exceeded -- Short saved to retry queue.[/]")
                    else:
                        result.error = short_upload.error
                        result.status = "failed"
                        console.print(f"  [red]Scheduled upload failed: {short_upload.error}[/]")
                else:
                    console.print(f"[cyan]  -> Uploading Short to YouTube immediately...[/]")
                    short_upload = self.uploader.upload_short(short_path, vc, self.privacy)
                    if short_upload.success:
                        result.shorts_url = short_upload.video_url
                        result.status = "success"
                        console.print(f"  [green]OK Short:[/] {short_upload.video_url}")
                    elif short_upload.quota_exceeded:
                        result.shorts_url = "QUOTA_EXCEEDED"
                        result.status = "success"
                        if short_path in temp_files:
                            temp_files.remove(short_path)
                        console.print("  [yellow]Quota exceeded -- Short saved to retry queue.[/]")
                    else:
                        result.error = short_upload.error
                        result.status = "failed"
                        console.print(f"  [red]Short upload failed: {short_upload.error}[/]")


        except Exception as e:
            result.error = str(e)
            result.status = "failed"
            logger.exception(f"Pipeline failed for {article.id}")
        finally:
            # Clean up all local files generated for this article.
            for fp in temp_files:
                if fp and os.path.exists(fp):
                    for attempt in range(5):
                        try:
                            os.remove(fp)
                            logger.debug(f"Cleaned up local file: {fp}")
                            break
                        except PermissionError:
                            if attempt < 4:
                                time.sleep(0.5 * (attempt + 1))
                            else:
                                logger.warning(f"Could not remove {fp} after 5 attempts")
                        except Exception as ex:
                            logger.warning(f"Could not remove {fp}: {ex}")
                            break

        result.duration_s = round(time.time() - start, 1)
        return result

    # ── Bundle generation ─────────────────────────────────────────────────────

    def run_daily_bundle(self, dry_run: bool = False) -> list[str]:
        """
        Build and upload the daily Top-30 bundle Shorts (3 parts × 10 items).
        Each part is scheduled to auto-publish at the next IST peak time (8 AM or 6 PM).
        Returns list of YouTube Short URLs (or DRY_RUN strings).
        """
        console.rule("[bold red]Daily Bundle — Top 30 News")
        from datetime import date
        bundle_id = date.today().isoformat()

        # Refresh ranker with current timestamp
        ranker = ArticleRanker()
        stubs = ranker.rank_daily(n=self.daily_bundle_size)

        if not stubs:
            console.print("[yellow]Daily bundle: no articles found in the last 24 h.[/]")
            return []

        console.print(f"[cyan]Composing daily bundle ({len(stubs)} articles → 3 parts)...")
        part_paths = self.bundle_composer.compose(
            stubs=stubs,
            mode="daily",
            bundle_id=bundle_id,
            channel_name=self.channel_name,
        )

        if not part_paths:
            console.print("[red]Daily bundle composition failed.[/]")
            return []

        urls = []
        for part_num, part_path in enumerate(part_paths, start=1):
            # Build a minimal VideoContent-like object for the uploader
            vc = _BundleVideoContent(
                title=f"Top {self.daily_bundle_size} News Today — Part {part_num} | {bundle_id}",
                description=(
                    f"Today's top {self.daily_bundle_size} most important news stories from India and around the world.\n"
                    f"Part {part_num} of {len(part_paths)} — stories #{(part_num-1)*10+1}–#{part_num*10}.\n\n"
                    f"#Shorts #TopNews #NewsRoundup #IndiaNews #BreakingNews"
                ),
                tags=["Shorts", "TopNews", "NewsRoundup", "IndiaNews", "BreakingNews",
                      "DailyNews", "NewsToday"],
                category="news",
            )

            if dry_run:
                console.print(f"  [yellow]DRY RUN — would upload part {part_num}: {part_path}[/]")
                urls.append("DRY_RUN")
                continue

            # Schedule at next IST peak time
            publish_at = self.uploader.get_next_peak_ist(self.daily_peak_times)
            console.print(
                f"[cyan]  Uploading daily bundle part {part_num} → "
                f"scheduled for {publish_at.strftime('%Y-%m-%d %H:%M UTC')}..."
            )
            result = self.uploader.upload_short_scheduled(
                video_path=part_path,
                video_content=vc,
                publish_at_utc=publish_at,
            )
            if result.success:
                urls.append(result.video_url)
                console.print(f"  [green]OK:[/] {result.video_url}")
            else:
                console.print(f"  [red]Failed:[/] {result.error}")
                urls.append("")

            # Clean up part file after successful upload
            if result.success and os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass

        return urls

    def run_weekly_bundle(self, dry_run: bool = False) -> list[str]:
        """
        Build and upload the weekly Top-100 bundle Shorts (10 parts × 10 items).
        All parts are scheduled for Sunday 10:00 AM IST.
        Returns list of YouTube Short URLs.
        """
        console.rule("[bold red]Weekly Bundle — Top 100 News")
        from datetime import date
        # ISO week label e.g. "2026-W22"
        bundle_id = f"{date.today().year}-W{date.today().isocalendar()[1]:02d}"

        ranker = ArticleRanker()
        stubs = ranker.rank_weekly(n=self.weekly_bundle_size)

        if not stubs:
            console.print("[yellow]Weekly bundle: no articles found in the last 7 days.[/]")
            return []

        console.print(f"[cyan]Composing weekly bundle ({len(stubs)} articles → 10 parts)...")
        part_paths = self.bundle_composer.compose(
            stubs=stubs,
            mode="weekly",
            bundle_id=bundle_id,
            channel_name=self.channel_name,
        )

        if not part_paths:
            console.print("[red]Weekly bundle composition failed.[/]")
            return []

        urls = []
        for part_num, part_path in enumerate(part_paths, start=1):
            vc = _BundleVideoContent(
                title=f"Top {self.weekly_bundle_size} News This Week — Part {part_num} | {bundle_id}",
                description=(
                    f"This week's top {self.weekly_bundle_size} most important news from India and the world.\n"
                    f"Part {part_num} of {len(part_paths)} — stories #{(part_num-1)*10+1}–#{part_num*10}.\n\n"
                    f"#Shorts #WeeklyNews #TopNews #IndiaNews #NewsRoundup"
                ),
                tags=["Shorts", "WeeklyNews", "TopNews", "IndiaNews", "NewsRoundup",
                      "Weekly", "BestNews"],
                category="news",
            )

            if dry_run:
                console.print(f"  [yellow]DRY RUN — would upload weekly part {part_num}: {part_path}[/]")
                urls.append("DRY_RUN")
                continue

            # All weekly parts scheduled for Sunday 10:00 AM IST
            publish_at = self.uploader.get_next_weekly_peak_ist(
                weekday=6,
                hour_ist=self.weekly_peak_hour,
                minute_ist=self.weekly_peak_minute,
            )
            console.print(
                f"[cyan]  Uploading weekly part {part_num} → "
                f"scheduled for {publish_at.strftime('%Y-%m-%d %H:%M UTC')}..."
            )
            result = self.uploader.upload_short_scheduled(
                video_path=part_path,
                video_content=vc,
                publish_at_utc=publish_at,
            )
            if result.success:
                urls.append(result.video_url)
                console.print(f"  [green]OK:[/] {result.video_url}")
            else:
                console.print(f"  [red]Failed:[/] {result.error}")
                urls.append("")

            if result.success and os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except Exception:
                    pass

        return urls

    # ── Logging ───────────────────────────────────────────────────────────────

    def _save_result(self, result: PipelineResult):
        with open(self._log_path, "a") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    def _print_summary(self, results: list, elapsed: float):
        console.rule("[bold]Pipeline Summary")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Source",    width=18)
        table.add_column("Title",     width=40)
        table.add_column("Status",    width=10)
        table.add_column("Shorts URL",width=40)

        for r in results:
            if r.status == "success":
                status = "[green]SUCCESS[/]"
            elif r.status == "skipped":
                status = "[yellow]DRY RUN[/]"
            else:
                status = f"[red]FAIL: {r.error[:25]}[/]"

            table.add_row(
                r.source,
                r.title[:40] + "...",
                status,
                r.shorts_url or "—",
            )

        console.print(table)
        success_n = sum(1 for r in results if r.status == "success")
        console.print(
            f"\n[bold]Processed {len(results)} articles | "
            f"[green]{success_n} succeeded[/] | "
            f"[red]{len(results)-success_n} failed[/] | "
            f"Time: {elapsed:.1f}s[/]"
        )


# ── Drip Scheduler ─────────────────────────────────────────────────────────────

def run_drip_scheduler():
    """
    Separate scheduler mode:
      - Every SCRAPE_INTERVAL_MINUTES     → scrape + enqueue new articles
      - Every PUBLISH_INTERVAL_MINUTES    → pop + process ONE article from queue (individual Short)
      - Every TRENDING_VIDEO_INTERVAL_MINUTES → generate at least one Short for a trending topic
      - Daily at 8:00 AM IST and 6:00 PM IST  → generate + schedule daily Top-30 bundle
      - Every Sunday at 10:00 AM IST          → generate + schedule weekly Top-100 bundle

    Example env:
      SCRAPE_INTERVAL_MINUTES=120
      PUBLISH_INTERVAL_MINUTES=15
      TRENDING_VIDEO_INTERVAL_MINUTES=60
      DAILY_BUNDLE_TIMES=08:00,18:00
      WEEKLY_BUNDLE_TIME=10:00
    """
    import schedule

    scrape_interval   = int(os.getenv("SCRAPE_INTERVAL_MINUTES",          "120"))
    publish_interval  = int(os.getenv("PUBLISH_INTERVAL_MINUTES",         "15"))
    trending_interval = int(os.getenv("TRENDING_VIDEO_INTERVAL_MINUTES",  "60"))

    # Daily bundle IST times — default 08:00 and 18:00
    daily_times_str = os.getenv("DAILY_BUNDLE_TIMES", "08:00,18:00")
    daily_times_ist = [t.strip() for t in daily_times_str.split(",") if ":" in t]

    # Weekly bundle IST time — default Sunday 10:00
    weekly_time_ist = os.getenv("WEEKLY_BUNDLE_TIME", "10:00")

    pipeline = NewsPipeline()

    def scrape_job():
        logger.info("Scrape job triggered — fetching & enqueueing articles...")
        pipeline.scrape_and_enqueue()
        queue.purge_old(keep_days=7)

    def publish_job():
        pipeline.run_drip()

    def trending_video_job():
        """Generate at least one Short for the top uncovered trending topic."""
        logger.info("Trending video job triggered — generating topic video...")
        try:
            result = pipeline.run_trending_topic_video()
            if result:
                logger.info(
                    f"Trending video job done: status={result.status} "
                    f"url={result.shorts_url or '—'}"
                )
        except Exception as e:
            logger.error(f"Trending video job failed: {e}")

    def daily_bundle_job():
        logger.info("Daily bundle job triggered.")
        try:
            urls = pipeline.run_daily_bundle()
            logger.info(f"Daily bundle: {len(urls)} parts uploaded/scheduled.")
        except Exception as e:
            logger.error(f"Daily bundle job failed: {e}")

    def weekly_bundle_job():
        logger.info("Weekly bundle job triggered.")
        try:
            urls = pipeline.run_weekly_bundle()
            logger.info(f"Weekly bundle: {len(urls)} parts uploaded/scheduled.")
        except Exception as e:
            logger.error(f"Weekly bundle job failed: {e}")

    # Scrape immediately, then on schedule
    scrape_job()
    schedule.every(scrape_interval).minutes.do(scrape_job)

    # Drip-publish individual Shorts from queue
    if queue.queue_depth() > 0:
        pipeline.run_drip()
    schedule.every(publish_interval).minutes.do(publish_job)

    # Trending topic video — fire immediately then on interval
    # This guarantees at least one trending video per interval regardless of queue state.
    trending_video_job()
    schedule.every(trending_interval).minutes.do(trending_video_job)
    logger.info(f"Trending topic video scheduled every {trending_interval} min.")

    # Daily bundle at IST peak times
    for time_str in daily_times_ist:
        schedule.every().day.at(time_str).do(daily_bundle_job)
        logger.info(f"Daily bundle scheduled at {time_str} IST.")

    # Weekly bundle (Sunday)
    schedule.every().sunday.at(weekly_time_ist).do(weekly_bundle_job)
    logger.info(f"Weekly bundle scheduled every Sunday at {weekly_time_ist} IST.")

    logger.info(
        f"Drip scheduler started — "
        f"scrape every {scrape_interval} min, "
        f"individual Shorts every {publish_interval} min, "
        f"trending topic video every {trending_interval} min, "
        f"daily bundles at {daily_times_ist}, "
        f"weekly bundle Sundays at {weekly_time_ist}. "
        f"Queue depth: {queue.queue_depth()}"
    )

    while True:
        schedule.run_pending()
        time.sleep(15)


# ── Legacy scheduler (unchanged) ──────────────────────────────────────────────

def run_scheduler():
    import schedule
    interval = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))
    pipeline = NewsPipeline()

    def job():
        logger.info("Scheduler triggered — running pipeline...")
        pipeline.run()

    schedule.every(interval).minutes.do(job)
    logger.info(f"Scheduler started — running every {interval} minutes.")
    job()
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="YouTube News Bot Pipeline (Shorts-only)")
    parser.add_argument("--dry-run",        action="store_true", help="Skip upload to YouTube")
    parser.add_argument("--schedule",       action="store_true", help="Legacy: run on fixed interval")
    parser.add_argument("--drip",           action="store_true", help="Drip scheduler (recommended)")
    parser.add_argument("--enqueue",        action="store_true", help="Scrape and enqueue only")
    parser.add_argument("--once",           action="store_true", help="Run once and exit")
    parser.add_argument("--bundle-daily",   action="store_true", help="Generate + schedule daily Top-30 bundle")
    parser.add_argument("--bundle-weekly",  action="store_true", help="Generate + schedule weekly Top-100 bundle")
    parser.add_argument("--trending-video", action="store_true", help="Generate one Short for the top trending topic")
    args = parser.parse_args()

    if args.drip:
        run_drip_scheduler()
    elif args.schedule:
        run_scheduler()
    elif args.enqueue:
        p = NewsPipeline()
        p.scrape_and_enqueue()
    elif args.bundle_daily:
        p = NewsPipeline()
        urls = p.run_daily_bundle(dry_run=args.dry_run)
        console.print(f"Daily bundle: {len(urls)} parts processed.")
    elif args.bundle_weekly:
        p = NewsPipeline()
        urls = p.run_weekly_bundle(dry_run=args.dry_run)
        console.print(f"Weekly bundle: {len(urls)} parts processed.")
    elif args.trending_video:
        p = NewsPipeline()
        result = p.run_trending_topic_video(dry_run=args.dry_run)
        if result:
            console.print(f"Trending video: {result.status} — {result.shorts_url or result.error}")
        else:
            console.print("[yellow]Trending video: nothing generated.[/]")
    else:
        p = NewsPipeline()
        p.run(dry_run=args.dry_run)


# ── Minimal VideoContent-like object for bundle uploads ───────────────────────

class _BundleVideoContent:
    """Minimal stand-in for VideoContent used by bundle upload calls."""
    def __init__(self, title: str, description: str, tags: list, category: str):
        self.title       = title
        self.description = description
        self.tags        = tags
        self.category    = category
        self.source_url  = ""
        self.source_name = ""
        self.short_script = ""
        self.script      = ""
        self.script_segments = []
        self.thumbnail_headline = ""
        self.thumbnail_subtext  = ""
        self.estimated_duration = 55
        self.article_id = ""
