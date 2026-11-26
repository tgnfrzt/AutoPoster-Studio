import os
import shutil
import random
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage"
RAW_DIR = STORAGE_DIR / "raw"
RENDERED_DIR = STORAGE_DIR / "rendered"
TEMPLATES_DIR = STORAGE_DIR / "templates"
DATA_DIR = STORAGE_DIR / "data"
RESOURCES_DIR = BASE_DIR / "resources"
OUTROS_DIR = RESOURCES_DIR / "outros"

for d in (RAW_DIR, RENDERED_DIR, TEMPLATES_DIR, DATA_DIR, RESOURCES_DIR, OUTROS_DIR):
    d.mkdir(parents=True, exist_ok=True)

ENV_FILE = BASE_DIR / ".env"

def load_env_file():
    if ENV_FILE.is_file():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

load_env_file()

@dataclass
class Settings:
    APP_NAME: str = "AutoPoster"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() in ("true", "1")
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "17420"))

    # Storage and Resources paths
    STORAGE_PATH: str = str(STORAGE_DIR)
    RAW_PATH: str = str(RAW_DIR)
    RENDERED_PATH: str = str(RENDERED_DIR)
    TEMPLATES_PATH: str = str(TEMPLATES_DIR)
    DATA_PATH: str = str(DATA_DIR)
    DB_PATH: str = str(DATA_DIR / "autoposter.db")
    RESOURCES_PATH: str = str(RESOURCES_DIR)
    OUTROS_PATH: str = str(OUTROS_DIR)
    WATERMARK_PATH: str = str(RESOURCES_DIR / "watermark.png")
    COOKIES_PATH: str = str(DATA_DIR / "cookies.txt")

    # Binaries resolution
    FFMPEG_PATH: str = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE_PATH: str = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"
    YTDLP_PATH: str = os.getenv("YTDLP_PATH") or shutil.which("yt-dlp") or "yt-dlp"

    # Instagram Configuration
    INSTAGRAM_BUSINESS_ID: str = os.getenv("INSTAGRAM_BUSINESS_ID", "")
    PAGE_ACCESS_TOKEN: str = os.getenv("PAGE_ACCESS_TOKEN", "")
    GRAPH_API_VERSION: str = "v23.0"
    GRAPH_API_URL: str = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
    REMOTE_HOSTING_URL: str = os.getenv("REMOTE_HOSTING_URL", "")

    # YouTube Configuration
    YOUTUBE_CLIENT_SECRETS_FILE: str = str(DATA_DIR / "client_secret.json")
    YOUTUBE_TOKEN_FILE: str = str(DATA_DIR / "youtube_token.json")
    YOUTUBE_DEFAULT_PRIVACY: str = os.getenv("YOUTUBE_DEFAULT_PRIVACY", "public")

    # TikTok Configuration
    TIKTOK_COOKIES_PATH: str = str(DATA_DIR / "tiktok_cookies.txt")
    TIKTOK_ACCESS_TOKEN: str = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    TIKTOK_CLIENT_KEY: str = os.getenv("TIKTOK_CLIENT_KEY", "")
    TIKTOK_CLIENT_SECRET: str = os.getenv("TIKTOK_CLIENT_SECRET", "")
    TIKTOK_SESSION_ID: str = os.getenv("TIKTOK_SESSION_ID", "")

    # Default Captions
    DEFAULT_INSTAGRAM_CAPTION: str = os.getenv(
        "DEFAULT_INSTAGRAM_CAPTION",
        "Follow for more!\n\n#reels #fyp #viral #explorepage #instagramreels"
    )
    DEFAULT_TIKTOK_CAPTION: str = os.getenv(
        "DEFAULT_TIKTOK_CAPTION",
        "Check this out! #fyp #viral #trending #tiktok"
    )
    DEFAULT_YOUTUBE_CAPTION: str = os.getenv(
        "DEFAULT_YOUTUBE_CAPTION",
        "Subscribe for more daily videos! #Shorts #Viral #Trending"
    )

    # Scheduler and Smart Fallbacks
    DAY_COOLDOWN_MIN: int = int(os.getenv("DAY_COOLDOWN_MIN", "45"))
    DAY_COOLDOWN_MAX: int = int(os.getenv("DAY_COOLDOWN_MAX", "70"))
    EVENING_COOLDOWN_MIN: int = int(os.getenv("EVENING_COOLDOWN_MIN", "20"))
    EVENING_COOLDOWN_MAX: int = int(os.getenv("EVENING_COOLDOWN_MAX", "45"))
    CATCHUP_THRESHOLD_MIN: int = int(os.getenv("CATCHUP_THRESHOLD_MIN", "120"))
    MAX_CATCHUP_POSTS: int = int(os.getenv("MAX_CATCHUP_POSTS", "4"))
    PULL_INTERVAL_MIN: int = int(os.getenv("PULL_INTERVAL_MIN", "5"))

