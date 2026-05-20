# 📺 YouTube News Bot

> **Fully automated Indian news channel pipeline** — scrapes The Hindu, India Today, NDTV, Times of India & Hindustan Times, generates professional video scripts with AI, synthesizes narration, composes videos with thumbnails, and publishes to YouTube automatically.

---

## 🏗️ Architecture

```
News Websites (RSS + Scraper)
        │
        ▼
  NewsAggregator          ← scraper/news_scraper.py
  (5 Indian sources)
        │
        ▼
  AIContentGenerator      ← content/ai_generator.py
  (Claude Sonnet)
  → YouTube title
  → Narration script
  → Description + tags
  → Thumbnail text
        │
        ├──────────────────────────────┐
        ▼                              ▼
   TTSEngine                  ThumbnailGenerator
   (Google TTS / pyttsx3)     (Pillow — breaking news style)
   → MP3 audio                → 1280×720 PNG
        │                              │
        └──────────────┬───────────────┘
                       ▼
               VideoComposer           ← video/video_composer.py
               (MoviePy)
               → Intro card
               → Main content + lower thirds
               → Outro / subscribe card
               → MP4 1080p
                       │
                       ▼
              YouTubeUploader          ← uploader/youtube_uploader.py
              (YouTube Data API v3)
              → Upload MP4
              → Set thumbnail
              → Add title/description/tags
              → Set category & privacy
                       │
                       ▼
              ✅ Live on YouTube
```

---

## 🚀 Quick Start

### 1. Clone & Setup

```bash
# Navigate to project
cd youtube-news-bot

# Run setup wizard
python setup.py
```

### 2. Configure API Keys

Edit `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...       # https://console.anthropic.com/
CHANNEL_NAME=IndiaNewsToday
MAX_ARTICLES_PER_RUN=3
DEFAULT_PRIVACY=public
TTS_ENGINE=gtts                     # gtts (requires internet) or pyttsx3 (offline)
```

### 3. YouTube OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. **New Project** → Enable **YouTube Data API v3**
3. **APIs & Services → Credentials → Create OAuth 2.0 Client ID**
4. Application type: **Desktop App**
5. Download JSON → rename to **`client_secrets.json`** in project root
6. First run opens browser for authorization

### 4. Run the Pipeline

```bash
# Test everything (no YouTube upload)
python pipeline.py --dry-run

# Run once (scrape → generate → compose → upload)
python pipeline.py

# Run on schedule (every 30 min by default)
python pipeline.py --schedule

# Launch web dashboard
python dashboard/app.py
# Open http://localhost:5050
```

---

## 📁 Project Structure

```
youtube-news-bot/
├── pipeline.py                  # Main orchestrator
├── setup.py                     # Setup wizard
├── requirements.txt
├── .env.example                 # Config template
├── client_secrets.json          # YouTube OAuth (you create this)
│
├── scraper/
│   └── news_scraper.py          # The Hindu, India Today, NDTV, TOI, HT
│
├── content/
│   └── ai_generator.py          # Claude-powered script/title/tags generator
│
├── video/
│   ├── tts_engine.py            # Text-to-Speech (gTTS / pyttsx3)
│   ├── thumbnail_generator.py   # Professional news thumbnails (Pillow)
│   └── video_composer.py        # MoviePy video assembly
│
├── uploader/
│   └── youtube_uploader.py      # YouTube Data API v3 upload
│
├── dashboard/
│   └── app.py                   # Flask monitoring dashboard
│
├── output/
│   ├── videos/                  # Generated MP4 files
│   ├── thumbnails/              # Generated PNG thumbnails
│   └── audio/                  # TTS audio segments
│
└── logs/
    ├── pipeline_log.jsonl       # Per-article run log
    └── seen_ids.json            # Prevents re-processing articles
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Claude API key |
| `MAX_ARTICLES_PER_RUN` | 5 | Articles to process per run |
| `SCRAPE_INTERVAL_MINUTES` | 30 | Schedule interval |
| `TTS_ENGINE` | `gtts` | `gtts` or `pyttsx3` |
| `VOICE_LANGUAGE` | `en` | Language code for TTS |
| `DEFAULT_PRIVACY` | `public` | `public` / `unlisted` / `private` |
| `VIDEO_DIR` | `./output/videos` | Where MP4s are saved |
| `ENABLE_THE_HINDU` | `true` | Toggle per source |
| `ENABLE_INDIA_TODAY` | `true` | Toggle per source |
| `ENABLE_NDTV` | `true` | Toggle per source |

---

## 📰 Supported News Sources

| Source | Type | Categories |
|--------|------|------------|
| **The Hindu** | RSS + Scrape | National, Business, Tech, Sports, International |
| **India Today** | RSS + Scrape | India, World, Business, Technology, Sports |
| **NDTV** | RSS + Scrape | India, World, Business, Sports, Tech |
| **Times of India** | RSS + Scrape | Top, India, World, Business, Tech |
| **Hindustan Times** | RSS + Scrape | India, World, Business, Technology |

---

## 🎬 What Gets Generated

Each article → complete YouTube video package:

- **📝 Script** — 300–500 word professional anchor narration
- **🎙️ Audio** — MP3 via Google TTS or pyttsx3
- **🖼️ Thumbnail** — 1280×720 breaking-news styled PNG
- **🎬 Video** — MP4 with intro card, content, lower-thirds ticker, outro
- **📋 Title** — SEO-optimized ≤ 100 chars
- **📄 Description** — Full description with bullet points, timestamps, hashtags
- **🏷️ Tags** — 15–25 relevant tags for discoverability

---

## 🛡️ Legal & Ethical Notes

- The bot uses **publicly available RSS feeds** and scrapes **publicly visible news articles**
- Always **attribute the source** in video descriptions (this is done automatically)
- Enable `DEFAULT_PRIVACY=unlisted` to review before publishing
- Consider the news outlet's **robots.txt** and **Terms of Service**
- This tool is intended for **commentary, news aggregation, and transformative content** use cases
- For commercial use, consult legal counsel regarding fair use/fair dealing

---

## 🔧 Troubleshooting

**`ModuleNotFoundError`** — Run `python setup.py` or `pip install -r requirements.txt`

**`client_secrets.json not found`** — Download OAuth credentials from Google Cloud Console

**`ANTHROPIC_API_KEY not set`** — Add to `.env` file

**Video has no image** — Normal if the article image couldn't be fetched; gradient background is used

**Upload quota exceeded** — YouTube API has daily quotas. Default quota allows ~6 uploads/day on free tier. [Request higher quota](https://support.google.com/youtube/contact/yt_api_form) for production use.

**gTTS rate limit** — Switch to `TTS_ENGINE=pyttsx3` in `.env` for offline TTS
