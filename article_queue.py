"""
article_queue.py
Persistent, minimal-footprint article queue.

Flow
────
  1. Scrape → enqueue_batch()  saves compact stubs to logs/article_queue.jsonl
  2. Every N minutes           pop_next() returns the oldest pending article stub
  3. Pipeline                  reconstructs a NewsArticle from the stub and runs it
  4. On success/fail           mark_done() / mark_failed() updates the stub in-place

Storage format (one JSON object per line):
  {
    "id":           "abc123",          # 12-char MD5 hash of URL
    "title":        "...",             # full title
    "content":      "...",             # trimmed to CONTENT_CAP chars
    "summary":      "...",             # first 300 chars of content
    "url":          "https://...",
    "source":       "The Hindu",
    "author":       "Staff",
    "published_at": "2026-...",
    "category":     "business",
    "image_url":    "https://...",
    "queued_at":    "2026-...",        # when it was added to the queue
    "status":       "pending",         # pending | processing | done | failed
    "processed_at": "",
    "error":        ""
  }
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

# Trim article body to this many chars before storing — keeps queue file small
CONTENT_CAP = 1_500
SUMMARY_CAP  = 300

_QUEUE_FILE = Path("./logs/article_queue.jsonl")
_lock = threading.Lock()          # file-level mutex for thread-safe ops


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_all() -> list[dict]:
    """Read every stub from the queue file."""
    if not _QUEUE_FILE.exists():
        return []
    stubs = []
    for line in _QUEUE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            try:
                stubs.append(json.loads(line))
            except Exception:
                pass
    return stubs


def _save_all(stubs: list[dict]) -> None:
    """Rewrite the queue file atomically."""
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _QUEUE_FILE.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in stubs) + "\n",
        encoding="utf-8",
    )
    tmp.replace(_QUEUE_FILE)


def _article_to_stub(article) -> dict:
    """Convert a NewsArticle dataclass → compact queue stub."""
    content = (article.content or "").strip()
    return {
        "id":           article.id,
        "title":        (article.title or "").strip(),
        "content":      content[:CONTENT_CAP],
        "summary":      content[:SUMMARY_CAP],
        "url":          article.url or "",
        "source":       article.source or "",
        "author":       article.author or "Staff",
        "published_at": article.published_at or datetime.now().isoformat(),
        "category":     article.category or "general",
        "image_url":    article.image_url or "",
        "queued_at":    datetime.now().isoformat(),
        "status":       "pending",
        "processed_at": "",
        "error":        "",
    }


def _stub_to_article(stub: dict):
    """Reconstruct a NewsArticle from a queue stub (lazy import to avoid circular)."""
    from scraper.news_scraper import NewsArticle
    a = NewsArticle(
        id=stub["id"],
        title=stub["title"],
        content=stub["content"],
        summary=stub["summary"],
        url=stub["url"],
        source=stub["source"],
        author=stub["author"],
        published_at=stub["published_at"],
        category=stub["category"],
        image_url=stub["image_url"],
    )
    return a


# ── Public API ────────────────────────────────────────────────────────────────

def enqueue_batch(articles: list, skip_existing: bool = True) -> int:
    """
    Add a list of NewsArticle objects to the queue.
    Returns the number actually enqueued (skips duplicates if skip_existing=True).
    """
    with _lock:
        existing = _load_all()
        existing_ids = {s["id"] for s in existing}

        added = 0
        for article in articles:
            if skip_existing and article.id in existing_ids:
                logger.debug(f"Queue: skipping duplicate {article.id} — {article.title[:50]}")
                continue
            stub = _article_to_stub(article)
            existing.append(stub)
            existing_ids.add(article.id)
            added += 1

        if added:
            _save_all(existing)
            logger.info(f"Queue: added {added} articles ({queue_depth()} pending total)")
        return added


def pop_next() -> Optional[dict]:
    """
    Return the oldest *pending* stub and mark it as 'processing'.
    Returns None if the queue is empty.
    """
    with _lock:
        stubs = _load_all()
        for stub in stubs:
            if stub.get("status") == "pending":
                stub["status"] = "processing"
                _save_all(stubs)
                logger.info(f"Queue: popped — {stub['id']} | {stub['title'][:60]}")
                return stub
    return None


def pop_by_id(article_id: str) -> Optional[dict]:
    """
    Return a specific *pending* stub by its ID and mark it as 'processing'.
    Returns None if not found or not in pending state.
    """
    with _lock:
        stubs = _load_all()
        for stub in stubs:
            if stub.get("id") == article_id and stub.get("status") == "pending":
                stub["status"] = "processing"
                _save_all(stubs)
                logger.info(f"Queue: popped by id — {stub['id']} | {stub['title'][:60]}")
                return stub
    return None


def mark_done(article_id: str) -> None:
    """Mark a stub as successfully processed."""
    with _lock:
        stubs = _load_all()
        for stub in stubs:
            if stub["id"] == article_id:
                stub["status"] = "done"
                stub["processed_at"] = datetime.now().isoformat()
                break
        _save_all(stubs)


def mark_failed(article_id: str, error: str = "") -> None:
    """Mark a stub as failed so it won't be retried automatically."""
    with _lock:
        stubs = _load_all()
        for stub in stubs:
            if stub["id"] == article_id:
                stub["status"] = "failed"
                stub["processed_at"] = datetime.now().isoformat()
                stub["error"] = error[:200]
                break
        _save_all(stubs)


def requeue_stalled() -> int:
    """
    Reset any stubs stuck in 'processing' (e.g. after a crash) back to 'pending'.
    Call this at startup.
    """
    with _lock:
        stubs = _load_all()
        count = 0
        for stub in stubs:
            if stub.get("status") == "processing":
                stub["status"] = "pending"
                count += 1
        if count:
            _save_all(stubs)
            logger.warning(f"Queue: reset {count} stalled 'processing' stubs → 'pending'")
        return count


def queue_depth() -> int:
    """Number of pending articles in the queue."""
    return sum(1 for s in _load_all() if s.get("status") == "pending")


def queue_stats() -> dict:
    """Return counts per status."""
    stubs = _load_all()
    stats = {"pending": 0, "processing": 0, "done": 0, "failed": 0, "total": len(stubs)}
    for s in stubs:
        status = s.get("status", "pending")
        if status in stats:
            stats[status] += 1
    return stats


def get_queue_preview(limit: int = 30) -> list[dict]:
    """Return the most recent stubs for the dashboard (newest first)."""
    stubs = _load_all()
    return list(reversed(stubs[-limit:]))


def stub_to_article(stub: dict):
    """Public alias."""
    return _stub_to_article(stub)


def get_all_stubs(statuses: Optional[list] = None) -> list[dict]:
    """
    Return all stubs from the queue, optionally filtered by status list.
    If statuses is None, all stubs (pending + done + failed + processing) are returned.
    Used by ArticleRanker for bundle generation.
    """
    stubs = _load_all()
    if statuses is None:
        return stubs
    return [s for s in stubs if s.get("status") in statuses]


def purge_old(keep_days: int = 7) -> int:
    """Remove done/failed stubs older than keep_days. Returns number removed."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
    with _lock:
        stubs = _load_all()
        before = len(stubs)
        stubs = [
            s for s in stubs
            if s.get("status") == "pending"
            or s.get("queued_at", "9999") >= cutoff
        ]
        if len(stubs) < before:
            _save_all(stubs)
        return before - len(stubs)
