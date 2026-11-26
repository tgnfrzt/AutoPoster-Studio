import os
import random
import subprocess
from typing import List, Dict, Any, Optional

BACKGROUNDS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources",
    "backgrounds"
)

CATEGORIES = {
    "subway_surfers": {
        "label": "🏃 Subway Surfers (High Retention)",
        "description": "Fast-paced vertical subway gameplay loop",
        "sample_url": "https://www.youtube.com/watch?v=0k55-G2d480"
    },
    "minecraft_parkour": {
        "label": "⛏️ Minecraft Parkour",
        "description": "Satisfying continuous spiral jump parkour",
        "sample_url": "https://www.youtube.com/watch?v=n_Dv4JMiwK8"
    },
    "gta_stunts": {
        "label": "🏎️ GTA 5 Mega Ramp Stunts",
        "description": "Dynamic high-speed car stunt sequences",
        "sample_url": "https://www.youtube.com/watch?v=kYJdJzWkEis"
    },
    "creepy_ambient": {
        "label": "🌧️ Creepy Ambient (Rain, Fog, Night)",
        "description": "Dark atmosphere, windshield rain, dark forest for horror tone",
        "sample_url": "https://www.youtube.com/watch?v=q76bEAU68Fk"
    }
}

def get_category_dir(category: str) -> str:
    path = os.path.join(BACKGROUNDS_ROOT, category)
    os.makedirs(path, exist_ok=True)
    return path

def list_backgrounds() -> Dict[str, Any]:
    """Returns available background categories and count of files in each."""
    result = {}
    for cat_id, info in CATEGORIES.items():
        cat_dir = get_category_dir(cat_id)
        files = [f for f in os.listdir(cat_dir) if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))]
        result[cat_id] = {
            "label": info["label"],
            "description": info["description"],
            "file_count": len(files),
            "files": files,
            "has_media": len(files) > 0
        }
    return result

def get_random_background(category: str = "subway_surfers") -> Optional[str]:
    """Retrieves a random video file from the specified background category."""
    cat_dir = get_category_dir(category)
    files = [
        os.path.join(cat_dir, f)
        for f in os.listdir(cat_dir)
        if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")) and os.path.getsize(os.path.join(cat_dir, f)) > 100000
    ]
    if files:
        return random.choice(files)
    return None

def generate_fallback_ambient(output_path: str, duration_sec: float = 60.0, is_vertical: bool = True) -> str:
    """Generates an aesthetic animated dark horror/rain visual if no background files exist."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    w, h = (1080, 1920) if is_vertical else (1920, 1080)
    
    # Generate moody dark stormy canvas with noise and vignette
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0a0c10:s={w}x{h}:d={duration_sec}:r=30",
        "-vf", "noise=alls=12:allf=t+u,eq=contrast=1.1:brightness=-0.02,vignette=PI/4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        output_path
    ]
    subprocess.run(cmd, capture_output=True)
    return output_path
