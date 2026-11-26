import os
import time
import requests
from typing import Dict, Any, Optional
try:
    from core.config import settings
except ImportError:
    from backend.core.config import settings

def upload_to_temp_hosting(video_path: str, hosting_url: str) -> Optional[str]:
    """Uploads local video to temporary hosting and returns the public URL."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > 95.0:
        raise RuntimeError(
            f"Video file is too large ({file_size_mb:.1f} MB) for Instagram temporary hosting relay. "
            f"PythonAnywhere and Meta Reels have a 95-100MB limit. "
            f"Instagram Reels must be vertical 9:16 videos under 90s. "
            f"For long-form 16:9 videos, please publish to YouTube instead."
        )

    try:
        with open(video_path, "rb") as f:
            files = {"file": (os.path.basename(video_path), f, "video/mp4")}
            res = requests.post(f"{hosting_url.rstrip('/')}/upload_video", files=files, timeout=120)
        if res.status_code == 200:
            data = res.text.strip()
            # If server returned JSON or direct URL string
            if data.startswith("{"):
                payload = res.json()
                return payload.get("video_url") or payload.get("url")
            return data
        else:
            raise RuntimeError(
                f"PythonAnywhere rejected the video upload with HTTP {res.status_code} ({res.reason}). "
                f"Response: {res.text[:150]}"
            )
    except Exception as e:
        print(f"[InstagramPublisher] Hosting upload error: {e}")
        raise

def delete_from_temp_hosting(video_url: str, hosting_url: str) -> None:
    """Guarantees deletion of the temporary video from the remote server."""
    if not video_url:
        return
    try:
        filename = video_url.split("/")[-1]
        del_url = f"{hosting_url.rstrip('/')}/delete_video/{filename}"
        r = requests.post(del_url, timeout=15)
        if r.status_code not in (200, 404):
            requests.post(f"{hosting_url.rstrip('/')}/delete_video", data={"filename": filename}, timeout=15)
        print(f"[InstagramPublisher] Cleaned up remote file: {filename}")
    except Exception as e:
        print(f"[InstagramPublisher] Remote cleanup warning: {e}")

def wait_container_ready(
    container_id: str,
    access_token: str,
    max_attempts: int = 25,
    interval_sec: int = 10
) -> bool:
    """Polls Instagram Graph API until media container status becomes FINISHED."""
    status_url = f"{settings.GRAPH_API_URL}/{container_id}"
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.get(status_url, params={"fields": "status_code", "access_token": access_token}, timeout=20)
            if r.status_code == 200:
                data = r.json()
                status = data.get("status_code")
                if status == "FINISHED":
                    return True
                elif status in ("ERROR", "EXPIRED"):
                    print(f"[InstagramPublisher] Container failed with status: {status}")
                    return False
        except Exception as e:
            print(f"[InstagramPublisher] Check attempt {attempt} error: {e}")
        time.sleep(interval_sec)
    return False

def publish_instagram_graph_api(
    video_path: str,
    caption: str = "",
    business_id: Optional[str] = None,
    access_token: Optional[str] = None,
    hosting_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Publishes a video to Instagram Reels via official Graph API with automated hosting & cleanup.
    """
    b_id = business_id or settings.INSTAGRAM_BUSINESS_ID
    token = access_token or settings.PAGE_ACCESS_TOKEN
    host = hosting_url or settings.REMOTE_HOSTING_URL

    if not token or not b_id:
        raise ValueError("Instagram credentials missing: PAGE_ACCESS_TOKEN or INSTAGRAM_BUSINESS_ID not set.")

    public_url = upload_to_temp_hosting(video_path, host)
    if not public_url:
        raise RuntimeError("Failed to upload video to temporary public hosting.")

    try:
        # Step 1: Create media container
        create_url = f"{settings.GRAPH_API_URL}/{b_id}/media"
        final_caption = (caption or "").strip() or settings.DEFAULT_INSTAGRAM_CAPTION
        params = {
            "access_token": token,
            "caption": final_caption,
            "media_type": "REELS",
            "video_url": public_url
        }
        res = requests.post(create_url, params=params, timeout=30)
        data = res.json()

        if "id" not in data:
            error_msg = data.get("error", {}).get("message", res.text)
            raise RuntimeError(f"Instagram Graph API container creation failed: {error_msg}")

        container_id = data["id"]

        # Step 2: Wait for Instagram to fetch and transcode the video
        ready = wait_container_ready(container_id, token)
        if not ready:
            raise TimeoutError("Instagram media container did not reach FINISHED status in time.")

        # Step 3: Publish container
        pub_url = f"{settings.GRAPH_API_URL}/{b_id}/media_publish"
        pub_res = requests.post(pub_url, params={"creation_id": container_id, "access_token": token}, timeout=30)
        pub_data = pub_res.json()

        if "id" not in pub_data:
            err = pub_data.get("error", {}).get("message", pub_res.text)
            raise RuntimeError(f"Instagram media_publish failed: {err}")

        post_id = pub_data["id"]
        return {
            "success": True,
            "platform": "instagram",
            "method": "graph_api",
            "post_id": post_id,
            "container_id": container_id,
            "post_url": f"https://www.instagram.com/reel/{post_id}/"
        }

    finally:
        # ALWAYS guarantee remote cleanup
        delete_from_temp_hosting(public_url, host)
