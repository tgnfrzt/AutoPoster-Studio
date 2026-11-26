import os
import math
import uuid
from typing import List, Dict, Any, Optional
try:
    from core.config import settings, get_watermark_path
    from utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available
except ImportError:
    from backend.core.config import settings, get_watermark_path
    from backend.utils.ffmpeg_runner import probe_video, run_ffmpeg, get_fast_encoder_args, is_nvenc_available

def split_and_conform_to_parts(
    input_path: str,
    output_dir: str,
    series_id: Optional[str] = None,
    chunk_duration_sec: float = 150.0,
    add_part_banner: bool = True,
    watermark_path: Optional[str] = None,
    watermark_alpha: float = 0.9,
    anti_fingerprint: bool = True,
    target_width: int = 1080,
    target_height: int = 1920,
    job_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Chops a long video into sequential mini-videos named {series_id}part1.mp4, {series_id}part2.mp4, etc.
    Conforms each part to 9:16 vertical canvas (1080x1920) with blurred background padding,
    overlays a clean 'PART X OF N' banner for TikTok series indexing,
    and applies anti-fingerprinting shields (color EQ, micro-zoom, noise dither & watermark).
    Accelerated with NVIDIA GTX 1650 NVENC hardware encoding and 16x faster scale-down background blur.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input video not found: {input_path}")

    wm_path = watermark_path or get_watermark_path()

    # Generate a clean series identifier if none provided (e.g. tt_a1b2c3)
    s_id = series_id or f"tt_{uuid.uuid4().hex[:6]}"

    info = probe_video(input_path)
    total_duration = info["duration"]
    if total_duration <= 0:
        raise ValueError(f"Invalid video duration: {total_duration}")

    os.makedirs(output_dir, exist_ok=True)
    num_parts = max(1, math.ceil(total_duration / chunk_duration_sec))
    chunks = []

    for i in range(1, num_parts + 1):
        start_time = (i - 1) * chunk_duration_sec
        # Don't create a trailing micro-part (< 15 seconds) if near the end
        if i == num_parts and (total_duration - start_time) < 15.0 and i > 1:
            break

        duration = min(chunk_duration_sec, total_duration - start_time)
        part_filename = f"{s_id}part{i}.mp4"
        part_output_path = os.path.join(output_dir, part_filename)

        # Smart resume: preserve and skip parts already rendered on disk
        if os.path.isfile(part_output_path) and os.path.getsize(part_output_path) > 1000000:
            print(f"[Splitter] Part {i}/{num_parts} ({part_filename}) already rendered on disk ({os.path.getsize(part_output_path)/(1024*1024):.1f}MB), skipping...")
            try:
                part_info = probe_video(part_output_path)
                chunks.append({
                    "series_id": s_id,
                    "part_number": i,
                    "total_parts": num_parts,
                    "filename": part_filename,
                    "output_path": part_output_path,
                    "duration": part_info["duration"],
                    "start_time": start_time
                })
                continue
            except Exception:
                pass

        # 1. Video Extreme Anti-Fingerprint & Canvas filters
        if anti_fingerprint:
            rad = 1.8 * math.pi / 180.0
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

        # Fast downscaled background blur (renders blur at 270x480, upscales to 1080x1920: 16x faster)
        scale_filter = (
            f"[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
            f"scale={target_width}:{target_height}[bg];"
            f"{fg_filter}"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[canvas]"
        )
        last_v = "[canvas]"

        inputs = ["-ss", str(start_time), "-t", str(duration), "-i", input_path]
        current_input_idx = 1

        # 2. Watermark overlay across video (deterrent & copyright protection)
        if wm_path and os.path.isfile(wm_path):
            inputs.extend(["-loop", "1", "-i", wm_path])
            filter_complex = scale_filter + (
                f";[{current_input_idx}:v]format=rgba,colorchannelmixer=aa={watermark_alpha},scale=260:-1[wm];"
                f"{last_v}[wm]overlay=60:H-h-420:shortest=1[v_wm]"
            )
            last_v = "[v_wm]"
            current_input_idx += 1
        else:
            filter_complex = scale_filter

        # 3. Dynamic "PART X OF N" banner overlay
        if add_part_banner:
            banner_text = f"PART {i}" if num_parts == 1 else f"PART {i} OF {num_parts}"
            filter_complex += (
                f";{last_v}drawtext=text='{banner_text}':"
                f"fontsize=46:fontcolor=white:x=(w-text_w)/2:y=180:"
                f"box=1:boxcolor=black@0.65:boxborderw=14[v_banner]"
            )
            last_v = "[v_banner]"

        # 4. Audio Extreme Anti-Fingerprinting (pitch + tempo + harmonic EQ)
        if not info["has_audio"]:
            inputs.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
            audio_map = f"{current_input_idx}:a"
        else:
            if anti_fingerprint:
                filter_complex += f";[0:a]asetrate=48000*1.012,aresample=48000,atempo=1.0178,equalizer=f=800:width_type=q:width=2:g=1.2,equalizer=f=3200:width_type=q:width=1.5:g=-1.0[outa]"
                audio_map = "[outa]"
            else:
                audio_map = "0:a"

        # Hardware-accelerated GPU encoder with CPU fallback
        encoder_args = get_fast_encoder_args(quality=21, preset="p4")

        cmd = inputs + [
            "-filter_complex", filter_complex,
            "-map", last_v,
            "-map", audio_map
        ] + encoder_args + [
            "-t", str(duration),
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-fflags", "+bitexact",
            "-flags", "+bitexact",
            part_output_path
        ]

        gpu_status = "GTX 1650 NVENC" if is_nvenc_available() else "CPU libx264"
        print(f"[Splitter] Rendering {part_filename} (Part {i}/{num_parts}, {duration:.1f}s, Anti-Fingerprint: {anti_fingerprint}, Encoder: {gpu_status})...")
        run_ffmpeg(cmd, job_id=job_id)

        part_info = probe_video(part_output_path)
        chunks.append({
            "series_id": s_id,
            "part_number": i,
            "total_parts": num_parts,
            "filename": part_filename,
            "output_path": part_output_path,
            "duration": part_info["duration"],
            "start_time": start_time
        })

    return chunks
