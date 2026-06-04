"""
Render.com startup script for the dashboard.
Reads PORT from environment (Render sets this automatically).
Also ensures tmp output/log dirs exist.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Ensure tmp directories exist (Render's filesystem is ephemeral)
Path(os.getenv("OUTPUT_DIR", "/tmp/output")).mkdir(parents=True, exist_ok=True)
Path(os.getenv("LOG_DIR", "/tmp/logs")).mkdir(parents=True, exist_ok=True)

from dashboard.app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
