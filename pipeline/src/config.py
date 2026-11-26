import os
import shutil
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PIPELINE_DIR = BASE_DIR / "pipeline"
ASSETS_DIR = BASE_DIR / "assets"
GAMEPLAY_DIR = ASSETS_DIR / "gameplay"
AMBIENT_DIR = ASSETS_DIR / "ambient"
FONTS_DIR = ASSETS_DIR / "fonts"
OUTPUT_DIR = ASSETS_DIR / "output"
OUTPUT_SHORTS_DIR = OUTPUT_DIR / "shorts"
OUTPUT_LONGFORM_DIR = OUTPUT_DIR / "longform"

# Ensure all asset directories exist
for d in (ASSETS_DIR, GAMEPLAY_DIR, AMBIENT_DIR, FONTS_DIR, OUTPUT_DIR, OUTPUT_SHORTS_DIR, OUTPUT_LONGFORM_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Binaries
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"

# Gemini API Key
def get_gemini_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        env_file = BASE_DIR / ".env"
        if env_file.is_file():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY="):
                            key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            except Exception:
                pass
    return key

# NVENC hardware acceleration detection
_NVENC_CACHE = None

def is_nvenc_available() -> bool:
    global _NVENC_CACHE
    if _NVENC_CACHE is not None:
        return _NVENC_CACHE
    import subprocess
    try:
        cmd = [FFMPEG_PATH, "-hide_banner", "-encoders"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        _NVENC_CACHE = "h264_nvenc" in res.stdout
    except Exception:
        _NVENC_CACHE = False
    return _NVENC_CACHE
