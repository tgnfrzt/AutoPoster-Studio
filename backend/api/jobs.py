import json
import os
import shutil
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from core.database import get_db
from core.workflow_engine import create_job, execute_job, get_job, update_job_step
from modules.sources.sanitizer import sanitize_and_dedupe
from core.config import settings
try:
    from utils.ffmpeg_runner import kill_job_processes
except ImportError:
    from backend.utils.ffmpeg_runner import kill_job_processes

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

class JobCreateRequest(BaseModel):
    source_url: Optional[str] = ""
    target_platforms: List[str] = ["instagram"]
    recipe: str = "standard"
    caption: Optional[str] = ""
    watermark_alpha: Optional[float] = 1.0
    chunk_duration_sec: Optional[float] = 150.0
    max_duration_sec: Optional[float] = 59.5
    anti_fingerprint: Optional[bool] = True

class JobRetryRequest(BaseModel):
    resume_step: str = "publish"
    updated_caption: Optional[str] = None
    updated_target_platform: Optional[str] = None

class BatchLinksRequest(BaseModel):
    links: List[str]
    target_platforms: List[str] = ["instagram"]
    recipe: str = "standard"
    caption: Optional[str] = ""
    watermark_alpha: Optional[float] = 1.0
    chunk_duration_sec: Optional[float] = 150.0
    anti_fingerprint: Optional[bool] = True

@router.get("")
def list_jobs(status: Optional[str] = None, limit: int = 50):
    conn = get_db()
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit))
    else:
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"jobs": rows, "count": len(rows)}

@router.post("")
def add_job(req: JobCreateRequest, background_tasks: BackgroundTasks):
    meta = {
        "caption": req.caption,
        "watermark_alpha": req.watermark_alpha,
        "chunk_duration_sec": req.chunk_duration_sec,
        "max_duration_sec": req.max_duration_sec,
        "recipe": req.recipe,
        "anti_fingerprint": req.anti_fingerprint
    }
    created_ids = []
    for platform in req.target_platforms:
        job_id = create_job(
            workflow_id=None,
            source_url=req.source_url or "",
            target_platform=platform,
            recipe=req.recipe,
            metadata=meta
        )
        created_ids.append(job_id)

    return {
        "success": True,
        "job_ids": created_ids,
        "message": f"Created {len(created_ids)} job(s) for targets: {', '.join(req.target_platforms)}"
    }

@router.post("/batch")
def add_batch_jobs(req: BatchLinksRequest):
    clean_urls, dup_count = sanitize_and_dedupe(req.links)
    created_ids = []
    meta = {
        "caption": req.caption,
        "watermark_alpha": req.watermark_alpha,
        "chunk_duration_sec": req.chunk_duration_sec,
        "recipe": req.recipe,
        "anti_fingerprint": req.anti_fingerprint
    }

    for url in clean_urls:
        for platform in req.target_platforms:
            jid = create_job(
                workflow_id=None,
                source_url=url,
                target_platform=platform,
                recipe=req.recipe,
                metadata=meta
            )
            created_ids.append(jid)

    return {
        "success": True,
        "created_count": len(created_ids),
        "duplicates_skipped": dup_count,
        "job_ids": created_ids
    }

