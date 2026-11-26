import os
import math
import uuid
from typing import Dict, Any, Optional, List

try:
    from core.config import settings, get_watermark_path
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from modules.ai.tts_engine import generate_horror_narration
    from modules.ai.background_manager import get_random_background, generate_fallback_ambient
except ImportError:
    from backend.core.config import settings, get_watermark_path
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from backend.modules.ai.tts_engine import generate_horror_narration
    from backend.modules.ai.background_manager import get_random_background, generate_fallback_ambient

def compose_creepy_brainrot_short(
    story_text: str,
    output_path: str,
    voice_preset: str = "mr_nightmare",
    top_category: str = "creepy_ambient",
    bottom_category: str = "subway_surfers",
    watermark_path: Optional[str] = None,
    watermark_alpha: float = 0.85,
    anti_fingerprint: bool = True,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders a Viral Split-Screen Horror Short (9:16 vertical):
    - Top Half (1080x960): Eerie atmospheric B-roll (rain, night driving, fog, CCTV)
    - Bottom Half (1080x960): Satisfying Brainrot gameplay (Subway Surfers, Minecraft Parkour)
    - Center Audio: Deep Mr. Nightmare / Qwen3-TTS 0.6B voiceover with low-end warmth
    - Center Subtitles: Burned-in high-contrast bold subtitles with black outline
    - Encoded on NVIDIA GTX 1650 NVENC for maximum speed
    """
    work_dir = os.path.dirname(output_path)
    os.makedirs(work_dir, exist_ok=True)

    uid = uuid.uuid4().hex[:6]
    audio_path = os.path.join(work_dir, f"narr_{uid}.mp3")
    srt_path = os.path.join(work_dir, f"narr_{uid}.srt")

    # Step 1: Synthesize voiceover & subtitles
    print(f"[StoryComposer] Generating narration with voice preset '{voice_preset}'...")
    tts_res = generate_horror_narration(
        text=story_text,
        output_audio_path=audio_path,
        output_srt_path=srt_path,
        preset_name=voice_preset
    )
    duration_sec = tts_res["duration"]
    if duration_sec <= 0:
        duration_sec = 45.0

    print(f"[StoryComposer] Narration ready: {duration_sec:.1f}s. Preparing visuals...")

    # Step 2: Resolve top (ambient) and bottom (brainrot) footage
    top_video = get_random_background(top_category)
    if not top_video:
        top_fallback = os.path.join(work_dir, f"ambient_top_{uid}.mp4")
        generate_fallback_ambient(top_fallback, duration_sec=duration_sec, is_vertical=False)
        top_video = top_fallback

    bottom_video = get_random_background(bottom_category)
    if not bottom_video:
        bottom_fallback = os.path.join(work_dir, f"ambient_bot_{uid}.mp4")
        generate_fallback_ambient(bottom_fallback, duration_sec=duration_sec, is_vertical=False)
        bottom_video = bottom_fallback

    wm_path = watermark_path or get_watermark_path()

    # Step 3: Build FFmpeg Split-Screen Filtergraph (1080x1920)
    # Top: 1080x960, Bottom: 1080x960 -> vstack = 1080x1920
    filter_complex = (
        f"[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[v_top];"
        f"[1:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[v_bot];"
        f"[v_top][v_bot]vstack=inputs=2[v_split]"
    )
    last_v = "[v_split]"

    # Subtitles overlay (centered just above the seam)
    # Clean windows path slashes for FFmpeg subtitles filter
    clean_srt_path = srt_path.replace("\\", "/").replace(":", "\\:")
    filter_complex += (
        f";{last_v}subtitles='{clean_srt_path}':"
        f"force_style='FontName=Impact,FontSize=26,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Alignment=2,MarginV=50'[v_sub]"
    )
    last_v = "[v_sub]"

    # Audio filter: Low-end warmth + loudness normalization
    audio_filter = "equalizer=f=120:width_type=q:width=1.0:g=3.0,loudnorm=I=-14:LRA=10:TP=-1.5[outa]"

    cmd_inputs = [
        "-stream_loop", "-1", "-i", top_video,
        "-stream_loop", "-1", "-i", bottom_video,
        "-i", audio_path
    ]
    current_input_idx = 3

    # Optional Watermark
    if wm_path and os.path.isfile(wm_path):
        cmd_inputs.extend(["-loop", "1", "-i", wm_path])
        filter_complex += (
            f";[{current_input_idx}:v]format=rgba,colorchannelmixer=aa={watermark_alpha},scale=240:-1[wm];"
            f"{last_v}[wm]overlay=50:H-h-350:shortest=1[v_wm]"
        )
        last_v = "[v_wm]"

    filter_complex += f";[2:a]{audio_filter}"

    encoder_args = get_fast_encoder_args(quality=21, preset="p4")

    cmd = cmd_inputs + [
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", "[outa]"
    ] + encoder_args + [
        "-t", str(duration_sec),
        "-shortest",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        output_path
    ]

    print(f"[StoryComposer] Rendering Split-Screen Short to {output_path} (NVENC Accelerated)...")
    run_ffmpeg(cmd, job_id=job_id)

    # Cleanup temporary TTS files
    for p in (audio_path, srt_path):
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass

    rendered_info = probe_video(output_path)
    return {
        "output_path": output_path,
        "duration": rendered_info["duration"],
        "width": rendered_info["width"],
        "height": rendered_info["height"],
        "size_bytes": rendered_info["size_bytes"],
        "voice_used": voice_preset
    }
