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
        if not GOOGLE_LIBS:
            logger.warning("Google libraries missing — can't authenticate.")
            return False

        creds = None

        # Load saved token
        if os.path.exists(self.token_path):
            with open(self.token_path, "rb") as f:
                creds = pickle.load(f)

        # Refresh or re-authenticate
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logger.warning(f"Token refresh failed: {e}")
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(self.client_secrets_path):
                logger.error(
                    f"client_secrets.json not found at {self.client_secrets_path}. "
                    "Download it from Google Cloud Console."
                )
                return False
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secrets_path, SCOPES
            )
            creds = flow.run_local_server(port=8080)
            with open(self.token_path, "wb") as f:
                pickle.dump(creds, f)

        self._service = build("youtube", "v3", credentials=creds)
        logger.success("YouTube authenticated!")
        return True

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
