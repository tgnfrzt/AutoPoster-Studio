import os
from datetime import datetime
from fastapi import APIRouter
from core.config import settings, get_watermark_path
from core.database import get_db
from core.worker import pull_remote_links, get_pull_status
from modules.dispatch.scheduler import evaluate_posting_schedule, get_last_post_time, get_random_cooldown, evaluate_all_platforms
from utils.safezones import SAFE_ZONES

router = APIRouter(prefix="/api/system", tags=["system"])

@router.get("/status")
def get_system_status():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT status, count(*) as count FROM jobs GROUP BY status")
    counts = {r["status"]: r["count"] for r in cursor.fetchall()}

    cursor.execute("SELECT count(*) as total FROM post_history")
    history_total = cursor.fetchone()["total"]
    conn.close()

    # Outros count in resources/outros/
    outros_count = 0
    if os.path.isdir(settings.OUTROS_PATH):
        outros_count = len([f for f in os.listdir(settings.OUTROS_PATH) if f.lower().endswith((".mp4", ".mov", ".mkv"))])

    watermark_exists = os.path.isfile(settings.WATERMARK_PATH)

    return {
        "app_name": settings.APP_NAME,
        "ffmpeg_installed": os.path.isfile(settings.FFMPEG_PATH),
        "ffmpeg_path": settings.FFMPEG_PATH,
        "ffprobe_installed": os.path.isfile(settings.FFPROBE_PATH),
        "ytdlp_installed": os.path.isfile(settings.YTDLP_PATH),
        "job_counts": counts,
        "total_published": history_total,
        "remote_hosting_url": settings.REMOTE_HOSTING_URL,
        "pull_status": get_pull_status(),
        "instagram_configured": bool(settings.PAGE_ACCESS_TOKEN and settings.INSTAGRAM_BUSINESS_ID),
        "youtube_configured": os.path.isfile(settings.YOUTUBE_TOKEN_FILE),
        "tiktok_configured": bool(settings.TIKTOK_ACCESS_TOKEN or settings.TIKTOK_SESSION_ID or os.path.isfile(settings.TIKTOK_COOKIES_PATH)),
        "watermark_found": watermark_exists,
        "watermark_path": settings.WATERMARK_PATH,
        "outros_available": outros_count,
        "outros_path": settings.OUTROS_PATH
    }

@router.get("/scheduler")
def get_scheduler_status():
    all_plats = evaluate_all_platforms()
    can_post_any = any(p["can_post_now"] for p in all_plats.values())
    last_post = get_last_post_time()

    reasons = [p_data["schedule_reason"] for p_data in all_plats.values()]
    summary_reason = " | ".join(reasons)

    return {
        "can_post_now": can_post_any,
        "recommended_batch": 1,
        "schedule_reason": summary_reason,
        "last_post_time": last_post.isoformat() if last_post else None,
        "current_random_cooldown_min": get_random_cooldown(),
        "daytime_range": f"{settings.DAY_COOLDOWN_MIN}-{settings.DAY_COOLDOWN_MAX}m",
        "evening_range": f"{settings.EVENING_COOLDOWN_MIN}-{settings.EVENING_COOLDOWN_MAX}m",
        "catchup_threshold": f"{settings.CATCHUP_THRESHOLD_MIN}m",
        "platforms": all_plats
    }

@router.post("/sync_pythonanywhere")
def trigger_pythonanywhere_sync():
    """Manually triggers instant link pull from the PythonAnywhere queue server."""
    new_count = pull_remote_links()
    return {
        "success": True,
        "new_links_pulled": new_count,
        "message": f"Successfully pulled {new_count} new link(s) from PythonAnywhere server." if new_count > 0 else "PythonAnywhere queue is currently empty or contains only already-processed links."
    }

@router.get("/safezones")
def get_safezones_spec():
    return SAFE_ZONES

@router.get("/history")
def get_publish_history(limit: int = 50):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM post_history ORDER BY published_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"history": rows}

# ==========================================
# MULTI-PLATFORM ANALYTICS & STATISTICS
# ==========================================

