import random
from datetime import datetime, timedelta, time as dtime
from typing import Optional, Tuple, Dict, Any
try:
    from core.config import settings
    from core.database import get_db
except ImportError:
    from backend.core.config import settings
    from backend.core.database import get_db

def normalize_platform(p: Optional[str]) -> str:
    p_clean = (p or "").lower().strip()
    if p_clean in ("instagram", "instagram_reels", "ig"):
        return "instagram"
    if p_clean in ("tiktok", "tt"):
        return "tiktok"
    if p_clean in ("youtube", "youtube_shorts", "yt", "youtube_longform"):
        return "youtube"
    return p_clean or "general"

def get_random_cooldown(now: Optional[datetime] = None) -> int:
    """
    Computes a realistic human-like randomized cooldown in minutes
    based on the time of day.
    """
    current = now or datetime.now()
    # Daytime window: 06:00 - 15:00
    if dtime(6, 0) <= current.time() < dtime(15, 0):
        return random.randint(settings.DAY_COOLDOWN_MIN, settings.DAY_COOLDOWN_MAX)
    # Peak evening window: 15:00 - 24:00 and late night
    return random.randint(settings.EVENING_COOLDOWN_MIN, settings.EVENING_COOLDOWN_MAX)

def get_last_post_time(platform: Optional[str] = None) -> Optional[datetime]:
    """Retrieves the timestamp of the last successful post from the database for a specific platform."""
    conn = get_db()
    cursor = conn.cursor()
    if platform:
        norm = normalize_platform(platform)
        if norm == "instagram":
            cursor.execute("SELECT published_at FROM post_history WHERE LOWER(platform) IN ('instagram', 'instagram_reels', 'ig') ORDER BY published_at DESC LIMIT 1")
        elif norm == "tiktok":
            cursor.execute("SELECT published_at FROM post_history WHERE LOWER(platform) IN ('tiktok', 'tt') ORDER BY published_at DESC LIMIT 1")
        elif norm == "youtube":
            cursor.execute("SELECT published_at FROM post_history WHERE LOWER(platform) IN ('youtube', 'youtube_shorts', 'yt', 'youtube_longform') ORDER BY published_at DESC LIMIT 1")
        else:
            cursor.execute("SELECT published_at FROM post_history WHERE LOWER(platform) = ? ORDER BY published_at DESC LIMIT 1", (norm,))
    else:
        cursor.execute("SELECT published_at FROM post_history ORDER BY published_at DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    if row and row["published_at"]:
        try:
            return datetime.fromisoformat(row["published_at"])
        except Exception:
            return None
    return None

def evaluate_posting_schedule(platform: Optional[str] = None) -> Tuple[bool, int, str]:
    """
    Evaluates whether the system should post now for a specific platform, how many items to post,
    and returns (can_post, batch_count, status_message).
    """
    norm_plat = normalize_platform(platform) if platform else "general"
    last_post = get_last_post_time(norm_plat if platform else None)
    now = datetime.now()

    if last_post is None:
        return True, 1, f"Initial post ({norm_plat.capitalize()}): No previous posts found in history."

    time_since_last = now - last_post
    threshold = timedelta(minutes=settings.CATCHUP_THRESHOLD_MIN)

    # 1. Extended downtime Catch-Up Mode!
    if time_since_last >= threshold:
        idle_m = int(time_since_last.total_seconds() / 60)
        return True, settings.MAX_CATCHUP_POSTS, f"CATCH-UP MODE ({norm_plat.capitalize()}): Idle for {idle_m}m (>{settings.CATCHUP_THRESHOLD_MIN}m). Queuing up to {settings.MAX_CATCHUP_POSTS} posts."

    # 2. Regular randomized cooldown
    cooldown_min = get_random_cooldown(now)
    passed_min = int(time_since_last.total_seconds() / 60)
    if time_since_last >= timedelta(minutes=cooldown_min):
        return True, 1, f"Ready ({norm_plat.capitalize()}): {passed_min}m passed since last post (required {cooldown_min}m)."

    wait_remaining = cooldown_min - passed_min
    return False, 0, f"Waiting cooldown ({norm_plat.capitalize()}): {wait_remaining}m remaining (current cooldown {cooldown_min}m)."

def evaluate_all_platforms() -> Dict[str, Dict[str, Any]]:
    """Evaluates posting schedule across all supported platforms independently."""
    platforms = ["instagram", "tiktok", "youtube"]
    results = {}
    for p in platforms:
        can_post, batch_count, reason = evaluate_posting_schedule(p)
        last_post = get_last_post_time(p)
        results[p] = {
            "can_post_now": can_post,
            "recommended_batch": batch_count,
            "schedule_reason": reason,
            "last_post_time": last_post.isoformat() if last_post else None
        }
    return results
