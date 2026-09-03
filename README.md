<div align="center">

# 📺 YouTube News Bot

**Fully automated news-to-YouTube pipeline — from headline to published video in minutes.**

Scrapes top Indian news sources, generates professional video scripts with local AI, synthesizes narration, composes broadcast-quality Shorts, and publishes to YouTube — all on autopilot.

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![YouTube Data API](https://img.shields.io/badge/YouTube-Data%20API%20v3-FF0000?logo=youtube&logoColor=white)](https://developers.google.com/youtube/v3)
[![Render Deploy](https://img.shields.io/badge/Render-Deploy-46E3B7?logo=render&logoColor=white)](https://render.com)
[![HuggingFace](https://img.shields.io/badge/🤗-Flan--T5-FFD21E)](https://huggingface.co/google/flan-t5-large)

<br />

[**Quick Start**](#-quick-start) · [**Features**](#-features) · [**Architecture**](#-architecture) · [**Configuration**](#%EF%B8%8F-configuration) · [**Deployment**](#-deployment) · [**Contributing**](#-contributing)

</div>

<br />

---

## ✨ Features

| Category | Details |
|:---------|:--------|
| 🗞️ **Multi-Source Scraping** | Aggregates from The Hindu, India Today, NDTV, Times of India, Hindustan Times, The Wire, and more via RSS + HTTP scraping |
| 🤖 **AI Script Generation** | Local Flan-T5 models (fully offline) or optional Claude API — generates anchor-style narration scripts, SEO titles, descriptions, and tags |
| 🎙️ **Multi-Engine TTS** | Choose from **Piper** (high-quality offline), **Kokoro** (neural ONNX), **gTTS** (Google), or **pyttsx3** (system voices) |
| 🎬 **Video Composition** | Animated Shorts with intro cards, lower-third tickers, background music, and professional outro — powered by MoviePy |
| 🖼️ **Thumbnail Generation** | Breaking-news-style thumbnails auto-generated with Pillow |
| 📤 **YouTube Auto-Upload** | OAuth 2.0 authentication, metadata setting, thumbnail upload, category & privacy controls |
| 📊 **Web Dashboard** | Flask-based monitoring UI — trigger runs, review queued articles, manage config, and switch YouTube channels |
| 📈 **Trending Topics** | Detects trending topics from YouTube Trending Charts, Google Trends, and news APIs to generate timely Shorts |
| 🗓️ **Smart Scheduling** | IST peak-time scheduling, drip publishing, daily bundles (top 30 articles), and weekly compilations (top 100) |
| 🏆 **Article Ranking** | Scoring engine (recency + source tier + keyword relevance) to surface the most impactful stories |
| ☁️ **Cloud Ready** | One-click deploy to Render with included `render.yaml` |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     YouTube News Bot Pipeline                   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  ┌─────────────┐    ┌─────────────────┐    ┌──────────────┐
  │   Scraper   │    │  Trending       │    │  API Sources │
  │  (RSS+HTTP) │    │  Topics Engine  │    │  (Currents,  │
  │  6+ sources │    │  (YT, Google)   │    │   NewsAPI)   │
  └──────┬──────┘    └───────┬─────────┘    └──────┬───────┘
         │                   │                     │
         └───────────────────┼─────────────────────┘
                             ▼
                   ┌──────────────────┐
                   │  Article Queue   │
                   │  + Ranker        │
                   │  (score 0-100)   │
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │  AI Generator    │
                   │  (Flan-T5 local  │
                   │   or Claude API) │
                   │  → Script        │
                   │  → Title & Tags  │
                   │  → Description   │
                   └────────┬─────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼                           ▼
     ┌──────────────┐          ┌───────────────────┐
     │  TTS Engine  │          │  Thumbnail Gen    │
     │  (Piper /    │          │  (Pillow)         │
     │   Kokoro /   │          │  1280×720 PNG     │
     │   gTTS)      │          └─────────┬─────────┘
     └──────┬───────┘                    │
            └─────────────┬──────────────┘
                          ▼
                ┌──────────────────┐
                │  Shorts Composer │
                │  (MoviePy)       │
                │  Animated intro  │
                │  Lower thirds    │
                │  BGM + Outro     │
                │  → MP4 1080p     │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │  YouTube Upload  │
                │  (Data API v3)   │
                │  OAuth 2.0       │
                │  → Published ✅  │
                └──────────────────┘
```

---

## 📁 Project Structure

```
youtube-news-bot/
│
├── pipeline.py                  # Main orchestrator — Shorts pipeline
├── setup.py                     # Interactive setup wizard
├── setup_models.py              # One-time ML model downloader
├── server.py                    # Render.com entry point
├── render.yaml                  # Cloud deployment config
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment config template
│
├── scraper/
│   └── news_scraper.py          # Multi-source news aggregator (RSS + HTTP)
│
├── sources/
│   └── api_sources.py           # External news API integrations
│
├── content/
│   ├── ai_generator.py          # AI content generator interface
│   └── local_ml_generator.py    # Flan-T5 local ML implementation
│
├── video/
│   ├── tts_engine.py            # Text-to-Speech (Piper / Kokoro / gTTS / pyttsx3)
│   ├── thumbnail_generator.py   # Breaking-news-style thumbnail creator
│   ├── shorts_composer.py       # Single-article Short video composer
│   ├── bundle_shorts_composer.py# Multi-article bundle composer
│   ├── video_composer.py        # Core video assembly engine
│   └── animation_engine.py      # Motion graphics & transitions
│
├── uploader/
│   └── youtube_uploader.py      # YouTube Data API v3 uploader + OAuth
│
├── scheduler/
│   └── peak_times.py            # IST peak-time scheduling logic
│
├── dashboard/
│   └── app.py                   # Flask monitoring web dashboard
│
├── assets/                      # Static assets (BGM, logos)
├── article_queue.py             # Persistent article queue manager
├── article_ranker.py            # Article scoring & ranking engine
├── trending_topics.py           # Multi-signal trending topic detector
└── trending_filter.py           # Trending content filter & matcher
```

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **FFmpeg** — [Download](https://ffmpeg.org/download.html) and add to PATH
- **YouTube Data API v3** credentials — [Google Cloud Console](https://console.cloud.google.com/)

### 1. Clone & Install

```bash
git clone https://github.com/yourusername/youtube-news-bot.git
cd youtube-news-bot

# Run the interactive setup wizard
python setup.py
```

### 2. Download ML Models (one-time, ~1.3 GB)

```bash
# Install CPU-only PyTorch first (smaller download)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Download Flan-T5 + Kokoro models
python setup_models.py
```

> [!TIP]
> After downloading, the bot runs **fully offline** — no API keys required for content generation.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
# Local ML (free, offline — recommended)
USE_LOCAL_ML=true

# YouTube channel
CHANNEL_NAME=YourChannelName

# TTS engine: piper (best quality), kokoro, gtts, pyttsx3
TTS_ENGINE=piper
```

### 4. Set Up YouTube OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **YouTube Data API v3**
3. **Credentials** → Create **OAuth 2.0 Client ID** (Desktop App)
4. Download JSON → rename to `client_secrets.json` in project root
5. First run will open a browser for authorization

### 5. Run

```bash
# Dry run — test everything without uploading
python pipeline.py --dry-run

# Single run — scrape → generate → compose → upload
python pipeline.py

# Scheduled mode — continuous operation
python pipeline.py --schedule

# Web dashboard — monitor & control
python dashboard/app.py
# Open http://localhost:5050
```

---

## ⚙️ Configuration

All settings are managed via the `.env` file. See [`.env.example`](.env.example) for the full reference.

### Core Settings

| Variable | Default | Description |
|:---------|:--------|:------------|
| `USE_LOCAL_ML` | `true` | `true` = Flan-T5 (free, offline) · `false` = Claude API |
| `ML_MODEL_SCRIPT` | `google/flan-t5-base` | HuggingFace model for script generation |
| `CHANNEL_NAME` | — | Your YouTube channel name |
| `TTS_ENGINE` | `piper` | `piper` · `kokoro` · `gtts` · `pyttsx3` |
| `VIDEO_RESOLUTION` | `1920x1080` | Output resolution (`1920x1080` or `1280x720`) |

### Scheduling

| Variable | Default | Description |
|:---------|:--------|:------------|
| `SCRAPE_INTERVAL_MINUTES` | `30` | How often to check for new articles |
| `PUBLISH_INTERVAL_MINUTES` | `15` | Drip-publish interval |
| `TRENDING_VIDEO_INTERVAL_MINUTES` | `60` | Trending Shorts generation interval |
| `MAX_ARTICLES_PER_RUN` | `5` | Max articles processed per scrape cycle |

### News Sources

| Variable | Default | Description |
|:---------|:--------|:------------|
| `ENABLE_THE_HINDU` | `true` | Toggle The Hindu |
| `ENABLE_INDIA_TODAY` | `true` | Toggle India Today |
| `ENABLE_NDTV` | `true` | Toggle NDTV |
| `ENABLE_TIMES_OF_INDIA` | `true` | Toggle Times of India |
| `ENABLE_HINDUSTAN_TIMES` | `true` | Toggle Hindustan Times |
| `ENABLE_THE_WIRE` | `true` | Toggle The Wire |

### Content Filters

| Variable | Default | Description |
|:---------|:--------|:------------|
| `MIN_ARTICLE_LENGTH` | `200` | Minimum article length (chars) to process |
| `BLOCKED_KEYWORDS` | `advertisement,sponsored,promo` | Comma-separated blocklist |
| `PREFERRED_CATEGORIES` | `politics,technology,business,sports,science` | Priority categories |
| `DEFAULT_PRIVACY` | `public` | YouTube privacy: `public` · `unlisted` · `private` |

---

## 📰 Supported News Sources

| Source | Method | Categories |
|:-------|:-------|:-----------|
| **The Hindu** | RSS + Scrape | National, Business, Technology, Sports, International |
| **India Today** | RSS + Scrape | India, World, Business, Technology, Sports |
| **NDTV** | RSS + Scrape | India, World, Business, Sports, Technology |
| **Times of India** | RSS + Scrape | Top Stories, India, World, Business, Tech |
| **Hindustan Times** | RSS + Scrape | India, World, Business, Technology |
| **The Wire** | RSS + Scrape | Politics, Economy, Science, Society |

---

## 🎬 What Gets Generated

Each article is transformed into a complete YouTube Shorts package:

| Component | Details |
|:----------|:--------|
| 📝 **Script** | 25–35 second anchor-style narration |
| 🎙️ **Audio** | High-quality TTS with configurable engine |
| 🖼️ **Thumbnail** | 1280×720 breaking-news-styled PNG |
| 🎬 **Video** | MP4 with animated intro, content overlay, lower-third ticker, BGM, and outro |
| 📋 **Title** | SEO-optimized, ≤ 100 characters |
| 📄 **Description** | Bullet points, timestamps, hashtags, source attribution |
| 🏷️ **Tags** | 15–25 auto-generated tags for discoverability |

**Bundle modes** also available:
- 🗓️ **Daily Bundle** — Top 30 articles → 3 × 60-second compilation Shorts
- 📅 **Weekly Bundle** — Top 100 articles → 10 × 60-second compilation Shorts

---

## ☁️ Deployment

### Render (recommended)

This project includes a [`render.yaml`](render.yaml) for one-click deployment:

1. Push your repo to GitHub
2. Connect to [Render](https://render.com)
3. Set environment variables in the Render dashboard (`CHANNEL_NAME`, API keys, etc.)
4. Deploy — the bot runs as a web service with the dashboard

> [!NOTE]
> On Render's free tier, use `USE_LOCAL_ML=false` with a cloud AI API, as the free plan has limited RAM for ML models.

### Self-Hosted

```bash
# Run with scheduler (recommended for production)
python pipeline.py --schedule

# Or use the server entry point
python server.py
```

---

## 🔧 Troubleshooting

| Problem | Solution |
|:--------|:---------|
| `ModuleNotFoundError` | Run `python setup.py` or `pip install -r requirements.txt` |
| `client_secrets.json not found` | Download OAuth credentials from [Google Cloud Console](https://console.cloud.google.com/) |
| Video has no background image | Normal — gradient background is used when article image can't be fetched |
| Upload quota exceeded | YouTube API free tier allows ~6 uploads/day. [Request higher quota](https://support.google.com/youtube/contact/yt_api_form) for production |
| gTTS rate limit errors | Switch to `TTS_ENGINE=piper` or `TTS_ENGINE=pyttsx3` in `.env` |
| High RAM usage | Use `google/flan-t5-base` instead of `flan-t5-large` (needs < 1 GB RAM) |
| `torch` installation issues | Install CPU-only build: `pip install torch --index-url https://download.pytorch.org/whl/cpu` |

---

## 🛡️ Legal & Ethical Notes

- Uses **publicly available RSS feeds** and scrapes **publicly visible articles**
- Source attribution is **automatically included** in every video description
- Set `DEFAULT_PRIVACY=unlisted` to review content before publishing
- Respect each outlet's **robots.txt** and **Terms of Service**
- Intended for **commentary, news aggregation, and transformative content**
- For commercial use, consult legal counsel regarding fair use / fair dealing

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

Please make sure to update tests and documentation as appropriate.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built with ❤️ for automated journalism**

[⬆ Back to top](#-youtube-news-bot)

</div>
