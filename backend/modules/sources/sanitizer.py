import re
from urllib.parse import urlparse, urlunparse
from datetime import datetime
from typing import Optional, List, Tuple
from core.database import get_db

def clean_url(url: str) -> str:
    """
    Sanitizes URLs by stripping tracking parameters, normalizing paths,
    and converting platform variants to canonical formats.
    """
    url = url.strip()
    if not url:
        return ""

    # 1. Instagram links
    if "instagram.com" in url:
        # Strip query parameters (?igsh=..., etc.)
        if "?" in url:
            url = url.split("?", 1)[0]
        # Normalize /reel/ to /p/
        if "/reel/" in url:
            url = url.replace("/reel/", "/p/")
        # Ensure trailing slash
        if not url.endswith("/"):
            url += "/"
        return url

    # 2. YouTube links (Shorts & Standard)
    if "youtube.com" in url or "youtu.be" in url:
        # Strip tracking queries but preserve v param if present
        parsed = urlparse(url)
        if "youtu.be" in parsed.netloc:
            video_id = parsed.path.strip("/")
            return f"https://www.youtube.com/watch?v={video_id}"
        elif "/shorts/" in parsed.path:
            video_id = parsed.path.split("/shorts/")[-1].strip("/")
            return f"https://www.youtube.com/shorts/{video_id}"
        elif "v=" in parsed.query:
            # Extract video ID
            match = re.search(r"v=([a-zA-Z0-9_-]+)", parsed.query)
            if match:
                return f"https://www.youtube.com/watch?v={match.group(1)}"

    # 3. TikTok links
    if "tiktok.com" in url:
        if "?" in url:
            url = url.split("?", 1)[0]
        return url.rstrip("/")

    # Generic: Strip query params
    if "?" in url:
        url = url.split("?", 1)[0]
    return url.strip()

def is_duplicate(canonical_url: str) -> bool:
    """Checks if the link has already been seen/processed in the database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM link_cache WHERE canonical_url = ?", (canonical_url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def record_link(canonical_url: str, status: str = "queued") -> bool:
    """
    Records a link into the deduplication cache.
    Returns True if newly inserted, False if it was already known.
    """
    if is_duplicate(canonical_url):
        return False
    conn = get_db()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO link_cache (canonical_url, first_seen, last_status) VALUES (?, ?, ?)",
            (canonical_url, datetime.now().isoformat(), status)
        )
    conn.close()
    return True

def sanitize_and_dedupe(urls: List[str]) -> Tuple[List[str], int]:
    """
    Sanitizes a list of raw URLs, deduplicates them against each other and against the database.
    Returns (unique_clean_urls, duplicate_count).
    """
    clean_urls = []
    seen_in_batch = set()
    dup_count = 0

    for raw in urls:
        cleaned = clean_url(raw)
        if not cleaned:
            continue
        if cleaned in seen_in_batch or is_duplicate(cleaned):
            dup_count += 1
            continue
        seen_in_batch.add(cleaned)
        clean_urls.append(cleaned)

    return clean_urls, dup_count