def get_random_outro() -> Optional[str]:
    """Finds all outro videos in resources/outros or resources/ and returns a random choice."""
    candidates = []
    if os.path.isdir(settings.OUTROS_PATH):
        for f in os.listdir(settings.OUTROS_PATH):
            if f.lower().endswith((".mp4", ".mov", ".mkv")):
                candidates.append(os.path.join(settings.OUTROS_PATH, f))
    if os.path.isdir(settings.RESOURCES_PATH):
        for f in os.listdir(settings.RESOURCES_PATH):
            if f.lower().startswith("outro") and f.lower().endswith((".mp4", ".mov", ".mkv")):
                candidates.append(os.path.join(settings.RESOURCES_PATH, f))

    if candidates:
        chosen = random.choice(candidates)
        print(f"[Resources] Selected random outro: {os.path.basename(chosen)} out of {len(candidates)} outro(s)")
        return chosen
    return None

def get_watermark_path() -> Optional[str]:
    """Returns the watermark path if watermark.png exists in resources."""
    if os.path.isfile(settings.WATERMARK_PATH):
        return settings.WATERMARK_PATH
    return None

def get_cookies_path() -> Optional[str]:
    """Returns the cookies file path if cookies.txt exists."""
    if os.path.isfile(settings.COOKIES_PATH):
        return settings.COOKIES_PATH
    res_cookies = os.path.join(settings.RESOURCES_PATH, "cookies.txt")
    if os.path.isfile(res_cookies):
        return res_cookies
    return None

def get_tiktok_cookies_path() -> Optional[str]:
    """Returns the TikTok cookies file path if tiktok_cookies.txt exists."""
    if os.path.isfile(settings.TIKTOK_COOKIES_PATH):
        return settings.TIKTOK_COOKIES_PATH
    return None

def is_youtube_configured() -> bool:
    """Checks if YouTube OAuth token file exists and is readable."""
    return os.path.isfile(settings.YOUTUBE_TOKEN_FILE)

def is_tiktok_configured() -> bool:
    """Checks if TikTok access token, session ID, or cookies exist."""
    return bool(settings.TIKTOK_ACCESS_TOKEN or settings.TIKTOK_SESSION_ID or os.path.isfile(settings.TIKTOK_COOKIES_PATH))

def save_settings_to_env(updates: Dict[str, Any]) -> None:
    """Updates .env file and syncs in-memory settings object immediately."""
    current_lines = []
    if ENV_FILE.is_file():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            current_lines = f.readlines()

    updated_keys = set()
    new_lines = []

    for line in current_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_val = str(updates[k]).replace("\n", "\\n")
                new_lines.append(f"{k}={new_val}\n")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_val = str(v).replace("\n", "\\n")
            new_lines.append(f"{k}={new_val}\n")

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    # Re-apply to os.environ and settings instance
    for k, v in updates.items():
        str_val = str(v)
        os.environ[k] = str_val
        if hasattr(settings, k):
            target_type = type(getattr(settings, k))
            try:
                if target_type == bool:
                    setattr(settings, k, str_val.lower() in ("true", "1"))
                elif target_type == int:
                    setattr(settings, k, int(str_val))
                else:
                    setattr(settings, k, str_val)
            except Exception as e:
                print(f"[Config] Error setting attribute {k}: {e}")

settings = Settings()