def _get_youtube_stats() -> dict:
    if not os.path.isfile(settings.YOUTUBE_TOKEN_FILE):
        return {"connected": False, "status": "Token missing. Authorize YouTube in Settings."}
    try:
        import json
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request

        with open(settings.YOUTUBE_TOKEN_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
        creds = Credentials.from_authorized_user_info(info)
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(settings.YOUTUBE_TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except Exception as e:
                return {"connected": False, "status": "Token Expired", "error": f"Session expired ({e}). Please re-authorize."}
        elif creds.expired and not creds.refresh_token:
            return {"connected": False, "status": "Token Expired", "error": "Token expired with no refresh token."}

        yt = build("youtube", "v3", credentials=creds)
        res = yt.channels().list(mine=True, part="snippet,statistics,contentDetails").execute()
        items = res.get("items", [])
        if not items:
            return {"connected": True, "channel_title": "No Channel", "subscribers": 0, "views": 0, "videos": 0, "recent_videos": []}
        ch = items[0]
        snippet = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        content = ch.get("contentDetails", {})
        
        recent_videos = []
        uploads_playlist = content.get("relatedPlaylists", {}).get("uploads")
        if uploads_playlist:
            try:
                p_res = yt.playlistItems().list(playlistId=uploads_playlist, part="snippet,contentDetails", maxResults=5).execute()
                video_ids = [item["contentDetails"]["videoId"] for item in p_res.get("items", []) if "contentDetails" in item]
                if video_ids:
                    v_res = yt.videos().list(id=",".join(video_ids), part="snippet,statistics").execute()
                    for v in v_res.get("items", []):
                        v_snip = v.get("snippet", {})
                        v_stat = v.get("statistics", {})
                        v_id = v.get("id")
                        recent_videos.append({
                            "id": v_id,
                            "title": v_snip.get("title"),
                            "published_at": v_snip.get("publishedAt"),
                            "thumbnail": v_snip.get("thumbnails", {}).get("medium", {}).get("url") or v_snip.get("thumbnails", {}).get("default", {}).get("url"),
                            "views": int(v_stat.get("viewCount", 0)),
                            "likes": int(v_stat.get("likeCount", 0)),
                            "comments": int(v_stat.get("commentCount", 0)),
                            "url": f"https://www.youtube.com/watch?v={v_id}"
                        })
            except Exception as ex:
                print(f"[Analytics] YouTube playlist fetch warning: {ex}")

        views_num = int(stats.get("viewCount", 0)) if str(stats.get("viewCount", "")).isdigit() else 0
        video_count_num = int(stats.get("videoCount", 0)) if str(stats.get("videoCount", "")).isdigit() else 0
        est_watch_hours = round((views_num * 40.0) / 3600.0, 1)
        channel_url = f"https://www.youtube.com/{snippet.get('customUrl', '')}" if snippet.get("customUrl") else f"https://www.youtube.com/channel/{ch.get('id')}"

        return {
            "connected": True,
            "channel_id": ch.get("id"),
            "channel_title": snippet.get("title"),
            "custom_url": snippet.get("customUrl", ""),
            "channel_url": channel_url,
            "avatar": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            "subscribers": int(stats.get("subscriberCount", 0)) if str(stats.get("subscriberCount", "")).isdigit() else stats.get("subscriberCount", "0"),
            "views": views_num,
            "videos": video_count_num,
            "estimated_watch_hours": est_watch_hours,
            "recent_videos": recent_videos
        }
    except Exception as e:
        return {"connected": False, "status": "Error", "error": str(e)}

def _get_instagram_stats() -> dict:
    b_id = settings.INSTAGRAM_BUSINESS_ID
    token = settings.PAGE_ACCESS_TOKEN
    if not b_id or not token:
        return {"connected": False, "status": "Not Configured", "error": "Instagram Business ID or Page Token not set."}
    try:
        import requests
        acc_url = f"{settings.GRAPH_API_URL}/{b_id}"
        r = requests.get(acc_url, params={"fields": "name,username,followers_count,media_count,profile_picture_url", "access_token": token}, timeout=10)
        if r.status_code != 200:
            err_data = r.json().get("error", {})
            return {"connected": False, "status": "Auth Error", "error": err_data.get("message", r.text)}
        acc = r.json()

        recent_media = []
        try:
            m_res = requests.get(f"{settings.GRAPH_API_URL}/{b_id}/media", params={"fields": "id,caption,media_type,timestamp,like_count,comments_count,permalink,thumbnail_url,media_url", "limit": 5, "access_token": token}, timeout=10)
            if m_res.status_code == 200:
                for item in m_res.json().get("data", []):
                    recent_media.append({
                        "id": item.get("id"),
                        "caption": item.get("caption", "")[:80] + "..." if len(item.get("caption", "")) > 80 else item.get("caption", ""),
                        "permalink": item.get("permalink"),
                        "media_type": item.get("media_type"),
                        "timestamp": item.get("timestamp"),
                        "thumbnail": item.get("thumbnail_url") or item.get("media_url"),
                        "likes": item.get("like_count", 0),
                        "comments": item.get("comments_count", 0)
                    })
        except Exception:
            pass

        return {
            "connected": True,
            "username": acc.get("username"),
            "name": acc.get("name"),
            "avatar": acc.get("profile_picture_url", ""),
            "followers": acc.get("followers_count", 0),
            "media_count": acc.get("media_count", 0),
            "recent_posts": recent_media
        }
    except Exception as e:
        return {"connected": False, "status": "Error", "error": str(e)}

def _get_tiktok_stats() -> dict:
    has_session = bool(settings.TIKTOK_SESSION_ID)
    has_cookies = os.path.isfile(settings.TIKTOK_COOKIES_PATH)
    has_token = bool(settings.TIKTOK_ACCESS_TOKEN)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT count(*) as total FROM post_history WHERE LOWER(platform) IN ('tiktok', 'tt')")
    tt_total = c.fetchone()["total"]
    c.execute("SELECT post_url, published_at FROM post_history WHERE LOWER(platform) IN ('tiktok', 'tt') ORDER BY published_at DESC LIMIT 5")
    recent = [dict(r) for r in c.fetchall()]
    conn.close()

    username = "bigblackguyreposts"
    nickname = "BigBlackGuyReposts"
    avatar = ""
    followers = 0
    following = 0
    hearts = 0
    video_count = 0

    if has_session:
        import requests, re
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        cookies = {"sessionid": settings.TIKTOK_SESSION_ID, "sessionid_ss": settings.TIKTOK_SESSION_ID}

        try:
            r_acc = requests.get("https://www.tiktok.com/passport/web/account/info/", headers=headers, cookies=cookies, timeout=5)
            if r_acc.status_code == 200:
                acc_data = r_acc.json().get("data", {})
                username = acc_data.get("username") or username
                nickname = acc_data.get("screen_name") or nickname
                avatar = acc_data.get("avatar_url") or avatar
        except Exception:
            pass

        if username:
            try:
                r_web = requests.get(f"https://www.tiktok.com/@{username}", headers=headers, cookies=cookies, timeout=6)
                if r_web.status_code == 200:
                    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.+?)</script>', r_web.text)
                    if m:
                        data = json.loads(m.group(1))
                        user_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
                        userInfo = user_detail.get("userInfo", {})
                        st = userInfo.get("stats", {})
                        u = userInfo.get("user", {})
                        followers = int(st.get("followerCount", 0))
                        following = int(st.get("followingCount", 0))
                        hearts = int(st.get("heartCount", 0))
                        video_count = int(st.get("videoCount", 0))
                        if u.get("avatarLarger") or u.get("avatarMedium"):
                            avatar = u.get("avatarLarger") or u.get("avatarMedium")
                        if u.get("nickname"):
                            nickname = u.get("nickname")
            except Exception:
                pass

    est_views = max(hearts * 14, video_count * 25, tt_total * 45)
    est_watch_hours = round((est_views * 18.0) / 3600.0, 1)

    status = "Active Session" if (has_session or has_cookies or has_token) else "Not Configured"
    return {
        "connected": bool(has_session or has_cookies or has_token),
        "status": status,
        "username": username,
        "nickname": nickname,
        "avatar": avatar,
        "followers": followers,
        "following": following,
        "likes": hearts,
        "video_count": video_count or tt_total,
        "estimated_views": est_views,
        "estimated_watch_hours": est_watch_hours,
        "profile_url": f"https://www.tiktok.com/@{username}",
        "has_session_id": has_session,
        "has_cookies_file": has_cookies,
        "has_access_token": has_token,
        "total_published": tt_total,
        "recent_posts": recent
    }

def _get_autoposter_internal_stats() -> dict:
    import json
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT status, count(*) as count FROM jobs GROUP BY status")
    job_counts = {r["status"]: r["count"] for r in c.fetchall()}

    c.execute("SELECT platform, count(*) as count FROM post_history GROUP BY platform")
    platform_counts = {r["platform"]: r["count"] for r in c.fetchall()}

    c.execute("SELECT count(*) as total FROM post_history")
    total_published = c.fetchone()["total"]

    c.execute("SELECT count(*) as recent7 FROM post_history WHERE published_at >= datetime('now', '-7 days')")
    published_7d = c.fetchone()["recent7"]

    c.execute("""
        SELECT h.id, h.platform, h.post_url, h.published_at, j.metadata_json
        FROM post_history h
        LEFT JOIN jobs j ON h.job_id = j.id
        ORDER BY h.published_at DESC
        LIMIT 10
    """)
    recent_history = []
    for r in c.fetchall():
        meta = {}
        if r["metadata_json"]:
            try:
                meta = json.loads(r["metadata_json"])
            except Exception:
                pass
        recent_history.append({
            "id": r["id"],
            "platform": r["platform"],
            "post_url": r["post_url"],
            "published_at": r["published_at"],
            "title": meta.get("title") or meta.get("caption") or (r["platform"].capitalize() + " Post"),
            "recipe": meta.get("recipe", "standard")
        })
    conn.close()

    total_completed = job_counts.get("completed", 0)
    total_failed = job_counts.get("failed", 0) + job_counts.get("repair_required", 0)
    sum_jobs = total_completed + total_failed
    success_rate = round((total_completed / sum_jobs) * 100, 1) if sum_jobs > 0 else 100.0

    return {
        "total_published": total_published,
        "published_7d": published_7d,
        "success_rate": success_rate,
        "job_counts": job_counts,
        "platform_counts": platform_counts,
        "recent_history": recent_history
    }

_analytics_cache = None
_analytics_cache_time = 0.0

@router.get("/analytics")
def get_analytics():
    """Aggregates real-time statistics and historical performance across YouTube, Instagram, and TikTok with in-memory caching."""
    global _analytics_cache, _analytics_cache_time
    import time
    now = time.time()
    if _analytics_cache and (now - _analytics_cache_time < 30.0):
        return _analytics_cache

    try:
        data = {
            "timestamp": datetime.now().isoformat(),
            "autoposter": _get_autoposter_internal_stats(),
            "youtube": _get_youtube_stats(),
            "instagram": _get_instagram_stats(),
            "tiktok": _get_tiktok_stats()
        }
        _analytics_cache = data
        _analytics_cache_time = now
        return data
    except Exception as e:
        print(f"[Analytics] Error generating analytics: {e}")
        if _analytics_cache:
            return _analytics_cache
        return {
            "timestamp": datetime.now().isoformat(),
            "error": str(e),
            "autoposter": {"total_published": 0, "published_7d": 0, "success_rate": 100.0, "recent_history": []},
            "youtube": {"connected": False, "status": "Error", "error": str(e)},
            "instagram": {"connected": False, "status": "Error", "error": str(e)},
            "tiktok": {"connected": False, "status": "Error", "error": str(e)}
        }
