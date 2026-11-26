import os
from typing import Dict, Any, Optional
try:
    from core.config import settings
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args
except ImportError:
    from backend.core.config import settings
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args

def process_youtube_shorts_video(
    input_path: str,
    output_path: str,
    max_duration_sec: float = 59.5,
    normalize_audio: bool = True,
    watermark_path: Optional[str] = None,
    watermark_alpha: float = 0.85,
    anti_fingerprint: bool = True,
    target_width: int = 1080,
    target_height: int = 1920,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders a video tailored for YouTube Shorts:
    1. 9:16 canvas with blurred background (fast downscaled blur).
    2. Anti-Fingerprint Shield (micro-zoom, color grade, film noise, acoustic dither).
    3. Strict max duration enforcement (<=60s) for Shorts shelf compliance.
    4. Audio normalization to -14 LUFS standard.
    5. Optional watermark overlay inside safe-zone.
    6. Hardware accelerated encoding (NVENC / fallback).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    info = probe_video(input_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 1. Video canvas & anti-fingerprint filters
    if anti_fingerprint:
        fg_filter = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"scale=iw*1.02:ih*1.02,crop=iw/1.02:ih/1.02,"
            f"eq=contrast=1.03:brightness=0.008:saturation=1.04,"
            f"noise=alls=2:allf=t+u[fg];"
        )
    else:
        fg_filter = f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fg];"

    filter_complex = (
        f"[0:v]scale=270:480:force_original_aspect_ratio=increase,"
        f"crop=270:480,boxblur=6:2,scale={target_width}:{target_height}[bg];"
        f"{fg_filter}"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )
    last_v = "[base]"

    args = ["-i", input_path]
    current_input_index = 1

    # Optional watermark overlay
    if watermark_path and os.path.isfile(watermark_path):
        args.extend(["-loop", "1", "-i", watermark_path])
        filter_complex += (
            f";[{current_input_index}:v]format=rgba,scale=240:-1,colorchannelmixer=aa={watermark_alpha}[wm];"
            f"{last_v}[wm]overlay=50:H-h-350:shortest=1[v_wm]"
        )
        last_v = "[v_wm]"
        current_input_index += 1

    # Handle duration trim
    trim_args = []
    if info["duration"] > max_duration_sec:
        trim_args = ["-t", str(max_duration_sec)]

    # Handle audio & loudnorm with acoustic anti-fingerprinting
    if not info["has_audio"]:
        args.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_filter = "aformat=sample_rates=48000:channel_layouts=stereo"
        map_audio = f"{current_input_index}:a"
    else:
        map_audio = "0:a"
        if anti_fingerprint:
            audio_filter = (
                "atempo=1.008,equalizer=f=1200:width_type=q:width=1.5:g=0.6,"
                "loudnorm=I=-14:LRA=11:TP=-1.5,aformat=sample_rates=48000:channel_layouts=stereo"
                if normalize_audio else
                "atempo=1.008,equalizer=f=1200:width_type=q:width=1.5:g=0.6,aformat=sample_rates=48000:channel_layouts=stereo"
            )
        else:
            audio_filter = (
                "loudnorm=I=-14:LRA=11:TP=-1.5,aformat=sample_rates=48000:channel_layouts=stereo"
                if normalize_audio else
                "aformat=sample_rates=48000:channel_layouts=stereo"
            )

    filter_complex += f";[{map_audio}]{audio_filter}[outa]"
    encoder_args = get_fast_encoder_args(quality=20)

    cmd = args + trim_args + [
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", "[outa]"
    ] + encoder_args + [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "48000",
        "-ac", "2",
        output_path
    ]

    run_ffmpeg(cmd, job_id=job_id)

    rendered_info = probe_video(output_path)
    return {
        "output_path": output_path,
        "duration": rendered_info["duration"],
        "width": rendered_info["width"],
        "height": rendered_info["height"],
        "size_bytes": rendered_info["size_bytes"]
    }
