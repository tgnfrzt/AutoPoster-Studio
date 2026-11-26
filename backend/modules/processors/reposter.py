import os
import math
from typing import Dict, Any, Optional
try:
    from core.config import settings
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from utils.antifingerprint import (
        get_antifingerprint_audio_filter,
        get_antifingerprint_ffmpeg_args
    )
except ImportError:
    from backend.core.config import settings
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from backend.utils.antifingerprint import (
        get_antifingerprint_audio_filter,
        get_antifingerprint_ffmpeg_args
    )

def process_repost_with_heavy_watermark(
    input_path: str,
    output_path: str,
    watermark_path: Optional[str] = None,
    watermark_alpha: float = 1.0,
    watermark_scale: int = 340,
    outro_path: Optional[str] = None,
    anti_fingerprint: bool = True,
    tilt_deg: float = 1.8,
    target_width: int = 1080,
    target_height: int = 1920,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders an Instagram Reel for reposting with a prominent/heavy watermark:
    1. Conforms to 1080x1920 (9:16).
    2. Applies Extreme Anti-Fingerprint Shield (1.8 deg tilt, 1.07x zoom, curves, film noise, vignette, 1.03x speed, pitch shift).
    3. Overlays watermark with reinforced contrast (heavy opacity + safe-zone placement).
    4. Seamlessly splices outro if provided.
    5. Strips metadata and enforces bitexact encoding.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    info = probe_video(input_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    intermediate_render = output_path if not (outro_path and os.path.isfile(outro_path)) else output_path + ".temp.mp4"

    # Base 9:16 canvas with blurred background & Extreme Anti-Fingerprinting
    if anti_fingerprint:
        rad = tilt_deg * math.pi / 180.0
        fg_filter = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
            f"rotate={rad:.5f}:ow=rotw({rad:.5f}):oh=roth({rad:.5f}):c=black,"
            f"scale=iw*1.07:ih*1.07,crop=iw/1.07:ih/1.07,"
            f"eq=contrast=1.07:brightness=0.012:saturation=1.08:gamma=1.03,"
            f"colorbalance=rs=0.03:gs=-0.01:bs=0.02:rm=0.02:gm=-0.01:bm=0.03,"
            f"vignette=PI/5,"
            f"noise=alls=6:allf=t+u,"
            f"setpts=PTS/1.03[fg];"
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

    # Heavy watermark filter with reinforced visibility
    if watermark_path and os.path.isfile(watermark_path):
        inputs.extend(["-i", watermark_path])
        # Scale watermark and keep alpha high (capped at 1.0)
        alpha = min(max(watermark_alpha, 0.5), 1.0)
        filter_complex += (
            f";[{current_input_index}:v]format=rgba,scale={watermark_scale}:-1,"
            f"colorchannelmixer=aa={alpha}[wm];"
            f"{last_v}[wm]overlay=(W-w)/2:H-h-430[v_wm]"
        )
        last_v = "[v_wm]"
        current_input_index += 1

    args = list(inputs)
    if not info["has_audio"]:
        args.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_in = f"{current_input_index}:a"
    else:
        if anti_fingerprint:
            filter_complex += f";" + get_antifingerprint_audio_filter(input_tag="0:a", output_tag="outa", speed=1.03, pitch_factor=1.012)
            audio_in = "[outa]"
        else:
            audio_in = "0:a"

    encoder_args = get_fast_encoder_args(quality=21)

    args.extend([
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", audio_in
    ] + encoder_args + [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-shortest"
    ] + get_antifingerprint_ffmpeg_args() + [
        intermediate_render
    ])

    run_ffmpeg(args, job_id=job_id)

    final_output = output_path
    if outro_path and os.path.isfile(outro_path):
        concat_args = [
            "-i", intermediate_render,
            "-i", outro_path,
            "-filter_complex",
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0];"
            f"[1:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1];"
            f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
            f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
            f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]",
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            final_output
        ]
        run_ffmpeg(concat_args, job_id=job_id)
        if os.path.isfile(intermediate_render):
            os.remove(intermediate_render)

    rendered_info = probe_video(final_output)
    return {
        "output_path": final_output,
        "duration": rendered_info["duration"],
        "width": rendered_info["width"],
        "height": rendered_info["height"],
        "size_bytes": rendered_info["size_bytes"]
    }
