import os
import math
from typing import Dict, Any, Optional
try:
    from core.config import settings, get_random_outro, get_watermark_path
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from utils.antifingerprint import (
        get_antifingerprint_video_filter,
        get_antifingerprint_audio_filter,
        get_antifingerprint_ffmpeg_args
    )
except ImportError:
    from backend.core.config import settings, get_random_outro, get_watermark_path
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
    from backend.utils.antifingerprint import (
        get_antifingerprint_video_filter,
        get_antifingerprint_audio_filter,
        get_antifingerprint_ffmpeg_args
    )

def process_instagram_video(
    input_path: str,
    output_path: str,
    watermark_path: Optional[str] = None,
    outro_path: Optional[str] = None,
    watermark_alpha: float = 1.0,
    stretch_to_9x16: bool = True,
    anti_fingerprint: bool = True,
    tilt_deg: float = 1.8,
    target_width: int = 1080,
    target_height: int = 1920,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Renders an Instagram Reel according to specifications:
    1. Stretches / scales to 9:16 (1080x1920) at 30 fps.
    2. Applies Extreme Anti-Fingerprint Shield (1.8 deg tilt, 1.07x zoom, curves, film noise, vignette, 1.03x speed, pitch shift).
    3. Overlays watermark.png across the entire video (from resources/watermark.png).
    4. Selects a random outro from resources/outros/ (or specified outro) and concatenates it.
    5. Strips all container/device metadata and enforces bitexact encoding.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Resolve watermark and outro from resources if not explicitly supplied
    wm_path = watermark_path or get_watermark_path()
    out_path = outro_path or get_random_outro()

    info = probe_video(input_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    intermediate_render = output_path if not (out_path and os.path.isfile(out_path)) else output_path + ".temp.mp4"

    # Step 1: Video scaling/stretching to 9:16 with Extreme Anti-Fingerprinting
    if stretch_to_9x16:
        if anti_fingerprint:
            scale_filter = get_antifingerprint_video_filter(
                input_tag="0:v",
                output_tag="vbase",
                target_width=target_width,
                target_height=target_height,
                tilt_deg=tilt_deg,
                zoom=1.07,
                speed=1.03
            )
        else:
            scale_filter = f"[0:v]scale={target_width}:{target_height},setsar=1,fps=30,format=yuv420p[vbase]"
    else:
        # Blurred background canvas fallback with tilted/zoomed foreground
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

        scale_filter = (
            f"[0:v]scale=270:480:force_original_aspect_ratio=increase,"
            f"crop=270:480,boxblur=6:2,scale={target_width}:{target_height}[bg];"
            f"{fg_filter}"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[vbase]"
        )

    filter_complex = scale_filter
    last_v = "[vbase]"

    inputs = ["-i", input_path]
    current_input_index = 1

    # Step 2: Overlay watermark across entire video if watermark.png exists
    if wm_path and os.path.isfile(wm_path):
        inputs.extend(["-loop", "1", "-i", wm_path])
        print(f"[InstagramProcessor] Applying watermark: {os.path.basename(wm_path)}")
        filter_complex += (
            f";[{current_input_index}:v]format=rgba,colorchannelmixer=aa={watermark_alpha}[wm];"
            f"{last_v}[wm]overlay=0:0:format=auto:shortest=1[v_wm]"
        )
        last_v = "[v_wm]"
        current_input_index += 1

    # Audio handling: ensure stereo audio stream exists + acoustic anti-fingerprint dither
    args = list(inputs)
    if not info["has_audio"]:
        args.extend(["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo"])
        audio_in = f"{current_input_index}:a"
    else:
        if anti_fingerprint:
            filter_complex += f";" + get_antifingerprint_audio_filter(input_tag="0:a", output_tag="outa", speed=1.03, pitch_factor=1.012)
            audio_in = "[outa]"
        else:
            audio_in = "0:a"

    encoder_args = get_fast_encoder_args(quality=22)

    args.extend([
        "-filter_complex", filter_complex,
        "-map", last_v,
        "-map", audio_in
    ] + encoder_args + [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest"
    ] + get_antifingerprint_ffmpeg_args() + [
        intermediate_render
    ])

    run_ffmpeg(args, job_id=job_id)

    # Step 3: Concat Outro if available
    final_output = output_path
    if out_path and os.path.isfile(out_path):
        print(f"[InstagramProcessor] Splicing outro: {os.path.basename(out_path)}...")
        concat_args = [
            "-i", intermediate_render,
            "-i", out_path,
            "-filter_complex",
            f"[0:v]scale={target_width}:{target_height},setsar=1,fps=30[v0];"
            f"[1:v]scale={target_width}:{target_height},setsar=1,fps=30[v1];"
            f"[0:a]aresample=44100:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
            f"[1:a]aresample=44100:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
            f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]",
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
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
        "outro_used": os.path.basename(out_path) if out_path else None,
        "watermark_applied": os.path.basename(wm_path) if wm_path else None
    }
