import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from core.database import get_db
from core.config import settings
from modules.publishers.instagram import publish_instagram_graph_api
from modules.publishers.youtube import publish_youtube_shorts
from modules.publishers.tiktok import publish_tiktok

def dispatch_publish(
    job_id: str,
    platform: str,
    video_path: str,
    metadata: Dict[str, Any],
    credentials: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Universal entry point to publish video to any target platform.
    Catches errors, normalizes output, and persists history on success.
    """
    title = metadata.get("title", "")
    caption = metadata.get("caption", title)
    tags = metadata.get("tags", [])

    receipt = {}

    if platform.lower() in ("instagram", "instagram_reels", "ig"):
        receipt = publish_instagram_graph_api(
            video_path=video_path,
            caption=caption,
            business_id=credentials.get("business_id") if credentials else None,
            access_token=credentials.get("access_token") if credentials else None,
            hosting_url=credentials.get("hosting_url") if credentials else None
        )
    elif platform.lower() in ("youtube", "youtube_shorts", "yt", "youtube_longform"):
        is_shorts = metadata.get("is_shorts")
        if is_shorts is None:
            is_shorts = not (platform.lower() == "youtube_longform" or metadata.get("recipe") in ("youtube_longform", "longform", "ai_studio_longform"))
        receipt = publish_youtube_shorts(
            video_path=video_path,
            title=title,
            description=caption,
            tags=tags,
            privacy_status=metadata.get("privacy_status") or settings.YOUTUBE_DEFAULT_PRIVACY,
            is_shorts=is_shorts
        )
    elif platform.lower() in ("tiktok", "tt"):
        receipt = publish_tiktok(
            video_path=video_path,
            caption=caption,
            access_token=credentials.get("access_token") if credentials else settings.TIKTOK_ACCESS_TOKEN,
            session_id=credentials.get("session_id") if credentials else settings.TIKTOK_SESSION_ID
        )
    else:
        raise ValueError(f"Unsupported publishing platform: {platform}")

    # Record to post history in database
    if receipt.get("success"):
        post_url = receipt.get("post_url", "")
        conn = get_db()
        with conn:
            conn.execute(
                "INSERT INTO post_history (id, job_id, platform, post_url, published_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), job_id, platform, post_url, datetime.now().isoformat())
            )
        conn.close()

    return receipt
