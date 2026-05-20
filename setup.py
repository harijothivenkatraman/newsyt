#!/usr/bin/env python3
"""
setup.py
One-time setup: installs dependencies, creates directories, validates config.
Run: python setup.py
"""

import os
import sys
import subprocess
from pathlib import Path

BANNER = """
╔══════════════════════════════════════════════════════╗
║        YouTube News Bot — Setup Wizard              ║
║  Automates Indian news → YouTube video pipeline     ║
╚══════════════════════════════════════════════════════╝
"""

def run(cmd: str):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [WARN] {result.stderr.strip()[:200]}")
    return result.returncode == 0

def main():
    print(BANNER)

    # 1. Create directories
    print("▸ Creating project directories...")
    dirs = [
        "output/videos", "output/thumbnails", "output/audio",
        "logs", "scraper", "content", "video", "uploader", "dashboard"
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    print("  ✓ Directories created\n")

    # 2. Install dependencies
    print("▸ Installing Python dependencies (this may take a few minutes)...")
    run(f"{sys.executable} -m pip install --upgrade pip --quiet")

    packages = [
        "requests beautifulsoup4 lxml feedparser",
        "anthropic python-dotenv rich loguru",
        "Pillow",
        "gtts",
        "google-api-python-client google-auth google-auth-oauthlib",
        "flask schedule pyyaml httpx",
        "moviepy imageio imageio-ffmpeg",
    ]
    for pkg_group in packages:
        ok = run(f"{sys.executable} -m pip install {pkg_group} --quiet")
        status = "✓" if ok else "⚠"
        print(f"  {status} {pkg_group.split()[0]}...")

    print("\n  ✓ Dependencies installed\n")

    # 3. .env setup
    env_example = Path(".env.example")
    env_file = Path(".env")
    if not env_file.exists() and env_example.exists():
        import shutil
        shutil.copy(env_example, env_file)
        print("▸ Created .env from template")
        print("  ⚠  Please edit .env and add your API keys!\n")
    else:
        print("▸ .env already exists — skipping\n")

    # 4. Check ANTHROPIC_API_KEY
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠  ANTHROPIC_API_KEY not set in .env")
        print("   Get one at: https://console.anthropic.com/\n")
    else:
        print("✓  ANTHROPIC_API_KEY is set\n")

    # 5. YouTube OAuth instructions
    print("▸ YouTube Setup Instructions:")
    print("  1. Go to https://console.cloud.google.com/")
    print("  2. Create a project → APIs & Services → Enable 'YouTube Data API v3'")
    print("  3. Create OAuth 2.0 credentials (Desktop App)")
    print("  4. Download JSON → rename to 'client_secrets.json' in project root")
    print("  5. Run `python pipeline.py --dry-run` first to authenticate\n")

    print("═" * 54)
    print("✅  Setup complete!")
    print()
    print("  Quick start commands:")
    print("    python pipeline.py --dry-run    # Test without uploading")
    print("    python pipeline.py              # Run once & upload")
    print("    python pipeline.py --schedule   # Run on schedule")
    print("    python dashboard/app.py         # Start web dashboard")
    print()


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    main()
