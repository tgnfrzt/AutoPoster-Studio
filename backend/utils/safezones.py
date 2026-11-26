from typing import Dict, Any

SAFE_ZONES = {
    "instagram": {
        "name": "Instagram Reels",
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "margins": {
            "top": 288,     # 15% top margin
            "bottom": 384,  # 20% bottom margin (captions/audio)
            "left": 60,     # 5.5% left margin
            "right": 162    # 15% right margin (action icons)
        },
        "max_recommended_duration_sec": 90,
        "absolute_max_duration_sec": 900
    },
    "youtube_shorts": {
        "name": "YouTube Shorts",
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "margins": {
            "top": 192,     # 10% top margin
            "bottom": 480,  # 25% bottom margin (title, channel, subscribe)
            "left": 40,
            "right": 140
        },
        "max_recommended_duration_sec": 60,
        "absolute_max_duration_sec": 180
    },
    "tiktok": {
        "name": "TikTok",
        "aspect_ratio": "9:16",
        "width": 1080,
        "height": 1920,
        "margins": {
            "top": 210,
            "bottom": 384,
            "left": 40,
            "right": 162
        },
        "max_recommended_duration_sec": 60,
        "absolute_max_duration_sec": 600
    }
}

def get_safe_zone(platform: str) -> Dict[str, Any]:
    return SAFE_ZONES.get(platform, SAFE_ZONES["instagram"])
