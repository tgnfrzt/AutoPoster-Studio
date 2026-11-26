import os
import sys
import json
import subprocess
import requests
from typing import Dict, Any, Optional
try:
    from core.config import settings
except ImportError:
    from backend.core.config import settings

def publish_tiktok_via_session(
    video_path: str,
    caption: str = "",
    session_id: Optional[str] = None,
    headless: bool = True
) -> Dict[str, Any]:
    """
    Publishes a video to TikTok via Web Session automation (Playwright + Edge/Chrome).
    Executed in an isolated subprocess to prevent Playwright Sync API / Asyncio conflicts.
    """
    sess = session_id or settings.TIKTOK_SESSION_ID
    cookies_path = settings.TIKTOK_COOKIES_PATH if os.path.isfile(settings.TIKTOK_COOKIES_PATH) else None

    if not sess and not cookies_path:
        raise ValueError(
            "Neither TikTok session_id nor tiktok_cookies.txt found. "
            "Please paste your sessionid from tiktok.com in Settings > TikTok."
        )

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    final_caption = (caption or "").strip() or settings.DEFAULT_TIKTOK_CAPTION
    print(f"[TikTokPublisher] Starting session upload for: {os.path.basename(video_path)}")

    runner_path = os.path.join(os.path.dirname(__file__), "tiktok_runner.py")
    python_exe = sys.executable

    # Try msedge first (native on Windows), then chrome, then chromium
    browsers_to_try = ["edge", "chrome", "chromium"]
    last_err = None

    for b in browsers_to_try:
        try:
            print(f"[TikTokPublisher] Attempting upload with browser: {b} (headless={headless}, subprocess isolated)...")
            cmd = [
                python_exe,
                runner_path,
                "--video-path", video_path,
                "--caption", final_caption,
                "--browser", b
            ]
            if sess:
                cmd.extend(["--session-id", sess])
            if cookies_path:
                cmd.extend(["--cookies-path", cookies_path])
            if not headless:
                cmd.append("--no-headless")

            sub_env = os.environ.copy()
            sub_env["PYTHONIOENCODING"] = "utf-8"
            sub_env["PYTHONUTF8"] = "1"

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
                timeout=360
            )

            # Look for JSON output in stdout
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            parsed_out = None
            for line in reversed(lines):
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed_out = json.loads(line)
                        break
                    except Exception:
                        pass

            if res.returncode == 0 and parsed_out and parsed_out.get("success"):
                print(f"[TikTokPublisher] Video published successfully via session ({b})!")
                return {
                    "success": True,
                    "platform": "tiktok",
                    "method": "session_uploader",
                    "browser": b,
                    "caption": final_caption,
                    "message": "Video successfully uploaded and published to TikTok using session ID!"
                }
            else:
                err_msg = (parsed_out.get("error") if parsed_out else None) or res.stderr.strip() or f"Process exited with code {res.returncode}"
                last_err = Exception(err_msg)
                print(f"[TikTokPublisher] Browser '{b}' upload error: {err_msg}. Trying next browser...")

        except subprocess.TimeoutExpired:
            last_err = Exception(f"Upload timed out after 300s using browser '{b}'")
            print(f"[TikTokPublisher] {last_err}. Trying next browser...")
        except Exception as e:
            last_err = e
            print(f"[TikTokPublisher] Browser '{b}' upload error: {e}. Trying next browser...")

    raise RuntimeError(f"TikTok session upload failed across available browsers: {last_err}")

def publish_tiktok_via_api(
    video_path: str,
    caption: str = "",
    access_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Publishes a video to TikTok via official Content Posting API v2.
    """
    token = access_token or settings.TIKTOK_ACCESS_TOKEN
    if not token:
        raise ValueError("TikTok API Access Token not configured.")

    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    final_caption = (caption or "").strip() or settings.DEFAULT_TIKTOK_CAPTION
    payload = {
        "post_info": {
            "title": final_caption[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": os.path.getsize(video_path),
            "chunk_size": os.path.getsize(video_path),
            "total_chunk_count": 1
        }
    }

    res = requests.post(init_url, json=payload, headers=headers, timeout=30)
    data = res.json()
    if data.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"TikTok API initialization failed: {data}")

    upload_url = data["data"]["upload_url"]
    publish_id = data["data"]["publish_id"]

    # Upload video binary to TikTok CDN
    with open(video_path, "rb") as f:
        put_headers = {
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{os.path.getsize(video_path)-1}/{os.path.getsize(video_path)}"
        }
        requests.put(upload_url, data=f, headers=put_headers, timeout=180)

    return {
        "success": True,
        "platform": "tiktok",
        "method": "official_api",
        "publish_id": publish_id,
        "post_url": "https://www.tiktok.com"
    }

def publish_tiktok(
    video_path: str,
    caption: str = "",
    access_token: Optional[str] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Universal TikTok publishing dispatcher:
    1. Primary: Session ID / cookies Web Uploader (No manual app review required).
    2. Fallback: Official Content Posting API v2 (if an approved access token is provided).
    """
    sess = session_id or settings.TIKTOK_SESSION_ID
    has_cookies = os.path.isfile(settings.TIKTOK_COOKIES_PATH)
    token = access_token or settings.TIKTOK_ACCESS_TOKEN

    # Priority 1: Session Uploader (Preferred for personal/creator automation)
    if sess or has_cookies:
        return publish_tiktok_via_session(
            video_path=video_path,
            caption=caption,
            session_id=sess
        )

    # Priority 2: Official Content Posting API v2
    if token:
        return publish_tiktok_via_api(
            video_path=video_path,
            caption=caption,
            access_token=token
        )

    raise ValueError(
        "No TikTok credentials found. TikTok requires either a sessionid cookie (Recommended) "
        "or an approved Content Posting API v2 Access Token. Please configure this in Settings > TikTok."
    )

