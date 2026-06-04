"""
uploader/youtube_uploader.py
Uploads videos to YouTube using the YouTube Data API v3.
Handles OAuth2 authentication, video upload, thumbnail setting,
and playlist management.

Setup:
1. Go to https://console.cloud.google.com/
2. Create a project → Enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop App)
4. Download client_secrets.json → place in project root
"""

import os
import json
import time
import pickle
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import googleapiclient.errors
    GOOGLE_LIBS = True
except ImportError:
    GOOGLE_LIBS = False
    logger.warning("Google API libraries not installed — uploads will be simulated.")


SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

# YouTube category IDs
CATEGORY_MAP = {
    "national":     "25",  # News & Politics
    "india":        "25",
    "politics":     "25",
    "world":        "25",
    "international":"25",
    "business":     "25",
    "technology":   "28",  # Science & Technology
    "tech":         "28",
    "science":      "28",
    "sports":       "17",  # Sports
    "entertainment":"24",  # Entertainment
    "default":      "25",
}


@dataclass
class UploadResult:
    success: bool
    video_id: str = ""
    video_url: str = ""
    error: str = ""
    quota_exceeded: bool = False      # True when HTTP 429 daily quota hit
    metadata: dict = field(default_factory=dict)


class YouTubeUploader:
    TOKEN_FILE     = "youtube_token.pickle"
    SECRETS_FILE   = "client_secrets.json"
    CHUNK_SIZE     = 4 * 1024 * 1024   # 4 MB

    def __init__(
        self,
        client_secrets_path: str = "client_secrets.json",
        token_path: str = "youtube_token.pickle",
        channel_name: str = "News Channel",
    ):
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self.channel_name = channel_name
        self._service = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """Authenticate using a saved token (refresh if expired).
        Does NOT open a browser — safe for headless/server environments.
        For fresh auth (no token), use get_auth_url() + exchange_code().
        """
        if not GOOGLE_LIBS:
            logger.warning("Google libraries missing — can't authenticate.")
            return False

        creds = None

        # Load saved token
        if os.path.exists(self.token_path):
            with open(self.token_path, "rb") as f:
                creds = pickle.load(f)

        # Refresh expired token (no browser needed)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(self.token_path, "wb") as f:
                    pickle.dump(creds, f)
                logger.success("YouTube token refreshed.")
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            logger.error(
                "No valid YouTube token found. Use get_auth_url() to start "
                "the OAuth flow and exchange_code() to complete it."
            )
            return False

        self._service = build("youtube", "v3", credentials=creds)
        logger.success("YouTube authenticated!")
        return True

    def get_auth_url(self, redirect_uri: str) -> str:
        """Return a Google OAuth URL for the user to visit in their browser.
        Call exchange_code() with the returned code to complete login.
        """
        if not GOOGLE_LIBS:
            raise RuntimeError("Google libraries not installed.")
        if not os.path.exists(self.client_secrets_path):
            raise FileNotFoundError(
                f"client_secrets.json not found at {self.client_secrets_path}."
            )
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            self.client_secrets_path,
            scopes=SCOPES,
            redirect_uri=redirect_uri,
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return auth_url

    def exchange_code(self, code: str, redirect_uri: str) -> bool:
        """Exchange an OAuth authorization code for credentials and save the token.
        Returns True on success.
        """
        if not GOOGLE_LIBS:
            return False
        if not os.path.exists(self.client_secrets_path):
            return False
        try:
            from google_auth_oauthlib.flow import Flow
            flow = Flow.from_client_secrets_file(
                self.client_secrets_path,
                scopes=SCOPES,
                redirect_uri=redirect_uri,
            )
            flow.fetch_token(code=code)
            creds = flow.credentials
            with open(self.token_path, "wb") as f:
                pickle.dump(creds, f)
            self._service = build("youtube", "v3", credentials=creds)
            logger.success("YouTube OAuth exchange complete — token saved.")
            return True
        except Exception as e:
            logger.error(f"OAuth code exchange failed: {e}")
            return False

    def get_channel_info(self) -> dict:
        """Return info about the currently authenticated YouTube channel."""
        if not self._service:
            if not self.authenticate():
                return {}
        try:
            resp = self._service.channels().list(
                part="snippet,statistics",
                mine=True,
            ).execute()
            items = resp.get("items", [])
            if not items:
                return {}
            ch = items[0]
            snippet = ch.get("snippet", {})
            stats = ch.get("statistics", {})
            return {
                "channel_id": ch.get("id", ""),
                "title": snippet.get("title", "Unknown"),
                "description": snippet.get("description", "")[:200],
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                "subscriber_count": stats.get("subscriberCount", "0"),
                "video_count": stats.get("videoCount", "0"),
                "view_count": stats.get("viewCount", "0"),
                "custom_url": snippet.get("customUrl", ""),
            }
        except Exception as e:
            logger.error(f"Failed to get channel info: {e}")
            return {}

    def logout(self) -> bool:
        """Remove saved token to log out. Next upload will require re-auth."""
        self._service = None
        if os.path.exists(self.token_path):
            try:
                os.remove(self.token_path)
                logger.success(f"Logged out — removed {self.token_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to remove token: {e}")
                return False
        return True

    def is_authenticated(self) -> bool:
        """Check if a valid token exists without triggering a browser flow."""
        if self._service:
            return True
        if not GOOGLE_LIBS:
            return False
        if not os.path.exists(self.token_path):
            return False
        try:
            with open(self.token_path, "rb") as f:
                creds = pickle.load(f)
            if creds and creds.valid:
                return True
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(self.token_path, "wb") as f:
                    pickle.dump(creds, f)
                return True
        except Exception:
            pass
        return False

    # ── YouTube dedup check ───────────────────────────────────────────────────

    def is_already_uploaded(self, title: str) -> tuple[bool, str]:
        """Check if a video with this title already exists on the channel.
        Returns (found: bool, video_url: str)."""
        if not self._service:
            if not self.authenticate():
                return False, ""
        try:
            resp = self._service.search().list(
                part="snippet",
                forMine=True,
                type="video",
                q=title[:70],
                maxResults=10,
            ).execute()
            for item in resp.get("items", []):
                existing = item["snippet"]["title"].lower().strip()
                if existing == title.lower().strip() or title.lower()[:60] in existing:
                    vid_id = item["id"]["videoId"]
                    return True, f"https://www.youtube.com/watch?v={vid_id}"
        except Exception as e:
            logger.warning(f"YouTube dedup check failed: {e}")
        return False, ""

    def get_top_performing_seo_context(self) -> str:
        """Fetch the top 5 most viewed videos from this channel and extract their SEO patterns."""
        if not self._service:
            if not self.authenticate():
                return ""
                
        try:
            logger.info("Fetching top-performing videos from YouTube API for SEO context...")
            resp = self._service.search().list(
                part="snippet",
                forMine=True,
                type="video",
                order="viewCount",
                maxResults=5,
            ).execute()
            
            items = resp.get("items", [])
            if not items:
                return ""
                
            context_lines = []
            context_lines.append("Here are the actual titles of the top 5 most-viewed videos on this channel:")
            
            video_ids = [item["id"]["videoId"] for item in items]
            vid_resp = self._service.videos().list(
                part="snippet,statistics",
                id=",".join(video_ids)
            ).execute()
            
            all_tags = []
            for v in vid_resp.get("items", []):
                title = v["snippet"]["title"]
                tags = v["snippet"].get("tags", [])
                views = v["statistics"].get("viewCount", "0")
                context_lines.append(f"- \"{title}\" ({views} views)")
                all_tags.extend(tags)
                
            from collections import Counter
            top_tags = [tag for tag, _ in Counter(all_tags).most_common(15)]
            
            if top_tags:
                context_lines.append("\nHere are the most successful tags used across these top videos:")
                context_lines.append(", ".join(top_tags))
                context_lines.append("\nPlease format the newly generated titles and tags to closely mimic these successful patterns.")
                
            return "\n".join(context_lines)
            
        except Exception as e:
            logger.warning(f"Failed to fetch SEO context from YouTube API: {e}")
            return ""

    # ── Upload ────────────────────────────────────────────────────────────────

    def upload(
        self,
        video_path: str,
        video_content,
        thumbnail_path: Optional[str] = None,
        privacy: str = "public",
        cleanup_on_success: bool = True,
    ) -> "UploadResult":
        """Upload a video with full metadata. Deduplicates and cleans up local file on success."""

        if not GOOGLE_LIBS:
            return self._simulate_upload(video_content)

        if not self._service:
            if not self.authenticate():
                return UploadResult(success=False, error="Authentication failed")

        # ── Dedup: check if already on YouTube ──
        already, existing_url = self.is_already_uploaded(video_content.title)
        if already:
            logger.info(f"Already uploaded, skipping: {existing_url}")
            return UploadResult(success=True, video_id="", video_url=existing_url,
                                metadata={"deduplicated": True})

        # Prepare metadata
        category_id = CATEGORY_MAP.get(video_content.category.lower(), "25")
        body = {
            "snippet": {
                "title":       video_content.title[:100],
                "description": video_content.description[:5000],
                "tags":        video_content.tags[:30],
                "categoryId":  category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus":          privacy,
                "selfDeclaredMadeForKids": False,
                "embeddable":              True,
                "publicStatsViewable":     True,
            },
        }

        media = MediaFileUpload(
            video_path,
            chunksize=self.CHUNK_SIZE,
            resumable=True,
            mimetype="video/mp4",
        )

        logger.info(f"Uploading: {video_content.title[:60]}...")
        request = self._service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        # ── Chunked upload with exponential backoff retry ──
        response = None
        retry_count = 0
        max_retries = 5
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"  Upload progress: {pct}%")
                retry_count = 0  # reset on success
            except ConnectionResetError as e:
                retry_count += 1
                if retry_count > max_retries:
                    return UploadResult(success=False, error=f"Upload failed after {max_retries} retries: {e}")
                wait = 2 ** retry_count
                logger.warning(f"  Connection reset. Retrying in {wait}s (attempt {retry_count}/{max_retries})...")
                time.sleep(wait)
            except googleapiclient.errors.HttpError as e:
                error_str = str(e)
                if "429" in error_str or "rateLimitExceeded" in error_str or "Quota exceeded" in error_str:
                    logger.warning(f"YouTube upload quota exceeded (429). Saving to retry queue.")
                    self._save_retry_queue(video_path, video_content, privacy, is_short=False)
                    return UploadResult(success=False, quota_exceeded=True,
                                       error="Quota exceeded — saved to retry queue (resets at midnight PT).")
                logger.error(f"Upload HTTP error: {e}")
                return UploadResult(success=False, error=str(e))

        video_id  = response.get("id", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.success(f"Uploaded! {video_url}")

        # Set thumbnail
        if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
            self._set_thumbnail(video_id, thumbnail_path)

        return UploadResult(
            success=True,
            video_id=video_id,
            video_url=video_url,
            metadata={"title": video_content.title, "category": video_content.category},
        )

    def _set_thumbnail(self, video_id: str, thumbnail_path: str):
        try:
            self._service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/png"),
            ).execute()
            logger.success(f"Thumbnail set for {video_id}")
        except Exception as e:
            logger.warning(f"Could not set thumbnail: {e}")

    def upload_short(
        self,
        video_path: str,
        video_content,
        privacy: str = "public",
    ) -> "UploadResult":
        """Upload a YouTube Short with #Shorts tag and source link in description."""

        if not GOOGLE_LIBS:
            return self._simulate_upload(video_content)

        if not self._service:
            if not self.authenticate():
                return UploadResult(success=False, error="Authentication failed")

        category_id = CATEGORY_MAP.get(video_content.category.lower(), "25")

        # Build Shorts-optimised title (max 100 chars, append #Shorts)
        base_title = video_content.title.strip()
        shorts_title = base_title if len(base_title) <= 90 else base_title[:87] + "..."
        shorts_title = f"{shorts_title} #Shorts"

        # Build description with source link
        source_block = ""
        if getattr(video_content, "source_url", ""):
            source_block += f"\n\n🔗 Source: {video_content.source_url}"
        if getattr(video_content, "source_name", ""):
            source_block += f"\n📰 Credit: {video_content.source_name}"

        shorts_description = (
            f"{video_content.description[:1200]}"
            f"{source_block}"
            "\n\n#Shorts #NewsShorts #BreakingNews #IndiaNews #News"
        )

        # Tags: include Shorts-specific tags
        tags = list(video_content.tags[:25]) + ["Shorts", "News", "BreakingNews", "IndiaNews"]
        tags = list(dict.fromkeys(tags))[:30]  # deduplicate

        body = {
            "snippet": {
                "title":       shorts_title[:100],
                "description": shorts_description[:5000],
                "tags":        tags,
                "categoryId":  category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus":          privacy,
                "selfDeclaredMadeForKids": False,
                "embeddable":              True,
                "publicStatsViewable":     True,
            },
        }

        media = MediaFileUpload(
            video_path,
            chunksize=self.CHUNK_SIZE,
            resumable=True,
            mimetype="video/mp4",
        )

        logger.info(f"Uploading Short: {shorts_title[:60]}...")
        request = self._service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        retry_count = 0
        max_retries = 5
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"  Short upload progress: {pct}%")
                retry_count = 0
            except ConnectionResetError as e:
                retry_count += 1
                if retry_count > max_retries:
                    return UploadResult(success=False, error=f"Short upload failed after retries: {e}")
                wait = 2 ** retry_count
                logger.warning(f"  Short connection reset. Retrying in {wait}s...")
                time.sleep(wait)
            except googleapiclient.errors.HttpError as e:
                error_str = str(e)
                if "429" in error_str or "rateLimitExceeded" in error_str or "Quota exceeded" in error_str:
                    logger.warning(f"YouTube Short quota exceeded (429). Saving to retry queue.")
                    self._save_retry_queue(video_path, video_content, privacy, is_short=True)
                    return UploadResult(success=False, quota_exceeded=True,
                                       error="Quota exceeded — saved to retry queue (resets at midnight PT).")
                logger.error(f"Short upload HTTP error: {e}")
                return UploadResult(success=False, error=str(e))

        video_id  = response.get("id", "")
        video_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.success(f"Short uploaded! {video_url}")

        return UploadResult(
            success=True,
            video_id=video_id,
            video_url=video_url,
            metadata={"title": shorts_title, "category": video_content.category, "type": "short"},
        )

    def upload_short_scheduled(
        self,
        video_path: str,
        video_content,
        publish_at_utc,     # datetime (UTC, timezone-aware) — when to auto-publish
        privacy_until_then: str = "private",
    ) -> "UploadResult":
        """
        Upload a Short as private and schedule it to auto-publish at publish_at_utc.

        The YouTube API requires:
          - status.privacyStatus = "private" (or "unlisted")
          - status.publishAt     = ISO 8601 UTC datetime string

        This is used for daily and weekly bundle Shorts scheduled at peak IST times.
        The channel must be a standard account (not a Brand Account) for scheduling to work.
        """
        if not GOOGLE_LIBS:
            return self._simulate_upload(video_content)

        if not self._service:
            if not self.authenticate():
                return UploadResult(success=False, error="Authentication failed")

        category_id = CATEGORY_MAP.get(
            getattr(video_content, "category", "news").lower(), "25"
        )

        base_title = (getattr(video_content, "title", "News Bundle") or "News Bundle").strip()
        shorts_title = base_title if len(base_title) <= 90 else base_title[:87] + "..."
        shorts_title = f"{shorts_title} #Shorts"

        source_block = ""
        if getattr(video_content, "source_url", ""):
            source_block = f"\n\n🔗 More news: {video_content.source_url}"

        shorts_description = (
            f"{getattr(video_content, 'description', '')[:1200]}"
            f"{source_block}"
            "\n\n#Shorts #NewsRoundup #TopNews #IndiaNews #BreakingNews"
        )

        tags = list(getattr(video_content, "tags", [])[:25]) + [
            "Shorts", "NewsRoundup", "TopNews", "IndiaNews", "BreakingNews",
        ]
        tags = list(dict.fromkeys(tags))[:30]

        # Format publishAt as RFC3339 UTC
        from datetime import timezone as _tz
        if publish_at_utc.tzinfo is None:
            publish_at_utc = publish_at_utc.replace(tzinfo=_tz.utc)
        publish_at_str = publish_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        body = {
            "snippet": {
                "title":       shorts_title[:100],
                "description": shorts_description[:5000],
                "tags":        tags,
                "categoryId":  category_id,
                "defaultLanguage": "en",
                "defaultAudioLanguage": "en",
            },
            "status": {
                "privacyStatus":          privacy_until_then,
                "publishAt":              publish_at_str,
                "selfDeclaredMadeForKids": False,
                "embeddable":              True,
                "publicStatsViewable":     True,
            },
        }

        media = MediaFileUpload(
            video_path,
            chunksize=self.CHUNK_SIZE,
            resumable=True,
            mimetype="video/mp4",
        )

        logger.info(f"Uploading scheduled Short: {shorts_title[:60]} → publish at {publish_at_str}")
        request = self._service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        retry_count = 0
        max_retries = 5
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"  Scheduled Short upload progress: {pct}%")
                retry_count = 0
            except ConnectionResetError as e:
                retry_count += 1
                if retry_count > max_retries:
                    return UploadResult(success=False, error=f"Upload failed after retries: {e}")
                wait = 2 ** retry_count
                logger.warning(f"  Connection reset. Retrying in {wait}s...")
                time.sleep(wait)
            except googleapiclient.errors.HttpError as e:
                error_str = str(e)
                if "429" in error_str or "rateLimitExceeded" in error_str or "Quota exceeded" in error_str:
                    logger.warning("Scheduled Short quota exceeded — saving to retry queue.")
                    self._save_retry_queue(video_path, video_content, privacy_until_then, is_short=True)
                    return UploadResult(
                        success=False, quota_exceeded=True,
                        error="Quota exceeded — saved to retry queue.",
                    )
                logger.error(f"Scheduled Short HTTP error: {e}")
                return UploadResult(success=False, error=str(e))

        video_id  = response.get("id", "")
        video_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.success(f"Scheduled Short uploaded! {video_url} → publishes at {publish_at_str}")

        return UploadResult(
            success=True,
            video_id=video_id,
            video_url=video_url,
            metadata={
                "title": shorts_title,
                "category": getattr(video_content, "category", "news"),
                "type": "scheduled_short",
                "publish_at": publish_at_str,
            },
        )

    @staticmethod
    def get_next_peak_ist(
        peak_times_ist: list[tuple[int, int]] = None,
    ):
        """
        Returns the next upcoming peak time in UTC, based on IST peak hours.

        Args:
            peak_times_ist: list of (hour, minute) tuples in IST (UTC+5:30).
                            Default: [(8, 0), (18, 0)] for 8 AM and 6 PM IST.

        Returns:
            A timezone-aware datetime in UTC representing the next peak slot.
        """
        from datetime import datetime, timezone, timedelta

        IST_OFFSET = timedelta(hours=5, minutes=30)
        now_utc    = datetime.now(timezone.utc)
        now_ist    = now_utc + IST_OFFSET

        if peak_times_ist is None:
            peak_times_ist = [(8, 0), (18, 0)]

        candidates = []
        for h, m in peak_times_ist:
            # Today's slot in IST
            candidate_ist = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate_ist <= now_ist:
                # Slot already passed today — move to tomorrow
                candidate_ist += timedelta(days=1)
            # Convert to UTC
            candidate_utc = candidate_ist - IST_OFFSET
            candidates.append(candidate_utc)

        # Return the earliest upcoming slot
        return min(candidates)

    @staticmethod
    def get_next_weekly_peak_ist(
        weekday: int = 6,          # 6 = Sunday (Mon=0 ... Sun=6)
        hour_ist: int = 10,
        minute_ist: int = 0,
    ):
        """
        Returns the next Sunday (or configured weekday) 10:00 AM IST as UTC datetime.
        """
        from datetime import datetime, timezone, timedelta

        IST_OFFSET = timedelta(hours=5, minutes=30)
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + IST_OFFSET

        days_ahead = (weekday - now_ist.weekday()) % 7
        if days_ahead == 0:
            # Today is the target weekday — check if time has passed
            target_ist = now_ist.replace(hour=hour_ist, minute=minute_ist, second=0, microsecond=0)
            if target_ist <= now_ist:
                days_ahead = 7
            # else days_ahead stays 0

        target_ist = (now_ist + timedelta(days=days_ahead)).replace(
            hour=hour_ist, minute=minute_ist, second=0, microsecond=0
        )
        return target_ist - IST_OFFSET  # UTC


    # ── Retry queue ───────────────────────────────────────────────────────────

    _RETRY_FILE = Path("./logs/upload_retry.jsonl")

    def _save_retry_queue(self, video_path: str, video_content, privacy: str, is_short: bool):
        """
        Persist a failed-due-to-quota upload so it can be retried tomorrow.
        The video file is NOT deleted — it must stay on disk until the retry succeeds.
        """
        import json
        from datetime import datetime
        entry = {
            "queued_at": datetime.now().isoformat(),
            "video_path": str(video_path),
            "is_short": is_short,
            "privacy": privacy,
            "article_id": getattr(video_content, "article_id", ""),
            "title": getattr(video_content, "title", ""),
            "description": getattr(video_content, "description", "")[:2000],
            "tags": getattr(video_content, "tags", [])[:30],
            "category": getattr(video_content, "category", "news"),
            "source_url": getattr(video_content, "source_url", ""),
            "source_name": getattr(video_content, "source_name", ""),
            "status": "pending",
        }
        try:
            self._RETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self._RETRY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info(f"Saved to retry queue: {entry['title'][:60]}")
        except Exception as ex:
            logger.error(f"Failed to save retry queue: {ex}")

    def retry_pending_uploads(self) -> list[UploadResult]:
        """
        Re-attempt all uploads saved to the retry queue.
        Call this at startup or on a daily schedule after midnight PT (when quota resets).
        Returns list of results.
        """
        import json
        if not self._RETRY_FILE.exists():
            return []

        lines = self._RETRY_FILE.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass

        pending = [e for e in entries if e.get("status") == "pending"]
        if not pending:
            logger.info("Retry queue: nothing pending.")
            return []

        logger.info(f"Retry queue: attempting {len(pending)} pending upload(s)...")
        results = []

        for entry in pending:
            video_path = entry.get("video_path", "")
            if not video_path or not os.path.exists(video_path):
                logger.warning(f"Retry: file missing — {video_path}")
                entry["status"] = "missing"
                results.append(UploadResult(success=False, error=f"File missing: {video_path}"))
                continue

            # Reconstruct a minimal VideoContent-like object
            class _VC:
                pass
            vc = _VC()
            vc.article_id   = entry.get("article_id", "")
            vc.title        = entry.get("title", "")
            vc.description  = entry.get("description", "")
            vc.tags         = entry.get("tags", [])
            vc.category     = entry.get("category", "news")
            vc.source_url   = entry.get("source_url", "")
            vc.source_name  = entry.get("source_name", "")
            vc.short_script = ""
            vc.script       = ""
            vc.script_segments = []
            vc.thumbnail_headline = ""
            vc.thumbnail_subtext  = ""
            vc.estimated_duration = 180

            privacy = entry.get("privacy", "public")
            is_short = entry.get("is_short", False)

            if is_short:
                result = self.upload_short(video_path, vc, privacy)
            else:
                result = self.upload(video_path, vc, privacy=privacy)

            if result.success:
                entry["status"] = "done"
                entry["video_url"] = result.video_url
                # Clean up the video file now that it's uploaded
                try:
                    os.remove(video_path)
                except Exception:
                    pass
                logger.success(f"Retry succeeded: {result.video_url}")
            elif result.quota_exceeded:
                logger.warning("Retry: still quota-limited — will try again tomorrow.")
                # Leave status as 'pending'
            else:
                entry["status"] = "failed"
                entry["error"] = result.error
                logger.error(f"Retry failed: {result.error}")

            results.append(result)

        # Rewrite the retry file with updated statuses
        try:
            with open(self._RETRY_FILE, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception as ex:
            logger.error(f"Failed to update retry queue: {ex}")

        return results

    # ── Simulation (no creds) ─────────────────────────────────────────────────

    def _simulate_upload(self, video_content) -> UploadResult:
        fake_id = f"SIMULATED_{video_content.article_id}"
        logger.info(f"[SIMULATE] Would upload: {video_content.title}")
        logger.info(f"[SIMULATE] Tags: {video_content.tags[:5]}...")
        logger.info(f"[SIMULATE] Description length: {len(video_content.description)} chars")
        return UploadResult(
            success=True,
            video_id=fake_id,
            video_url=f"https://youtube.com/watch?v={fake_id}",
            metadata={"simulated": True},
        )
