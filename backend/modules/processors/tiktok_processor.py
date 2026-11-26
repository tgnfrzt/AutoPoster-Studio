import os
from typing import Dict, Any, Optional
try:
    from core.config import settings, get_watermark_path
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args
except ImportError:
    from backend.core.config import settings, get_watermark_path
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args

def process_tiktok_video(
    input_path: str,
    output_path: str,
    watermark_path: Optional[str] = None,
    watermark_alpha: float = 0.9,
    anti_fingerprint: bool = True,
    target_width: int = 1080,
    target_height: int = 1920,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders a video tailored for TikTok:
    1. 9:16 vertical canvas with blurred background (fast downscaled blur).
    2. Overlays watermark from resources/watermark.png.
    3. Applies anti-fingerprinting shields (color EQ, micro-zoom, noise dither & acoustic dither).
    4. Respects right-hand interaction gutter (keeps overlays left/centered).
    5. Hardware-accelerated GPU encoder with CPU fallback, fast start (+faststart).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    wm_path = watermark_path or get_watermark_path()

    info = probe_video(input_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

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
    inputs = ["-i", input_path]
    current_input_index = 1

    if wm_path and os.path.isfile(wm_path):
        inputs.extend(["-loop", "1", "-i", wm_path])
        filter_complex += (
            f";[{current_input_index}:v]format=rgba,colorchannelmixer=aa={watermark_alpha},scale=260:-1[wm];"
            f"{last_v}[wm]overlay=60:H-h-420:shortest=1[v_wm]"
        )
        last_v = "[v_wm]"
        current_input_index += 1

    args = list(inputs)
    if not info["has_audio"]:
        args.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_in = f"{current_input_index}:a"
    else:
        if anti_fingerprint:
            filter_complex += f";[0:a]atempo=1.008,equalizer=f=1200:width_type=q:width=1.5:g=0.6[outa]"
            audio_in = "[outa]"
        else:
            audio_in = "0:a"

    encoder_args = get_fast_encoder_args(quality=21)

    args.extend([
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", audio_in
    ] + encoder_args + [
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        output_path
    ])

    run_ffmpeg(args, job_id=job_id)

    rendered_info = probe_video(output_path)
    return {
        "output_path": output_path,
        "duration": rendered_info["duration"],
        "width": rendered_info["width"],
        "height": rendered_info["height"],
        "size_bytes": rendered_info["size_bytes"]
    }
