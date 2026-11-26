import sys
import os
import shutil
import uuid
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .config import OUTPUT_SHORTS_DIR, OUTPUT_LONGFORM_DIR
from .content_engine import (
    generate_shorts_script,
    generate_longform_script,
    generate_mr_nightmare_compilation
)
from .tts_engine import generate_audio, synthesize_scenes_batch
from .subtitle_engine import generate_ass_subtitles
from .video_engine import compose_short_video, compose_mr_nightmare_longform
from .ai_director import direct_compilation_scenes
from .visual_engine import batch_generate_local_sd_images

def run_generate_short(
    source: str = "reddit",
    topic: str = "shortscarystories",
    voice: str = "mr_nightmare",
    gameplay: Optional[str] = None,
    output_path: Optional[str] = None,
    progress_callback: Optional[Any] = None,
    tts_engine: str = "chatterbox_turbo",
    cancel_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """End-to-End Orchestrator for Vertical 9:16 Split-Screen Viral Shorts."""
    t0 = time.time()
    uid = uuid.uuid4().hex[:8]
    work_dir = os.path.dirname(output_path) if output_path else str(OUTPUT_SHORTS_DIR)
    os.makedirs(work_dir, exist_ok=True)

    final_output = output_path or os.path.join(work_dir, f"short_{uid}.mp4")
    audio_path = os.path.join(work_dir, f"audio_{uid}.mp3")
    ass_path = os.path.join(work_dir, f"subtitles_{uid}.ass")

    def _emit(stage: int, stage_name: str, step: int, total: int, msg: str, pct: Optional[float] = None):
        if progress_callback:
            try:
                progress_callback({
                    "stage": stage,
                    "total_stages": 4,
                    "stage_name": stage_name,
                    "current_step": step,
                    "total_steps": total,
                    "percent": pct if pct is not None else round((stage - 1 + (step / max(1, total))) / 4.0 * 100, 1),
                    "message": msg
                })
            except Exception:
                pass

    print(f"\n==========================================")
    print(f"[PIPELINE] Generating Viral 9:16 Short")
    print(f"Topic: {topic} | Source: {source} | Voice: {voice} | Engine: {tts_engine}")
    print(f"==========================================")

    try:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        _emit(1, "Fetching Script", 0, 1, "Generating/scraping Short script...")
        print(f"[1/4] Fetching/generating story script...")
        script_data = generate_shorts_script(source=source, topic=topic)
        title = script_data.get("title", "Creepy Short")
        full_text = script_data.get("full_script", "")
        img_prompt = script_data.get("image_prompt", "dark foggy cabin analog horror 35mm")
        _emit(1, "Script Ready", 1, 1, f"Script generated: {title}")

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        _emit(2, "Voiceover Synthesis (GPU CUDA)", 0, 1, f"Synthesizing voice with {tts_engine} GPU...")
        print(f"[2/4] Synthesizing neural voiceover ({tts_engine})...")
        duration = generate_audio(
            text=full_text,
            voice=voice,
            output_path=audio_path,
            rate="+10%",
            tts_engine=tts_engine,
            cancel_event=cancel_event,
        )
        _emit(2, "Voiceover Complete", 1, 1, f"Voice synthesized ({duration:.1f}s)")

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        _emit(3, "Subtitles Alignment", 0, 1, "Generating word-level ASS subtitles...")
        print(f"[3/4] Transcribing word-level ASS subtitles...")
        generate_ass_subtitles(
            audio_path=audio_path,
            output_ass_path=ass_path,
            fallback_text=full_text,
            total_duration=duration
        )
        _emit(3, "Subtitles Ready", 1, 1, "Word-level subtitles generated")

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        _emit(4, "NVENC Video Compositing", 0, 1, "Compositing 9:16 split-screen video...")
        print(f"[4/4] Assembling split-screen video with NVENC...")
        compose_short_video(
            narration_audio_path=audio_path,
            ass_subtitle_path=ass_path,
            image_prompt=img_prompt,
            output_path=final_output,
            gameplay_path=gameplay
        )
    finally:
        # Always clean up temporary audio/subtitle files even on error or cancel
        for p in (audio_path, ass_path):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    elapsed = time.time() - t0
    _emit(4, "Complete", 1, 1, f"Viral Short finished ({duration:.1f}s in {elapsed:.1f}s)!", pct=100.0)
    print(f"[SHORT COMPLETE] {final_output} ({duration:.1f}s rendered in {elapsed:.1f}s)\n")
    return {
        "success": True,
        "type": "short",
        "title": title,
        "duration_sec": round(duration, 1),
        "output_path": final_output,
        "script": full_text,
        "elapsed_sec": round(elapsed, 1)
    }

def run_generate_longform(
    topic: str = "LetsNotMeet",
    source: str = "qwen_hermes",
    custom_text: Optional[str] = None,
    voice: str = "mr_nightmare",
    visual_mode: str = "hybrid_director",
    segments: Optional[int] = None,
    target_duration_minutes: float = 46.0,
    max_stories: Optional[int] = None,
    ambient_track: Optional[str] = None,
    output_path: Optional[str] = None,
    progress_callback: Optional[Any] = None,
    tts_engine: str = "chatterbox_turbo",
    cancel_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    End-to-End Orchestrator for Long-Form Mr. Nightmare YouTube Videos (supports 45+ min marathons):
    1. Content Engine + Hermes/ArcticShift: Real long-form Reddit horror stories (4-6 uncut stories for 45+ min).
    2. Qwen3.6 AI Director: Auto-starts Qwen3.6-35B-A3B, tags each scene with visual_type, search query, AI prompt, SFX cue & music mood, then stops Qwen3.6.
    3. Audio TTS: Fast batched Chatterbox Turbo GPU (default) or Qwen3-TTS 0.6B GPU, dark broadcast mastered.
    4. Hybrid Visual Engine: Local GPU SD-Turbo horror images + Mixkit stock video clips + visible nocturnal photos.
    5. Video Engine: Foley SFX mixing, very-low-volume dynamic creepy music, Georgia serif chapter cards, NVENC hardware encoding.
    """
    t0 = time.time()
    uid = uuid.uuid4().hex[:8]
    work_dir = os.path.dirname(output_path) if output_path else str(OUTPUT_LONGFORM_DIR)
    os.makedirs(work_dir, exist_ok=True)
    run_tmp_dir = Path(work_dir) / f"tmp_{uid}"
    run_tmp_dir.mkdir(parents=True, exist_ok=True)

    final_output = output_path or os.path.join(work_dir, f"mr_nightmare_{uid}.mp4")

    def _emit(
        stage: int,
        stage_name: str,
        step: int,
        total: int,
        msg: str,
        pct: Optional[float] = None,
        total_scenes_hint: int = 0,
        last_gen_sec: Optional[float] = None,
    ):
        if progress_callback:
            try:
                stage_weights = {
                    1: (0.0, 2.0),     # Reddit story scrape/curate
                    2: (2.0, 12.0),    # Qwen3.6 scene & SFX direction
                    3: (12.0, 82.0),   # GPU voiceover synthesis (largest block)
                    4: (82.0, 88.0),   # Local SD-Turbo GPU image batch
                    5: (88.0, 99.0),   # Video compositing & NVENC encoding
                }
                if pct is None:
                    s_min, s_max = stage_weights.get(stage, (0.0, 100.0))
                    frac = min(1.0, max(0.0, step / max(1, total))) if total > 0 else 0.0
                    p = round(s_min + (s_max - s_min) * frac, 1)
                else:
                    p = round(pct, 1)

                payload = {
                    "stage": stage,
                    "total_stages": 5,
                    "stage_name": stage_name,
                    "current_step": step,
                    "total_steps": total,
                    "percent": p,
                    "message": msg,
                }
                if total_scenes_hint > 0:
                    payload["total_scenes"] = total_scenes_hint
                if last_gen_sec is not None:
                    payload["last_gen_sec"] = last_gen_sec
                progress_callback(payload)
            except Exception:
                pass

    print(f"\n=======================================================")
    print(f"[PIPELINE] Generating Mr. Nightmare Long-Form Horror Video")
    print(f"Topic: {topic} | Source: {source} | Visuals: {visual_mode} | Voice: {voice} ({tts_engine}) | Target: {target_duration_minutes}m")
    print(f"=======================================================")

    total_audio_dur = 0.0
    scene_items: List[Dict[str, Any]] = []
    stories: List[Dict[str, Any]] = []
    title = f"Disturbing True Stories: {topic}"

    try:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        # Step 1: Fetch real Reddit horror stories or custom compilation
        _emit(1, "Scraping Reddit Horror Stories", 0, 1, f"Fetching stories from r/{topic}...")
        print(f"[1/5] Fetching long-form Reddit horror stories...")
        comp = generate_mr_nightmare_compilation(
            topic=topic,
            source=source,
            custom_text=custom_text,
            target_duration_minutes=target_duration_minutes,
            max_stories=max_stories
        )
        title = comp.get("title", f"Disturbing True Stories: {topic}")
        stories = comp.get("stories", [])
        intro_text = comp.get("intro", "")
        outro_text = comp.get("outro", "")

        print(f"  Title: {title}")
        print(f"  Stories loaded: {len(stories)} (~{comp.get('total_words', 0)} words)")

        # Step 2: Build scene timeline items & run Qwen3.6 AI Scene/SFX/Music Director
        if intro_text and (segments is None or segments > 6) and len(stories) > 1:
            scene_items.append({
                "type": "intro",
                "chapter_title": title,
                "text": intro_text,
                "image_prompt": f"cinematic dark horror title scene for {title}, foggy lonely road at midnight"
            })

        for s_idx, s in enumerate(stories):
            s_num = s.get("story_number", s_idx + 1)
            s_title = s.get("title", f"Story {s_num}")
            s_segs = s.get("segments", [])

            if segments and len(scene_items) >= segments:
                break

            for seg_idx, seg in enumerate(s_segs):
                if segments and len(scene_items) >= segments:
                    break
                is_first_seg = (seg_idx == 0)
                chapter_label = f"Story {s_num} - {s_title}" if is_first_seg else ""
                scene_items.append({
                    "type": "story_segment",
                    "story_number": s_num,
                    "chapter_title": chapter_label,
                    "text": seg.get("text", ""),
                    "image_prompt": seg.get("image_prompt", "creepy dark foggy forest at night")
                })

        if outro_text and (segments is None or segments > 6):
            scene_items.append({
                "type": "outro",
                "chapter_title": "",
                "text": outro_text,
                "image_prompt": "foggy desolate road at night, eerie mist"
            })

        num_scenes = len(scene_items)
        _emit(
            1,
            "Stories Loaded",
            1,
            1,
            f"Loaded {len(stories)} stories ({num_scenes} scenes, ~{comp.get('total_words', 0)} words)",
            total_scenes_hint=num_scenes,
        )

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        use_qwen_director = (
            source in ("qwen_hermes", "ollama", "qwen")
            or visual_mode in ("hybrid_director", "local_ai_gpu")
        )
        _emit(
            2,
            "AI Scene & Sound Direction",
            0,
            num_scenes,
            f"Directing {num_scenes} scenes with Qwen3.6 (batches of 6)...",
            total_scenes_hint=num_scenes,
        )
        print(f"[2/5] Directing {num_scenes} scenes (Visuals + Foley SFX + Mood) [Qwen3.6={'ON' if use_qwen_director else 'OFF'}]...")

        def _dir_progress(done_scenes: int, total_s: int, b_num: int, b_tot: int):
            _emit(
                2,
                "AI Scene & Sound Direction",
                done_scenes,
                total_s,
                f"Qwen3.6 directed scenes {done_scenes}/{total_s} (batch {b_num}/{b_tot})",
                total_scenes_hint=total_s,
            )

        direct_compilation_scenes(
            scene_items,
            use_qwen=use_qwen_director,
            progress_callback=_dir_progress,
            cancel_event=cancel_event,
        )
        _emit(
            2,
            "AI Direction Complete",
            num_scenes,
            num_scenes,
            f"Directed {num_scenes} scenes with visual types & foley cues",
            total_scenes_hint=num_scenes,
        )

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        # Step 3: Synthesize deep dark-mastered narrator voice in batched GPU worker
        engine_label = "Chatterbox Turbo GPU" if tts_engine == "chatterbox_turbo" else "Qwen3-TTS 0.6B GPU"
        _emit(
            3,
            f"Synthesizing Voice ({engine_label})",
            0,
            num_scenes,
            f"Loading {engine_label} for {num_scenes} scenes...",
            total_scenes_hint=num_scenes,
        )
        print(f"[3/5] Synthesizing deep dark-mastered narrator voice ({engine_label}) for {num_scenes} scenes...")

        def _tts_progress(done: int, total_s: int, audio_dur: float, gen_sec: float, item: Dict[str, Any]):
            _emit(
                3,
                f"Synthesizing Voice ({engine_label})",
                done,
                total_s,
                f"Scene {done}/{total_s} ({audio_dur:.1f}s in {gen_sec:.1f}s): '{item['text'][:36]}...'",
                total_scenes_hint=total_s,
                last_gen_sec=gen_sec,
            )

        total_audio_dur = synthesize_scenes_batch(
            scene_items=scene_items,
            run_tmp_dir=run_tmp_dir,
            uid=uid,
            voice=voice,
            tts_engine=tts_engine,
            dark_mastering=True,
            progress_callback=_tts_progress,
            cancel_event=cancel_event,
        )

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        # Step 4: Batch-generate local GPU AI horror images (SD-Turbo on GTX 1650) if enabled
        if visual_mode in ("hybrid_director", "local_ai_gpu"):
            _emit(4, "Generating Local AI Horror Images (SD-Turbo)", 0, 1, "Running GTX 1650 SD-Turbo batch for key scenes...", total_scenes_hint=num_scenes)
            print(f"[4/5] Running GTX 1650 Local GPU SD-Turbo batch for AI horror scenes...")
            batch_generate_local_sd_images(scene_items, work_dir=run_tmp_dir)
            _emit(4, "AI Images Ready", 1, 1, "SD-Turbo image batch complete.", total_scenes_hint=num_scenes)
        else:
            _emit(4, "Stock & Nocturnal Visuals", 1, 1, f"Visual mode '{visual_mode}' — stock footage and dark photography ready.", total_scenes_hint=num_scenes)
            print(f"[4/5] Visual mode '{visual_mode}' — skipping local SD batch.")

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

        # Step 5: Compositing video with NVENC
        _emit(5, "Compositing Video with NVENC", 0, num_scenes, f"Compositing {num_scenes} scenes with 1080p NVENC...", total_scenes_hint=num_scenes)
        print(f"[5/5] Compositing stock video clips, AI horror imagery, foley SFX & very-low-volume music...")

        def _video_progress(curr, total_s, it):
            if it.get("muxing"):
                _emit(5, "Finalizing Creepy Music Audio Bed", total_s, total_s, "Muxing narration, foley SFX and dynamic background ambient music...", total_scenes_hint=total_s)
            else:
                _emit(5, "Compositing Video with NVENC", curr, total_s, f"Rendered scene {curr}/{total_s} with NVENC H.264", total_scenes_hint=total_s)

        compose_mr_nightmare_longform(
            scene_items=scene_items,
            output_path=final_output,
            ambient_path=ambient_track,
            visual_mode=visual_mode,
            progress_callback=_video_progress,
            cancel_event=cancel_event,
        )
    finally:
        # Always clean up temporary per-run working directory even on error or cancel
        shutil.rmtree(run_tmp_dir, ignore_errors=True)

    elapsed = time.time() - t0
    _emit(5, "Render Complete", len(scene_items), len(scene_items), f"Finished {total_audio_dur/60.0:.1f} min video in {elapsed:.1f}s!", pct=100.0, total_scenes_hint=len(scene_items))
    print(f"[LONGFORM COMPLETE] Output duration: {total_audio_dur:.1f}s ({total_audio_dur/60.0:.1f} min) (Rendered in {elapsed:.1f}s)")
    print(f"  Final File: {final_output}\n")

    return {
        "success": True,
        "type": "longform",
        "title": title,
        "duration_sec": round(total_audio_dur, 1),
        "duration_min": round(total_audio_dur / 60.0, 1),
        "output_path": final_output,
        "scenes_count": len(scene_items),
        "stories_count": len(stories),
        "stories": [
            {
                "title": s.get("title"),
                "subreddit": s.get("subreddit", ""),
                "author": s.get("author", ""),
                "score": s.get("score", 0),
                "word_count": s.get("word_count", 0),
            }
            for s in stories
        ],
        "elapsed_sec": round(elapsed, 1)
    }
