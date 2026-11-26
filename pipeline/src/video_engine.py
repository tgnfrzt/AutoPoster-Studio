import os
import random
import subprocess
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import (
    FFMPEG_PATH, FFPROBE_PATH, GAMEPLAY_DIR, AMBIENT_DIR, ASSETS_DIR,
    OUTPUT_SHORTS_DIR, OUTPUT_LONGFORM_DIR, is_nvenc_available
)
from .visual_engine import get_scene_visual, generate_pollinations_image, resolve_scene_visual_asset
from .tts_engine import get_audio_duration

SFX_DIR = ASSETS_DIR / "sfx"

def escape_ffmpeg_path(path: str) -> str:
    """Escapes Windows paths for FFmpeg filtergraphs (e.g., ass/subtitles filter)."""
    abs_path = os.path.abspath(path).replace("\\", "/")
    return abs_path.replace(":", "\\:")

def get_horror_serif_font() -> str:
    """Returns escaped FFmpeg path to Georgia or Times New Roman serif font."""
    candidates = [
        r"C:\Windows\Fonts\georgia.ttf",
        r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\palabi.ttf",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c.replace("\\", "/").replace(":", "\\:")
    return ""

def escape_drawtext(text: str) -> str:
    """Escapes special characters for FFmpeg drawtext filter and truncates on word boundary."""
    if not text:
        return ""
    clean = (
        text.replace("\\", "")
        .replace("'", "\u2019")
        .replace('"', "")
        .replace(":", " -")
        .replace("%", "")
        .replace(";", ",")
    ).strip()
    if len(clean) > 68:
        clean = clean[:65].rsplit(" ", 1)[0] + "..."
    return clean

def get_or_create_ambient(duration_needed: float = 300.0, preferred_track: Optional[str] = None) -> str:
    """Selects a real downloaded Mr. Nightmare horror ambient track from assets/ambient."""
    if preferred_track and os.path.isfile(preferred_track):
        return preferred_track

    if preferred_track and os.path.isdir(AMBIENT_DIR):
        cand = os.path.join(AMBIENT_DIR, os.path.basename(preferred_track))
        if os.path.isfile(cand):
            return cand

    default_primary = os.path.join(AMBIENT_DIR, "creepy_stories_nightmare_ambience.mp3")
    if os.path.isfile(default_primary) and os.path.getsize(default_primary) > 100_000:
        return default_primary

    candidates = []
    if os.path.isdir(AMBIENT_DIR):
        for f in sorted(os.listdir(AMBIENT_DIR)):
            if f.lower().endswith((".mp3", ".wav", ".aac", ".ogg")) and "procedural" not in f.lower():
                full_p = os.path.join(AMBIENT_DIR, f)
                if os.path.getsize(full_p) > 100_000:
                    candidates.append(full_p)

    if candidates:
        return candidates[0]

    raise FileNotFoundError(f"No real ambient horror audio files found in {AMBIENT_DIR}")

def get_video_duration(file_path: str) -> float:
    """Uses ffprobe to obtain exact video duration in seconds."""
    try:
        cmd = [
            FFPROBE_PATH, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(res.stdout.strip())
    except Exception:
        return 60.0

def get_or_create_gameplay(duration_needed: float = 60.0) -> str:
    """Finds a real gameplay video in assets/gameplay."""
    candidates = []
    if os.path.isdir(GAMEPLAY_DIR):
        for f in os.listdir(GAMEPLAY_DIR):
            if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm")) and not f.startswith("sample_fallback"):
                full_p = os.path.join(GAMEPLAY_DIR, f)
                if os.path.getsize(full_p) > 200_000:
                    candidates.append(full_p)

    if candidates:
        return random.choice(candidates)

    raise FileNotFoundError(f"No gameplay video found in {GAMEPLAY_DIR}")

def compose_short_video(
    narration_audio_path: str,
    ass_subtitle_path: str,
    image_prompt: str,
    output_path: Optional[str] = None,
    gameplay_path: Optional[str] = None
) -> str:
    """Composes a vertical 9:16 Split-Screen Viral Short with word-level highlighted ASS subtitles."""
    duration = get_audio_duration(narration_audio_path)
    if duration <= 0:
        duration = 50.0

    if not output_path:
        uid = uuid.uuid4().hex[:8]
        output_path = str(OUTPUT_SHORTS_DIR / f"short_{uid}.mp4")

    work_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(work_dir, exist_ok=True)
    top_img_path = os.path.join(work_dir, f"top_{uuid.uuid4().hex[:6]}.jpg")

    # Use real curated dark horror photography for top half (instant, zero Cloudflare timeouts)
    get_scene_visual(
        image_prompt=image_prompt,
        output_path=top_img_path,
        mode="local_library",
        scene_index=random.randint(0, 13),
        width=1080,
        height=960
    )

    gp_path = gameplay_path if (gameplay_path and os.path.isfile(gameplay_path)) else get_or_create_gameplay(duration_needed=duration)
    gp_dur = get_video_duration(gp_path)
    start_time = 0.0
    if gp_dur > duration + 5:
        start_time = random.uniform(0.0, gp_dur - duration - 2)

    frames = max(30, int(round(duration * 30)))
    escaped_ass = escape_ffmpeg_path(ass_subtitle_path)
    filter_complex = (
        f"[0:v]scale=2160:1920:flags=bicubic,"
        f"zoompan=z='1.0+0.10*(on/{frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x960:fps=30,setsar=1[v_top];"
        f"[1:v]fps=30,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[v_bot];"
        f"[v_top][v_bot]vstack=inputs=2,ass='{escaped_ass}'[v_out]"
    )

    encoder_args = [
        "-c:v", "h264_nvenc", "-preset", "p3", "-tune", "hq",
        "-b:v", "4000k", "-maxrate", "6000k", "-bufsize", "8000k"
    ] if is_nvenc_available() else ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", top_img_path,
        "-stream_loop", "-1", "-ss", f"{start_time:.2f}", "-t", f"{duration:.2f}", "-i", gp_path,
        "-i", narration_audio_path,
        "-filter_complex", filter_complex,
        "-map", "[v_out]",
        "-map", "2:a",
        *encoder_args,
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{duration:.2f}",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg Short render failed: {res.stderr[-400:]}")

    if os.path.exists(top_img_path):
        try:
            os.remove(top_img_path)
        except Exception:
            pass

    return output_path

def _mix_scene_sfx(
    narration_path: str,
    sfx_path: str,
    output_audio_path: str,
    seg_dur: float,
    sfx_offset: float = 0.42,
) -> str:
    """
    Mixes a contextual foley SFX into the segment's narration audio at sfx_offset * seg_dur,
    preserving exact mono 44100Hz MP3 format so concat demuxer stays sample-aligned.
    """
    clamped_off = max(0.15, min(0.75, float(sfx_offset or 0.42)))
    delay_ms = max(250, min(int(max(0.5, seg_dur - 1.5) * 1000), int(clamped_off * seg_dur * 1000)))

    filter_complex = (
        f"[0:a]aresample=44100,aformat=channel_layouts=mono[narr];"
        f"[1:a]aresample=44100,aformat=channel_layouts=mono,atrim=0:3.8,"
        f"afade=t=in:st=0:d=0.10,afade=t=out:st=3.0:d=0.75,"
        f"adelay={delay_ms}:all=1[sfx];"
        f"[narr][sfx]amix=inputs=2:duration=first:dropout_transition=0:weights=1.0 0.20,"
        f"alimiter=limit=0.95[a_out]"
    )
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", narration_path,
        "-i", sfx_path,
        "-filter_complex", filter_complex,
        "-map", "[a_out]",
        "-ac", "1", "-ar", "44100",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_audio_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if res.returncode == 0 and os.path.isfile(output_audio_path) and os.path.getsize(output_audio_path) > 4000:
        return output_audio_path
    return narration_path


def _build_music_volume_expr(mood_windows: List[tuple]) -> str:
    """
    Coalesces contiguous music_mood windows into a flat (non-nested) FFmpeg volume expression
    so calm narration stays near room tone while tense/climax scenes gently swell creepy music.
    """
    if not mood_windows:
        return "1.0"
    merged = []
    for st, en, mood in mood_windows:
        if merged and merged[-1][2] == mood:
            merged[-1] = (merged[-1][0], en, mood)
        else:
            merged.append((st, en, mood))

    drone_terms = [f"between(t,{st:.1f},{en:.1f})" for st, en, m in merged if m == "very_low_drone"][:16]
    tense_terms = [f"between(t,{st:.1f},{en:.1f})" for st, en, m in merged if m == "low_tense_piano"][:16]

    parts = ["0.30"]
    if drone_terms:
        parts.append(f"0.35*({'+'.join(drone_terms)})")
    if tense_terms:
        parts.append(f"0.70*({'+'.join(tense_terms)})")
    return "+".join(parts)


def compose_mr_nightmare_longform(
    scene_items: List[Dict[str, Any]],
    output_path: Optional[str] = None,
    ambient_path: Optional[str] = None,
    visual_mode: str = "hybrid_director",
    progress_callback: Optional[Any] = None,
    cancel_event: Optional[Any] = None
) -> str:
    """
    Composes a broadcast-quality Mr. Nightmare 16:9 horror video (supports 45+ minute marathons):
    - Hybrid visual timeline combining real stock footage clips, local GPU AI horror scenes,
      and contextual dark night photographs (guaranteed visible, never pitch black)
    - Smooth transitions and Georgia serif chapter title cards (no bottom-right watermark)
    - Contextual foley SFX timed to story actions + very-low-volume creepy music mood curve
    """
    if not output_path:
        uid = uuid.uuid4().hex[:8]
        output_path = str(OUTPUT_LONGFORM_DIR / f"mr_nightmare_{uid}.mp4")

    work_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(work_dir, exist_ok=True)
    uid_tag = uuid.uuid4().hex[:6]
    temp_clips = []
    temp_audios = []
    temp_sfx_audios = []
    temp_ephemeral_photos = []
    mood_windows = []
    concat_vid_list = os.path.join(work_dir, f"concat_v_{uid_tag}.txt")
    concat_aud_list = os.path.join(work_dir, f"concat_a_{uid_tag}.txt")
    total_duration = 0.0

    encoder_args = [
        "-c:v", "h264_nvenc",
        "-preset", "p2",
        "-tune", "hq",
        "-b:v", "4500k",
        "-maxrate", "7000k",
        "-bufsize", "10000k"
    ] if is_nvenc_available() else [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19"
    ]

    serif_font = get_horror_serif_font()
    font_opt = f"fontfile='{serif_font}':" if serif_font else ""
    used_assets = set()
    used_clips: List[str] = []

    try:
        for idx, item in enumerate(scene_items):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            audio_path = item["audio_path"]
            seg_dur = get_audio_duration(audio_path)
            if seg_dur <= 0.1:
                seg_dur = 20.0
            seg_start = total_duration
            total_duration += seg_dur

            mood = item.get("music_mood") or "very_low_drone"
            mood_windows.append((seg_start, total_duration, mood))

            # Optional per-scene foley SFX mixing
            sfx_cue = (item.get("sfx_cue") or "none").strip()
            sfx_file = SFX_DIR / f"{sfx_cue}.mp3"
            seg_audio_to_use = audio_path
            if sfx_cue != "none" and sfx_file.is_file() and seg_dur >= 3.0:
                sfx_out = os.path.join(work_dir, f"aud_sfx_{idx}_{uid_tag}.mp3")
                mixed = _mix_scene_sfx(
                    narration_path=audio_path,
                    sfx_path=str(sfx_file),
                    output_audio_path=sfx_out,
                    seg_dur=seg_dur,
                    sfx_offset=item.get("sfx_offset", 0.42),
                )
                if mixed != audio_path:
                    seg_audio_to_use = mixed
                    temp_sfx_audios.append(mixed)

            # Resolve hybrid scene visual (stock clip OR AI image OR contextual photo)
            resolved = resolve_scene_visual_asset(
                item=item,
                work_dir=Path(work_dir),
                scene_index=idx,
                visual_mode=visual_mode,
                used_assets=used_assets,
                used_clips=used_clips,
            )
            asset_path = Path(resolved["path"])
            is_video = bool(resolved.get("is_video", False)) or asset_path.suffix.lower() == ".mp4"
            if not is_video and "scene_photo_" in asset_path.name:
                temp_ephemeral_photos.append(str(asset_path))

            fps = 24
            fade_out_st = max(0.5, seg_dur - 0.65)

            if is_video:
                filter_chain = (
                    f"scale=1920:1080:force_original_aspect_ratio=increase,"
                    f"crop=1920:1080,fps={fps},setsar=1,"
                    f"eq=brightness=-0.02:contrast=1.06:saturation=0.76,"
                    f"vignette=PI/5,"
                    f"fade=t=in:st=0:d=0.65,"
                    f"fade=t=out:st={fade_out_st:.2f}:d=0.65"
                )
                input_args = ["-stream_loop", "-1", "-t", f"{seg_dur:.3f}", "-i", str(asset_path)]
            else:
                frames = max(24, int(round(seg_dur * fps)))
                motion_type = idx % 3
                if motion_type == 0:
                    zoom_expr = (
                        f"scale=3840:2160:flags=bicubic,"
                        f"zoompan=z='1.0+0.12*(on/{frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={fps}"
                    )
                elif motion_type == 1:
                    zoom_expr = (
                        f"scale=3840:2160:flags=bicubic,"
                        f"zoompan=z='1.12':x='(iw-iw/zoom)*(0.20+0.60*(on/{frames}))':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={fps}"
                    )
                else:
                    zoom_expr = (
                        f"scale=3840:2160:flags=bicubic,"
                        f"zoompan=z='1.14-0.12*(on/{frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={fps}"
                    )
                filter_chain = (
                    f"{zoom_expr},setsar=1,"
                    f"fade=t=in:st=0:d=0.65,"
                    f"fade=t=out:st={fade_out_st:.2f}:d=0.65"
                )
                input_args = ["-i", str(asset_path)]

            # Overlay Georgia serif chapter title card on intro / story start (no bottom-right watermark)
            title_text = item.get("chapter_title", "")
            if title_text:
                clean_title = escape_drawtext(title_text)
                if clean_title:
                    fade_end = min(seg_dur - 0.5, 6.2)
                    hold_end = max(1.5, fade_end - 1.0)
                    fsize = 42 if len(clean_title) > 52 else 48
                    filter_chain += (
                        f",drawtext={font_opt}text='{clean_title}'"
                        f":fontsize={fsize}:fontcolor=0xF4F1EA"
                        f":shadowcolor=black@0.92:shadowx=3:shadowy=3"
                        f":box=1:boxcolor=black@0.48:boxborderw=22"
                        f":x=(w-text_w)/2:y=h*0.80"
                        f":alpha='if(lt(t,0.9),t/0.9,if(lt(t,{hold_end:.1f}),1,if(lt(t,{fade_end:.1f}),({fade_end:.1f}-t)/1.0,0)))'"
                    )

            clip_path = os.path.join(work_dir, f"clip_{idx}_{uid_tag}.mp4")
            cmd = [
                FFMPEG_PATH, "-y",
                *input_args,
                "-vf", filter_chain,
                *encoder_args,
                "-r", str(fps),
                "-t", f"{seg_dur:.2f}",
                "-pix_fmt", "yuv420p",
                "-an",
                clip_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"FFmpeg scene {idx} render failed: {res.stderr[-400:]}")

            temp_clips.append(clip_path)
            temp_audios.append(os.path.abspath(seg_audio_to_use))

            if progress_callback:
                try:
                    progress_callback(idx + 1, len(scene_items), item)
                except Exception:
                    pass

        # Step 2: Concat video clips & narration+SFX track + very-low-volume creepy music bed
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")
        if progress_callback:
            try:
                progress_callback(len(scene_items), len(scene_items), {"muxing": True})
            except Exception:
                pass
        with open(concat_vid_list, "w", encoding="utf-8") as fv:
            for c in temp_clips:
                safe_c = os.path.abspath(c).replace("\\", "/")
                fv.write(f"file '{safe_c}'\n")

        with open(concat_aud_list, "w", encoding="utf-8") as fa:
            for a in temp_audios:
                safe_a = os.path.abspath(a).replace("\\", "/")
                fa.write(f"file '{safe_a}'\n")

        ambient_file = get_or_create_ambient(duration_needed=total_duration, preferred_track=ambient_path)
        fade_out_audio_st = max(1.0, total_duration - 2.5)
        vol_expr = _build_music_volume_expr(mood_windows)

        filter_complex = (
            f"[1:a]aresample=44100[a_narr];"
            f"[2:a]aresample=44100,loudnorm=I=-29:TP=-2.0:LRA=11,"
            f"volume='{vol_expr}':eval=frame,"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_audio_st:.2f}:d=2.5[a_amb];"
            f"[a_narr][a_amb]amix=inputs=2:duration=first:dropout_transition=3:weights=1.0 0.13,alimiter=limit=0.96[a_mix]"
        )

        final_cmd = [
            FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0", "-i", concat_vid_list,
            "-f", "concat", "-safe", "0", "-i", concat_aud_list,
            "-stream_loop", "-1", "-i", ambient_file,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[a_mix]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total_duration:.2f}",
            "-movflags", "+faststart",
            output_path
        ]

        print(f"[VideoEngine] Muxing Mr. Nightmare horror compilation ({total_duration:.1f}s / {total_duration/60.0:.1f}m)...")
        res_final = subprocess.run(final_cmd, capture_output=True, text=True)
        if res_final.returncode != 0:
            raise RuntimeError(f"FFmpeg final mux failed: {res_final.stderr[-400:]}")

    finally:
        for p in (*temp_clips, *temp_sfx_audios, *temp_ephemeral_photos, concat_vid_list, concat_aud_list):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    return output_path

# Backward compatibility alias
def compose_longform_video(segments: List[Dict[str, Any]], output_path: Optional[str] = None, ambient_path: Optional[str] = None) -> str:
    return compose_mr_nightmare_longform(scene_items=segments, output_path=output_path, ambient_path=ambient_path)
