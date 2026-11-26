import os
import random
import subprocess
from typing import Dict, Any, Optional, List
from core.config import settings
from utils.ffmpeg_runner import probe_video, run_ffmpeg

DEFAULT_FACTS = [
    {"hook": "Did You Know?", "text": "Octopuses have three hearts and blue blood!", "tags": "#Shorts #Facts #Nature"},
    {"hook": "Mind-Blowing Fact", "text": "Honey never spoils. Archaeologists have found 3,000-year-old honey that is still edible!", "tags": "#Shorts #DidYouKnow #Interesting"},
    {"hook": "Crazy Science", "text": "A day on Venus is longer than a year on Venus!", "tags": "#Shorts #Space #Science"},
    {"hook": "Quick Fact", "text": "Bananas are curved because they grow towards the sun against gravity!", "tags": "#Shorts #Trending #Facts"}
]

def generate_color_background(output_path: str, duration: float = 30.0, width: int = 1080, height: int = 1920) -> str:
    """Generates a dynamic 9:16 gradient/ambient background when no video asset is provided."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "-f", "lavfi",
        "-i", f"color=c=0x111827:s={width}x{height}:d={duration}",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        output_path
    ]
    run_ffmpeg(cmd)
    return output_path

def generate_short_video(
    output_path: str,
    title: Optional[str] = None,
    body_text: Optional[str] = None,
    background_video_path: Optional[str] = None,
    audio_track_path: Optional[str] = None,
    duration_sec: float = 25.0,
    target_width: int = 1080,
    target_height: int = 1920
) -> Dict[str, Any]:
    """
    Generates a complete YouTube Short video:
    1. Uses background video loop or ambient canvas.
    2. Overlays styled title hook + body caption inside Shorts safe zones.
    3. Normalizes audio to -14 LUFS standard.
    4. Guarantees 9:16 vertical and duration <= 59.5s.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not title or not body_text:
        sample = random.choice(DEFAULT_FACTS)
        title = title or sample["hook"]
        body_text = body_text or sample["text"]

    clean_title = title.replace("'", "").replace(":", "-")
    clean_body = body_text.replace("'", "").replace(":", "-")

    bg_path = background_video_path
    temp_bg = None
    if not bg_path or not os.path.isfile(bg_path):
        temp_bg = output_path + ".bg.mp4"
        bg_path = generate_color_background(temp_bg, duration=duration_sec, width=target_width, height=target_height)

    # Filter graph: Scale background to 9:16 + draw Hook Header + draw Body Card
    # Safe zone: Title placed at y=480, Body text centered at y=860
    filter_complex = (
        f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height}[v0];"
        f"[v0]drawtext=text='{clean_title}':"
        f"fontsize=56:fontcolor=0xfacc15:x=(w-text_w)/2:y=480:"
        f"box=1:boxcolor=black@0.75:boxborderw=20[v1];"
        f"[v1]drawtext=text='{clean_body}':"
        f"fontsize=42:fontcolor=white:x=(w-text_w)/2:y=860:"
        f"box=1:boxcolor=black@0.7:boxborderw=18[v_out]"
    )

    inputs = ["-t", str(min(duration_sec, 59.0)), "-i", bg_path]
    audio_map = "0:a"

    if audio_track_path and os.path.isfile(audio_track_path):
        inputs.extend(["-t", str(min(duration_sec, 59.0)), "-i", audio_track_path])
        audio_map = "1:a"

    cmd = inputs + [
        "-filter_complex", filter_complex,
        "-map", "[v_out]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
        output_path
    ]

    run_ffmpeg(cmd)

    if temp_bg and os.path.isfile(temp_bg):
        os.remove(temp_bg)

    rendered_info = probe_video(output_path)
    return {
        "output_path": output_path,
        "title": f"{clean_title} #Shorts",
        "description": f"{clean_body}\n\n#Shorts #Viral #Trending",
        "duration": rendered_info["duration"],
        "width": rendered_info["width"],
        "height": rendered_info["height"]
    }