@router.post("/upload")
async def upload_direct_video(
    file: UploadFile = File(...),
    target_platform: str = Form("youtube_shorts"),
    target_platforms: Optional[str] = Form(None),
    caption: str = Form(""),
    title: str = Form(""),
    recipe: str = Form("standard"),
    anti_fingerprint: bool = Form(True),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Direct Drag-and-Drop file upload endpoint.
    Saves the file to storage/raw/ and enqueues processing jobs across chosen target platforms.
    """
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    safe_name = f"upload_{uuid.uuid4().hex[:8]}{ext}"
    dest_path = os.path.join(settings.RAW_PATH, safe_name)

    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    clean_title = title.strip() or os.path.splitext(os.path.basename(file.filename))[0].replace("_", " ").title()
    
    # Determine default caption based on recipe
    if recipe == "youtube_longform":
        final_caption = caption or f"{clean_title}\n\n#YouTube #Video"
        effective_anti_fp = False
    elif recipe in ("clean_passthrough", "raw_passthrough"):
        final_caption = caption or f"{clean_title} #viral #trending"
        effective_anti_fp = False
    else:
        final_caption = caption or f"{clean_title} #Shorts #Viral"
        effective_anti_fp = anti_fingerprint

    # Parse target platforms
    targets = []
    if target_platforms:
        try:
            parsed = json.loads(target_platforms)
            if isinstance(parsed, list):
                targets = [str(p).strip() for p in parsed if str(p).strip()]
        except Exception:
            targets = [p.strip() for p in target_platforms.split(",") if p.strip()]
    if not targets:
        targets = [target_platform]

    created_ids = []
    for plat in targets:
        meta = {
            "title": clean_title,
            "caption": final_caption,
            "recipe": recipe,
            "anti_fingerprint": effective_anti_fp,
            "original_filename": file.filename,
            "is_shorts": False if recipe == "youtube_longform" else True
        }

        job_id = create_job(
            workflow_id=None,
            source_url="",
            target_platform=plat,
            recipe=recipe,
            metadata=meta
        )

        # Mark raw path as ready for processing
        update_job_step(
            job_id,
            step="download",
            status="queued",
            progress=35.0,
            raw_path=dest_path
        )
        created_ids.append(job_id)

    return {
        "success": True,
        "job_ids": created_ids,
        "job_id": created_ids[0] if created_ids else None,
        "filename": file.filename,
        "target_platforms": targets,
        "message": f"Uploaded '{file.filename}' and queued {len(created_ids)} job(s)."
    }

@router.get("/{job_id}")
def inspect_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["metadata"] = json.loads(job["metadata_json"] or "{}")
    job["receipt"] = json.loads(job["receipt_json"] or "{}")
    return job

@router.post("/{job_id}/retry")
def retry_job(job_id: str, req: JobRetryRequest, background_tasks: BackgroundTasks):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") in ("downloading", "processing", "splitting", "publishing"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is already active in step '{job.get('current_step')}' (status: {job.get('status')}). Please wait for it to complete or fail before retrying."
        )

    conn = get_db()
    with conn:
        if req.updated_caption is not None or req.updated_target_platform is not None:
            meta = json.loads(job["metadata_json"] or "{}")
            if req.updated_caption is not None:
                meta["caption"] = req.updated_caption
            target = req.updated_target_platform or job["target_platform"]
            conn.execute(
                "UPDATE jobs SET metadata_json = ?, target_platform = ?, status = 'queued' WHERE id = ?",
                (json.dumps(meta), target, job_id)
            )
        else:
            conn.execute("UPDATE jobs SET status = 'queued' WHERE id = ?", (job_id,))
    conn.close()

    background_tasks.add_task(execute_job, job_id, req.resume_step)
    return {"success": True, "job_id": job_id, "resuming_from_step": req.resume_step}

@router.post("/{job_id}/stop")
def stop_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    killed = kill_job_processes(job_id)

    conn = get_db()
    with conn:
        conn.execute("""
            UPDATE jobs 
            SET status = 'cancelled', 
                error_message = 'Job stopped by user', 
                error_code = 'USER_STOPPED',
                updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), job_id))
    conn.close()

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Job stopped. Terminated {killed} running process(es)."
    }

@router.delete("/{job_id}")
def delete_job(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Kill active processes
    kill_job_processes(job_id)

    # 2. Clean up job directory
    job_dir = os.path.join(settings.STORAGE_PATH, "jobs", job_id)
    if os.path.isdir(job_dir):
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            print(f"[API] Error removing job dir {job_dir}: {e}")

    # 3. Clean up individual raw/processed files if tied to this job
    for p_key in ("raw_media_path", "processed_media_path"):
        fpath = job.get(p_key)
        if fpath and os.path.isfile(fpath) and job_id in fpath:
            try:
                os.remove(fpath)
            except Exception:
                pass

    # 4. Remove from DB
    conn = get_db()
    with conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.close()

    return {"success": True, "job_id": job_id, "message": "Job terminated and deleted."}

@router.get("/{job_id}/stream/{media_type}")
def stream_media(job_id: str, media_type: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    path = job.get("processed_media_path") if media_type == "processed" else job.get("raw_media_path")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Media file not found for {media_type}")

    return FileResponse(path, media_type="video/mp4")
