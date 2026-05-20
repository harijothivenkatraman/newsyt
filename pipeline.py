"""
pipeline.py
Main orchestrator — runs the full scrape → generate → compose → upload pipeline.
Can be run once, on a schedule, or triggered via the dashboard.
"""

import os
import sys
import json
import time
from datetime import datetime
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
from video.video_composer import VideoComposer
from video.shorts_composer import ShortsComposer
from uploader.youtube_uploader import YouTubeUploader

console = Console()


@dataclass
class PipelineResult:
    article_id: str
    title: str
    source: str
    status: str          # success | failed | skipped
    video_path: str = ""
    short_path: str = ""
    thumbnail_path: str = ""
    youtube_url: str = ""
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
        self.video_dir        = os.getenv("VIDEO_DIR",     "./output/videos")
        self.shorts_dir       = os.getenv("SHORTS_DIR",    "./output/shorts")
        self.thumbnail_dir    = os.getenv("THUMBNAIL_DIR", "./output/thumbnails")
        self.tts_engine_name  = os.getenv("TTS_ENGINE",    "kokoro")
        self.tts_language     = os.getenv("VOICE_LANGUAGE","en")
        self.max_articles     = int(os.getenv("MAX_ARTICLES_PER_RUN", "5"))
        self.privacy          = os.getenv("DEFAULT_PRIVACY", "public")
        self.channel_name     = os.getenv("CHANNEL_NAME", "News Channel")
        self.upload_shorts    = os.getenv("UPLOAD_SHORTS", "true").lower() == "true"

        # Component instantiation
        self.aggregator     = NewsAggregator()
        self.ai_gen         = get_generator()   # LocalMLGenerator by default (USE_LOCAL_ML=true)
        self.tts            = TTSEngine(self.tts_engine_name, self.tts_language, self.output_dir)
        self.thumbnailer    = ThumbnailGenerator(self.thumbnail_dir)
        self.composer       = VideoComposer(self.video_dir)
        self.shorts_composer = ShortsComposer(self.shorts_dir)
        self.uploader       = YouTubeUploader(channel_name=self.channel_name)

        self.results: list[PipelineResult] = []
        self._log_path = Path("./logs/pipeline_log.jsonl")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self, dry_run: bool = False) -> list[PipelineResult]:
        run_start = time.time()
        console.rule("[bold red]YouTube News Bot — Pipeline Starting")

        # 1. Scrape
        console.print("\n[cyan]Step 1/5:[/] Scraping news sources...")
        articles = self.aggregator.fetch_all(max_per_source=3)

        if not articles:
            console.print("[yellow]No new articles found. Exiting.")
            return []

        articles = articles[: self.max_articles]
        console.print(f"[green]Found {len(articles)} new articles to process.[/]")

        results = []
        for i, article in enumerate(articles, 1):
            console.rule(f"[dim]Article {i}/{len(articles)}")
            result = self._process_article(article, dry_run=dry_run)
            results.append(result)
            self._save_result(result)

        self.results = results
        self._print_summary(results, time.time() - run_start)
        return results

    def _process_article(self, article, dry_run: bool) -> PipelineResult:
        start = time.time()
        result = PipelineResult(
            article_id=article.id,
            title=article.title,
            source=article.source,
            status="failed",
        )

        temp_files = []

        try:
            # 2. AI Content Generation
            console.print(f"[cyan]  -> Generating AI content...")
            vc = self.ai_gen.generate(article)
            if not vc:
                result.error = "AI generation returned None"
                return result

            # Attach image URL for thumbnail
            vc._image_url = getattr(article, "image_url", "")

            console.print(f"  [green]OK[/] Title: {vc.title[:70]}")

            # 3. Text-to-Speech
            console.print(f"[cyan]  -> Synthesizing speech...")
            audio_path = self.tts.synthesize(vc.script, f"{article.id}_narration.mp3")
            if audio_path:
                temp_files.append(audio_path)
            console.print(f"  [green]OK[/] Audio: {audio_path}")

            # 4. Thumbnail
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

            # 5. Video
            console.print(f"[cyan]  -> Composing video...")
            video_path = self.composer.compose(audio_path, thumb_path, vc, article.id)
            result.video_path = video_path
            if video_path:
                temp_files.append(video_path)
            console.print(f"  [green]OK[/] Video: {video_path}")

            # 5b. Shorts
            if self.upload_shorts:
                console.print(f"[cyan]  -> Composing Short...")
                short_path = self.shorts_composer.compose(audio_path, vc, article.id)
                result.short_path = short_path
                if short_path:
                    temp_files.append(short_path)
                    console.print(f"  [green]OK[/] Short: {short_path}")
                else:
                    console.print(f"  [yellow]Short composition skipped.[/]")

            if dry_run:
                console.print("  [yellow]DRY RUN — skipping upload.[/]")
                result.status = "success"
                result.youtube_url = "DRY_RUN"
                result.shorts_url  = "DRY_RUN"
            else:
                # 6. Upload regular video
                console.print(f"[cyan]  -> Uploading to YouTube...")
                upload = self.uploader.upload(video_path, vc, thumb_path, self.privacy)
                if upload.success:
                    result.youtube_url = upload.video_url
                    result.status = "success"
                    console.print(f"  [green]OK Uploaded:[/] {upload.video_url}")
                else:
                    result.error = upload.error
                    result.status = "failed"

                # 6b. Upload Short
                if self.upload_shorts and result.short_path:
                    console.print(f"[cyan]  -> Uploading Short...")
                    short_upload = self.uploader.upload_short(result.short_path, vc, self.privacy)
                    if short_upload.success:
                        result.shorts_url = short_upload.video_url
                        console.print(f"  [green]OK Short:[/] {short_upload.video_url}")
                    else:
                        console.print(f"  [yellow]Short upload failed: {short_upload.error}[/]")

        except Exception as e:
            result.error = str(e)
            result.status = "failed"
            logger.exception(f"Pipeline failed for {article.id}")
        finally:
            # Clean up all local files generated for this article
            for fp in temp_files:
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                        logger.debug(f"Cleaned up local file: {fp}")
                    except Exception as ex:
                        logger.warning(f"Could not remove local file {fp}: {ex}")

        result.duration_s = round(time.time() - start, 1)
        return result

    # ── Logging ───────────────────────────────────────────────────────────────

    def _save_result(self, result: PipelineResult):
        with open(self._log_path, "a") as f:
            f.write(json.dumps(result.to_dict()) + "\n")

    def _print_summary(self, results: list, elapsed: float):
        console.rule("[bold]Pipeline Summary")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Source",   width=18)
        table.add_column("Title",    width=40)
        table.add_column("Status",   width=10)
        table.add_column("Video URL", width=30)
        table.add_column("Shorts URL", width=30)

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
                r.youtube_url or "—",
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


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduler():
    import schedule
    interval = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "30"))
    pipeline = NewsPipeline()

    def job():
        logger.info("Scheduler triggered — running pipeline...")
        pipeline.run()

    schedule.every(interval).minutes.do(job)
    logger.info(f"Scheduler started — running every {interval} minutes.")
    job()   # Run immediately on start
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="YouTube News Bot Pipeline")
    parser.add_argument("--dry-run",   action="store_true", help="Skip upload to YouTube")
    parser.add_argument("--schedule",  action="store_true", help="Run on a schedule")
    parser.add_argument("--once",      action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.schedule:
        run_scheduler()
    else:
        p = NewsPipeline()
        p.run(dry_run=args.dry_run)
