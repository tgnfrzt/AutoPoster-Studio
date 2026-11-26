import os
import json
import time
from typing import Dict, Any, Optional, List
from core.config import settings

def get_youtube_service():
    """
    Builds an authenticated YouTube Resource object using saved credentials.
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request

    token_path = settings.YOUTUBE_TOKEN_FILE
    if not os.path.isfile(token_path):
        raise FileNotFoundError(f"YouTube credentials token not found at {token_path}. Please authenticate in the Settings panel.")

    with open(token_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    creds = Credentials.from_authorized_user_info(info)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except Exception as ex:
            raise RuntimeError(
                f"YouTube authorization token has expired or was revoked by Google ({ex}). "
                "Please open AutoPoster Settings and click '1-Click Authorize Channel' to re-authorize your YouTube account."
            ) from ex
    elif creds.expired and not creds.refresh_token:
        raise RuntimeError(
            "YouTube token has expired and has no refresh token. "
            "Please open AutoPoster Settings and click '1-Click Authorize Channel' to re-authorize your YouTube account."
        )

    return build("youtube", "v3", credentials=creds)

def publish_youtube_shorts(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: str = "public",
    is_shorts: bool = True
) -> Dict[str, Any]:
    """
    Uploads a video to YouTube (Shorts or standard Long-Form) using resumable chunked upload.
    """
    from googleapiclient.http import MediaFileUpload

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    clean_title = (title or "").strip() or "Untitled Video"
    if is_shorts:
        if "#Shorts" not in clean_title and "#shorts" not in clean_title:
            clean_title = f"{clean_title[:80]} #Shorts"
        desc = (description or "").strip() or settings.DEFAULT_YOUTUBE_CAPTION
        if "#Shorts" not in desc and "#shorts" not in desc:
            desc = f"{desc}\n\n#Shorts #Viral #Trending"
    else:
        desc = (description or "").strip() or settings.DEFAULT_YOUTUBE_CAPTION

    youtube = get_youtube_service()

    effective_privacy = privacy_status or settings.YOUTUBE_DEFAULT_PRIVACY or "public"
    body = {
        "snippet": {
            "title": clean_title[:100],
            "description": desc,
            "tags": tags or (["Shorts", "Viral", "Reels"] if is_shorts else ["Video", "YouTube"]),
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": effective_privacy,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=1024*1024*5, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[YouTubePublisher] Uploaded {int(status.progress() * 100)}%")

    video_id = response.get("id")
    post_url = f"https://www.youtube.com/shorts/{video_id}" if is_shorts else f"https://www.youtube.com/watch?v={video_id}"
    return {
        "success": True,
        "platform": "youtube_shorts" if is_shorts else "youtube_longform",
        "video_id": video_id,
        "post_url": post_url,
        "raw_response": response
    }

publish_youtube_video = publish_youtube_shorts
