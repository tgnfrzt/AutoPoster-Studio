import os
import shutil
import json
import uuid
import traceback
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.config import settings, get_random_outro, get_watermark_path
from core.database import get_db
from modules.sources.sanitizer import clean_url, record_link
from modules.sources.downloader import download_video, AgeRestrictedError
from modules.processors.instagram_processor import process_instagram_video
from modules.processors.youtube_processor import process_youtube_shorts_video
from modules.processors.tiktok_processor import process_tiktok_video
from modules.processors.splitter import split_and_conform_to_parts
from modules.processors.reposter import process_repost_with_heavy_watermark
from modules.processors.content_generator import generate_short_video
from modules.processors.story_composer import compose_creepy_brainrot_short
from modules.dispatch.universal_publisher import dispatch_publish

def create_job(
    workflow_id: Optional[str],
    source_url: str,
    target_platform: str,
    recipe: str = "standard",
    series_id: Optional[str] = None,
    series_order: int = 0,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """Creates a new job record in the database."""
    job_id = str(uuid.uuid4())
    canonical = clean_url(source_url) if source_url else f"generated_{job_id[:8]}"
    if source_url:
        record_link(canonical, status="queued")

    meta = metadata or {}
    meta["recipe"] = recipe

    conn = get_db()
    with conn:
        conn.execute("""
            INSERT INTO jobs (
                id, workflow_id, source_url, canonical_url, status,
                target_platform, current_step, step_progress, series_id, series_order,
                metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, workflow_id, source_url or "", canonical, "queued",
            target_platform, "init", 0.0, series_id, series_order,
            json.dumps(meta), datetime.now().isoformat(), datetime.now().isoformat()
        ))
    conn.close()
    return job_id

def update_job_step(
    job_id: str,
    step: str,
    status: str,
    progress: float = 0.0,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    raw_path: Optional[str] = None,
    processed_path: Optional[str] = None,
    receipt: Optional[Dict[str, Any]] = None
):
    """Updates the status and state artifacts of a job."""
    conn = get_db()
    with conn:
        updates = ["current_step = ?", "status = ?", "step_progress = ?", "updated_at = ?"]
        params = [step, status, progress, datetime.now().isoformat()]

        if error is not None:
            updates.append("error_message = ?")
            params.append(error)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if raw_path is not None:
            updates.append("raw_media_path = ?")
            params.append(raw_path)
        if processed_path is not None:
            updates.append("processed_media_path = ?")
            params.append(processed_path)
        if receipt is not None:
            updates.append("receipt_json = ?")
            params.append(json.dumps(receipt))
            if receipt.get("success"):
                updates.append("published_at = ?")
                params.append(datetime.now().isoformat())

        params.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
    conn.close()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def execute_job(job_id: str, resume_step: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a job through its pipeline steps.
    Supports standard rendering (9:16 stretch + watermark + random outro),
    YouTube-to-TikTok chunk splitting with series indexing,
    Instagram reposting with heavy watermarks, and AI short generation.
    """
    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    target = job["target_platform"].lower()
    metadata = json.loads(job["metadata_json"] or "{}")
    recipe = metadata.get("recipe", "standard")

    job_dir = os.path.join(settings.STORAGE_PATH, "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)

    raw_path = job.get("raw_media_path") or os.path.join(job_dir, "raw.mp4")
    processed_path = job.get("processed_media_path") or os.path.join(job_dir, f"rendered_{target}.mp4")
    current_step = job.get("current_step") or "init"

    def mark_step(step: str, status: str, progress: float = 0.0, **kwargs):
        nonlocal current_step
        current_step = step
        update_job_step(job_id, step=step, status=status, progress=progress, **kwargs)

    try:
        # ==========================================
        # RECIPE: Preprocessed / Split Series Part (Already rendered, jump straight to publish)
        # ==========================================
        if recipe in ("split_part", "preprocessed", "ai_studio_generated", "ai_studio_longform") or metadata.get("is_pre_rendered"):
            if not processed_path or not os.path.isfile(processed_path):
                # Check if metadata specified rendered_video
                alt_path = metadata.get("rendered_video")
                if alt_path and os.path.isfile(alt_path):
                    processed_path = alt_path
                else:
                    raise FileNotFoundError(f"Rendered video part not found on disk: {processed_path}")
            mark_step("process", "process_complete", 75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: Completely Generated Short
        # ==========================================
        elif recipe == "generated_shorts":
            update_job_step(job_id, step="generate", status="generating", progress=30.0)
            gen_res = generate_short_video(
                output_path=processed_path,
                title=metadata.get("title"),
                body_text=metadata.get("caption"),
                duration_sec=float(metadata.get("duration_sec", 25.0))
            )
            metadata["title"] = gen_res["title"]
            metadata["caption"] = gen_res["description"]
            update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: Creepy Brainrot Short (Mr. Nightmare / Qwen3-TTS + Split-Screen B-roll)
        # ==========================================
        elif recipe in ("creepy_brainrot_short", "horror_short"):
            update_job_step(job_id, step="generate", status="generating", progress=30.0)
            story_text = metadata.get("story_text") or metadata.get("caption") or metadata.get("body_text") or "I heard a strange noise outside my window at 3 AM."
            voice = metadata.get("voice_preset", "mr_nightmare")
            top_cat = metadata.get("top_category", "creepy_ambient")
            bot_cat = metadata.get("bottom_category", "subway_surfers")

            res = compose_creepy_brainrot_short(
                story_text=story_text,
                output_path=processed_path,
                voice_preset=voice,
                top_category=top_cat,
                bottom_category=bot_cat,
                watermark_path=metadata.get("watermark_path") or get_watermark_path(),
                watermark_alpha=float(metadata.get("watermark_alpha", 0.85)),
                anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                job_id=job_id
            )
            title = metadata.get("title") or "True Horror Story #Shorts"
            metadata["title"] = title
            metadata["caption"] = metadata.get("caption") or f"{title} #horror #creepy #redditstories #mrnightmare"
            update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: YouTube to TikTok Parts Splitter
        # ==========================================
        elif recipe == "youtube_to_tiktok_split":
            # Step 1: Download full long video (if not already cached)
            if not resume_step or resume_step in ("init", "download"):
                if not os.path.isfile(raw_path):
                    update_job_step(job_id, step="download", status="downloading", progress=20.0)
                    down_meta = download_video(job["source_url"], raw_path, job_id=job_id)
                    metadata.update(down_meta)
                    update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)
                else:
                    update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)

            # Step 2: Split into sequential mini-videos named {series_id}part1.mp4, etc.
            if not resume_step or resume_step in ("init", "download", "process"):
                update_job_step(job_id, step="process", status="splitting", progress=60.0)
                s_id = job.get("series_id")
                if not s_id:
                    existing_parts = [f for f in os.listdir(job_dir) if "part" in f and f.endswith(".mp4") and os.path.getsize(os.path.join(job_dir, f)) > 1000000]
                    if existing_parts:
                        s_id = existing_parts[0].split("part")[0]
                    else:
                        s_id = f"tt_{uuid.uuid4().hex[:6]}"

                chunk_len = float(metadata.get("chunk_duration_sec", 150.0))
                chunks = split_and_conform_to_parts(
                    input_path=raw_path,
                    output_dir=job_dir,
                    series_id=s_id,
                    chunk_duration_sec=chunk_len,
                    add_part_banner=bool(metadata.get("add_part_banner", True)),
                    watermark_path=metadata.get("watermark_path") or get_watermark_path(),
                    watermark_alpha=float(metadata.get("watermark_alpha", 0.9)),
                    anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                    job_id=job_id
                )
                print(f"[WorkflowEngine] Split video into {len(chunks)} TikTok part(s) under Series ID '{s_id}'.")

                if chunks:
                    # Update parent job as Part 1 of the series (pre-rendered)
                    processed_path = chunks[0]["output_path"]
                    title = metadata.get("title") or "Video"
                    metadata["recipe"] = "split_part"
                    metadata["caption"] = f"{title} - Part 1/{len(chunks)} #fyp #viral #tiktok"

                    conn = get_db()
                    with conn:
                        conn.execute(
                            "UPDATE jobs SET series_id = ?, series_order = 1, metadata_json = ? WHERE id = ?",
                            (s_id, json.dumps(metadata), job_id)
                        )
                    conn.close()

                    update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

                    # Enqueue remaining parts sequentially with incrementing series_order and recipe=split_part
                    for chk in chunks[1:]:
                        p_num = chk["part_number"]
                        sub_meta = dict(metadata)
                        sub_meta["caption"] = f"{title} - Part {p_num}/{len(chunks)} #fyp #viral #tiktok"
                        sub_meta["recipe"] = "split_part"
                        sub_jid = create_job(
                            workflow_id=job.get("workflow_id"),
                            source_url="",
                            target_platform="tiktok",
                            recipe="split_part",
                            series_id=s_id,
                            series_order=p_num,
                            metadata=sub_meta
                        )
                        update_job_step(sub_jid, step="publish", status="queued", progress=75.0, processed_path=chk["output_path"])

        # ==========================================
        # RECIPE: Instagram Repost with Heavy Watermark
        # ==========================================
        elif recipe == "instagram_repost_heavy":
            if not resume_step or resume_step in ("init", "download"):
                update_job_step(job_id, step="download", status="downloading", progress=20.0)
                down_meta = download_video(job["source_url"], raw_path, job_id=job_id)
                metadata.update(down_meta)
                update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)

            if not resume_step or resume_step in ("init", "download", "process"):
                update_job_step(job_id, step="process", status="processing", progress=50.0)
                watermark = metadata.get("watermark_path") or get_watermark_path()
                outro = metadata.get("outro_path") or get_random_outro()

                process_repost_with_heavy_watermark(
                    input_path=raw_path,
                    output_path=processed_path,
                    watermark_path=watermark,
                    watermark_alpha=float(metadata.get("watermark_alpha", 1.0)),
                    outro_path=outro,
                    anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                    job_id=job_id
                )
                update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: YouTube Long-Form (16:9 Landscape / Full Length / No Shorts Shelf)
        # ==========================================
        elif recipe == "youtube_longform":
            metadata["is_shorts"] = False
            if job["source_url"] and (not resume_step or resume_step in ("init", "download")):
                update_job_step(job_id, step="download", status="downloading", progress=20.0)
                down_meta = download_video(job["source_url"], raw_path, job_id=job_id)
                metadata.update(down_meta)
                update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)

            if not resume_step or resume_step in ("init", "download", "process"):
                update_job_step(job_id, step="process", status="processing", progress=50.0)
                anti_fp = bool(metadata.get("anti_fingerprint", False))
                norm_audio = metadata.get("normalize_audio", True)
                if not anti_fp and not norm_audio:
                    shutil.copyfile(raw_path, processed_path)
                else:
                    from utils.ffmpeg_runner import run_ffmpeg, get_fast_encoder_args
                    af = "loudnorm=I=-14:LRA=11:TP=-1.5,aformat=sample_rates=48000:channel_layouts=stereo" if norm_audio else "anull"
                    v_args = get_fast_encoder_args(quality=20) if anti_fp else ["-c:v", "copy"]
                    cmd = ["-i", raw_path] + v_args + ["-af", af, "-c:a", "aac", "-b:a", "192k", processed_path]
                    run_ffmpeg(cmd, job_id=job_id)
                update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: Clean Passthrough (No Filters, No Anti-Fingerprint, Original Quality)
        # ==========================================
        elif recipe in ("clean_passthrough", "raw_passthrough", "direct_passthrough"):
            metadata["anti_fingerprint"] = False
            if job["source_url"] and (not resume_step or resume_step in ("init", "download")):
                update_job_step(job_id, step="download", status="downloading", progress=20.0)
                down_meta = download_video(job["source_url"], raw_path, job_id=job_id)
                metadata.update(down_meta)
                update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)

            if not resume_step or resume_step in ("init", "download", "process"):
                update_job_step(job_id, step="process", status="processing", progress=50.0)
                shutil.copyfile(raw_path, processed_path)
                update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # RECIPE: Standard Target Conformer (Instagram / YouTube / TikTok)
        # ==========================================
        else:
            # If source_url is present, download first
            if job["source_url"] and (not resume_step or resume_step in ("init", "download")):
                update_job_step(job_id, step="download", status="downloading", progress=20.0)
                down_meta = download_video(job["source_url"], raw_path, job_id=job_id)
                metadata.update(down_meta)
                update_job_step(job_id, step="download", status="download_complete", progress=40.0, raw_path=raw_path)

            if not resume_step or resume_step in ("init", "download", "process"):
                update_job_step(job_id, step="process", status="processing", progress=50.0)

                if target in ("instagram", "instagram_reels", "ig"):
                    # Process Instagram: 9:16 stretch, watermark from resources, random outro from resources/outros/
                    proc_res = process_instagram_video(
                        input_path=raw_path,
                        output_path=processed_path,
                        watermark_path=metadata.get("watermark_path") or get_watermark_path(),
                        outro_path=metadata.get("outro_path") or get_random_outro(),
                        watermark_alpha=float(metadata.get("watermark_alpha", 1.0)),
                        stretch_to_9x16=bool(metadata.get("stretch_to_9x16", True)),
                        anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                        job_id=job_id
                    )
                elif target in ("youtube", "youtube_shorts", "yt"):
                    proc_res = process_youtube_shorts_video(
                        input_path=raw_path,
                        output_path=processed_path,
                        max_duration_sec=float(metadata.get("max_duration_sec", 59.5)),
                        normalize_audio=True,
                        watermark_path=metadata.get("watermark_path") or get_watermark_path(),
                        watermark_alpha=float(metadata.get("watermark_alpha", 0.85)),
                        anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                        job_id=job_id
                    )
                elif target in ("tiktok", "tt"):
                    proc_res = process_tiktok_video(
                        input_path=raw_path,
                        output_path=processed_path,
                        watermark_path=metadata.get("watermark_path") or get_watermark_path(),
                        watermark_alpha=float(metadata.get("watermark_alpha", 0.9)),
                        anti_fingerprint=bool(metadata.get("anti_fingerprint", True)),
                        job_id=job_id
                    )
                update_job_step(job_id, step="process", status="process_complete", progress=75.0, processed_path=processed_path)

        # ==========================================
        # FINAL STEP: Universal Dispatch & Publishing
        # ==========================================
        if not resume_step or resume_step in ("init", "download", "process", "publish"):
            mark_step("publish", "publishing", 85.0)

            if not processed_path or not os.path.isfile(processed_path):
                raise FileNotFoundError(f"Cannot publish: Processed video file not found at {processed_path}")

            receipt = dispatch_publish(
                job_id=job_id,
                platform=target,
                video_path=processed_path,
                metadata=metadata
            )
            mark_step("publish", "completed", 100.0, receipt=receipt)
            return {"success": True, "job_id": job_id, "receipt": receipt}

    except Exception as exc:
        # Check if job was cancelled by user
        curr_job = get_job(job_id)
        if curr_job and curr_job.get("status") == "cancelled":
            print(f"[WorkflowEngine] Job {job_id} was cancelled by user. Terminating execution cleanly.")
            return {"success": False, "job_id": job_id, "error": "Job was cancelled by user"}

        trace = traceback.format_exc()
        print(f"[WorkflowEngine] Error executing job {job_id}: {exc}\n{trace}")

        is_age_restricted = isinstance(exc, AgeRestrictedError)
        err_code = "AGE_RESTRICTED" if is_age_restricted else "EXECUTION_FAILED"
        status_to_set = "failed" if is_age_restricted else "repair_required"

        update_job_step(
            job_id,
            step=current_step,
            status=status_to_set,
            error=str(exc),
            error_code=err_code,
            raw_path=raw_path if os.path.isfile(raw_path) else None,
            processed_path=processed_path if os.path.isfile(processed_path) else None
        )
        return {"success": False, "job_id": job_id, "error": str(exc), "error_code": err_code}
