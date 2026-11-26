import os
import sys
import glob
import time
import uuid
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

# Ensure base dir is in sys.path
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from pipeline.src.pipeline import run_generate_short, run_generate_longform
from pipeline.src.content_engine import scrape_reddit_horror_stories
from pipeline.src.config import GAMEPLAY_DIR, AMBIENT_DIR, OUTPUT_SHORTS_DIR, OUTPUT_LONGFORM_DIR, ASSETS_DIR
from core.workflow_engine import create_job, execute_job
from core.database import get_db

router = APIRouter(prefix="/api/stories", tags=["stories"])

# Thread-safe global progress state for real-time WebUI tracking
_PROGRESS_LOCK = threading.Lock()
_CANCEL_EVENT = threading.Event()
GLOBAL_PROGRESS: Dict[str, Any] = {
    "active": False,
    "job_id": None,
    "type": "idle",       # "longform", "short", "idle"
    "status": "idle",     # "idle", "running", "completed", "cancelled", "error"
    "tts_engine": "chatterbox_turbo",
    "stage": 0,
    "total_stages": 5,
    "stage_name": "Idle",
    "stage_start_time": 0.0,
    "current_step": 0,
    "total_steps": 0,
    "total_scenes": 0,
    "avg_scene_sec": 0.0,
    "percent": 0.0,
    "start_time": 0.0,
    "elapsed_sec": 0.0,
    "eta_sec": 0.0,
    "message": "Ready to generate. Select your theme and click generate.",
    "recent_logs": [],
    "result": None,
    "error": None
}

class ShortGenRequest(BaseModel):
    source: str = "reddit"
    topic: str = "shortscarystories"
    voice: str = "mr_nightmare"
    tts_engine: str = "chatterbox_turbo"
    gameplay: Optional[str] = None

class LongformGenRequest(BaseModel):
    topic: str = "LetsNotMeet"
    source: str = "qwen_hermes" # "qwen_hermes", "reddit", "reddit_live", "curated", "gemini", "ollama", "custom"
    custom_text: Optional[str] = None
    voice: str = "mr_nightmare" # "mr_nightmare", "mr_nightmare_calm", "chilling_deep", "ryan_documentary"
    tts_engine: str = "chatterbox_turbo" # "chatterbox_turbo", "qwen3_tts"
    visual_mode: str = "hybrid_director" # "hybrid_director", "stock_footage", "local_ai_gpu", "local_library", "reddit_live"
    ambient_track: Optional[str] = None
    target_duration_minutes: float = 46.0
    max_stories: Optional[int] = None
    story_count: Optional[int] = 5
    segments: Optional[int] = None
    segments_count: Optional[int] = None

class EnqueueCreationRequest(BaseModel):
    video_path: Optional[str] = None
    relative_path: Optional[str] = None
    title: str = "Mr. Nightmare Studio Horror Video"
    target_platforms: List[str] = ["youtube_longform"]
    caption: Optional[str] = None
    type: Optional[str] = None

@router.get("/reddit_stories")
def api_get_reddit_stories(
    subreddit: str = "LetsNotMeet",
    timeframe: str = "all",
    limit: int = 15
):
    """Scrapes and returns real top-voted Reddit horror stories with upvotes, authors, and word counts."""
    stories = scrape_reddit_horror_stories(subreddit=subreddit, timeframe=timeframe, limit=limit)
    return {
        "status": "ok",
        "subreddit": subreddit,
        "count": len(stories),
        "stories": stories
    }

@router.get("/assets_status")
def get_assets_status():
    """Returns the file counts and contents of assets/gameplay, assets/ambient, and output folders."""
    gameplay_files = [f for f in os.listdir(GAMEPLAY_DIR) if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))] if os.path.isdir(GAMEPLAY_DIR) else []
    ambient_files = [f for f in os.listdir(AMBIENT_DIR) if f.lower().endswith((".mp3", ".wav", ".aac", ".ogg"))] if os.path.isdir(AMBIENT_DIR) else []
    shorts_files = [f for f in os.listdir(OUTPUT_SHORTS_DIR) if f.lower().endswith(".mp4")] if os.path.isdir(OUTPUT_SHORTS_DIR) else []
    longform_files = [f for f in os.listdir(OUTPUT_LONGFORM_DIR) if f.lower().endswith(".mp4")] if os.path.isdir(OUTPUT_LONGFORM_DIR) else []

    return {
        "gameplay_count": len(gameplay_files),
        "gameplay_files": gameplay_files[:10],
        "gameplay_videos": gameplay_files,
        "gameplay_folder": str(GAMEPLAY_DIR),
        "ambient_count": len(ambient_files),
        "ambient_files": ambient_files[:10],
        "ambient_audio": ambient_files,
        "ambient_folder": str(AMBIENT_DIR),
        "shorts_created_count": len(shorts_files),
        "longform_created_count": len(longform_files)
    }

@router.get("/recent_creations")
def get_recent_creations():
    """Returns a list of rendered shorts and long-form videos with preview URLs."""
    items = []
    
    if os.path.isdir(OUTPUT_SHORTS_DIR):
        for f in os.listdir(OUTPUT_SHORTS_DIR):
            if f.lower().endswith(".mp4"):
                full_p = os.path.join(OUTPUT_SHORTS_DIR, f)
                stat = os.stat(full_p)
                items.append({
                    "id": f,
                    "type": "short",
                    "filename": f,
                    "path": full_p,
                    "relative_path": os.path.relpath(full_p, ASSETS_DIR).replace("\\", "/"),
                    "url": f"/assets_media/output/shorts/{f}",
                    "size_bytes": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "created_at": stat.st_mtime
                })

    if os.path.isdir(OUTPUT_LONGFORM_DIR):
        for f in os.listdir(OUTPUT_LONGFORM_DIR):
            if f.lower().endswith(".mp4"):
                full_p = os.path.join(OUTPUT_LONGFORM_DIR, f)
                stat = os.stat(full_p)
                items.append({
                    "id": f,
                    "type": "longform",
                    "filename": f,
                    "path": full_p,
                    "relative_path": os.path.relpath(full_p, ASSETS_DIR).replace("\\", "/"),
                    "url": f"/assets_media/output/longform/{f}",
                    "size_bytes": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "created_at": stat.st_mtime
                })

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"creations": items[:30]}

def _compute_stage_eta(p: Dict[str, Any], now_ts: float) -> float:
    """Compute stage-aware rolling ETA instead of bogus (elapsed / percent) extrapolation."""
    stage = int(p.get("stage") or 0)
    total_stages = int(p.get("total_stages") or 5)
    step = int(p.get("current_step") or 0)
    total_steps = int(p.get("total_steps") or 0)
    total_scenes = int(p.get("total_scenes") or (total_steps if stage in (2, 3, 5) else 0))
    stage_start = float(p.get("stage_start_time") or p.get("start_time") or now_ts)
    stage_elapsed = max(0.0, now_ts - stage_start)

    if total_stages <= 4:
        # Short 9:16 pipeline (~20-60s)
        pct = float(p.get("percent") or 0.0)
        if pct >= 15.0 and p.get("start_time", 0) > 0:
            elapsed = max(0.0, now_ts - float(p["start_time"]))
            return max(0.0, round((elapsed / (pct / 100.0)) - elapsed, 1))
        return 0.0

    tts_engine = (p.get("tts_engine") or "chatterbox_turbo").lower()
    tts_rate = 24.0 if tts_engine == "chatterbox_turbo" else 180.0
    sd_est = 55.0 if total_scenes > 4 else 15.0
    render_rate = 2.5

    if stage <= 1:
        return 0.0
    if stage == 2:
        if step < 1 or total_steps <= 0:
            return 0.0
        cur_rem = (stage_elapsed / step) * max(0, total_steps - step)
        future_rem = (total_scenes * tts_rate) + sd_est + (total_scenes * render_rate)
        return round(cur_rem + future_rem, 1)
    if stage == 3:
        if step < 1 or total_steps <= 0:
            return 0.0
        sec_per_scene = stage_elapsed / step
        cur_rem = sec_per_scene * max(0, total_steps - step)
        future_rem = sd_est + (total_scenes * render_rate)
        return round(cur_rem + future_rem, 1)
    if stage == 4:
        cur_rem = max(5.0, sd_est - stage_elapsed)
        future_rem = total_scenes * render_rate
        return round(cur_rem + future_rem, 1)
    if stage >= 5:
        if total_steps > 0 and step >= total_steps:
            return 8.0
        if step < 1 or total_steps <= 0:
            return round(max(5.0, total_scenes * render_rate), 1)
        cur_rem = (stage_elapsed / step) * max(0, total_steps - step)
        return round(cur_rem + 8.0, 1)
    return 0.0


def _update_progress_cb(info: Dict[str, Any], default_total_stages: int = 5) -> None:
    now_ts = time.time()
    with _PROGRESS_LOCK:
        new_stage = int(info.get("stage", GLOBAL_PROGRESS["stage"]))
        if new_stage != GLOBAL_PROGRESS["stage"]:
            GLOBAL_PROGRESS["stage_start_time"] = now_ts
        GLOBAL_PROGRESS["stage"] = new_stage
        GLOBAL_PROGRESS["total_stages"] = info.get("total_stages", default_total_stages)
        GLOBAL_PROGRESS["stage_name"] = info.get("stage_name", GLOBAL_PROGRESS["stage_name"])
        GLOBAL_PROGRESS["current_step"] = info.get("current_step", GLOBAL_PROGRESS["current_step"])
        GLOBAL_PROGRESS["total_steps"] = info.get("total_steps", GLOBAL_PROGRESS["total_steps"])
        if info.get("total_scenes"):
            GLOBAL_PROGRESS["total_scenes"] = int(info["total_scenes"])
        if info.get("last_gen_sec") is not None and GLOBAL_PROGRESS["stage"] == 3:
            prev = float(GLOBAL_PROGRESS.get("avg_scene_sec") or 0.0)
            cur = float(info["last_gen_sec"])
            GLOBAL_PROGRESS["avg_scene_sec"] = round(cur if prev <= 0 else (0.7 * prev + 0.3 * cur), 2)
        GLOBAL_PROGRESS["percent"] = info.get("percent", GLOBAL_PROGRESS["percent"])
        if GLOBAL_PROGRESS["start_time"] > 0:
            GLOBAL_PROGRESS["elapsed_sec"] = round(now_ts - GLOBAL_PROGRESS["start_time"], 1)
        msg = info.get("message", "")
        if msg:
            GLOBAL_PROGRESS["message"] = msg
            ts = datetime.now().strftime("%H:%M:%S")
            GLOBAL_PROGRESS["recent_logs"].append(f"[{ts}] {msg}")
            if len(GLOBAL_PROGRESS["recent_logs"]) > 250:
                GLOBAL_PROGRESS["recent_logs"] = GLOBAL_PROGRESS["recent_logs"][-250:]


@router.get("/progress")
def get_generation_progress():
    """Returns the live progress status, percentage, current scene, elapsed time, and rolling ETA."""
    with _PROGRESS_LOCK:
        p = dict(GLOBAL_PROGRESS)
        if p["active"] and p["start_time"] > 0:
            now_ts = time.time()
            p["elapsed_sec"] = round(now_ts - p["start_time"], 1)
            p["eta_sec"] = _compute_stage_eta(p, now_ts)
        return p


@router.post("/cancel")
def cancel_generation():
    """Signals any active longform or short generation worker to abort cleanly and release GPU resources."""
    with _PROGRESS_LOCK:
        if not GLOBAL_PROGRESS["active"]:
            return {"ok": True, "active": False, "message": "No active generation to cancel."}
        _CANCEL_EVENT.set()
        GLOBAL_PROGRESS["message"] = "Cancelling active generation and freeing GPU resources..."
        ts = datetime.now().strftime("%H:%M:%S")
        GLOBAL_PROGRESS["recent_logs"].append(f"[{ts}] Cancellation requested by user...")
        return {"ok": True, "active": True, "job_id": GLOBAL_PROGRESS["job_id"], "message": "Cancellation requested."}


@router.post("/generate_short")
def api_generate_short(req: ShortGenRequest, sync: bool = False):
    """Generates a viral 9:16 vertical split-screen Short asynchronously (or synchronously if sync=True)."""
    with _PROGRESS_LOCK:
        if GLOBAL_PROGRESS["active"]:
            return {
                "status": "already_running",
                "job_id": GLOBAL_PROGRESS["job_id"],
                "message": "A video generation job is already active. Please wait for it to complete."
            }

        _CANCEL_EVENT.clear()
        now_ts = time.time()
        job_id = f"short_{uuid.uuid4().hex[:8]}"
        GLOBAL_PROGRESS["active"] = True
        GLOBAL_PROGRESS["job_id"] = job_id
        GLOBAL_PROGRESS["type"] = "short"
        GLOBAL_PROGRESS["status"] = "running"
        GLOBAL_PROGRESS["tts_engine"] = req.tts_engine
        GLOBAL_PROGRESS["stage"] = 1
        GLOBAL_PROGRESS["total_stages"] = 4
        GLOBAL_PROGRESS["stage_name"] = "Initializing"
        GLOBAL_PROGRESS["stage_start_time"] = now_ts
        GLOBAL_PROGRESS["current_step"] = 0
        GLOBAL_PROGRESS["total_steps"] = 1
        GLOBAL_PROGRESS["total_scenes"] = 1
        GLOBAL_PROGRESS["avg_scene_sec"] = 0.0
        GLOBAL_PROGRESS["percent"] = 2.0
        GLOBAL_PROGRESS["start_time"] = now_ts
        GLOBAL_PROGRESS["elapsed_sec"] = 0.0
        GLOBAL_PROGRESS["eta_sec"] = 0.0
        GLOBAL_PROGRESS["message"] = "Initializing 9:16 Viral Short Generator..."
        GLOBAL_PROGRESS["recent_logs"] = [f"[{datetime.now().strftime('%H:%M:%S')}] Started 9:16 viral short generation for topic '{req.topic}' (Engine: {req.tts_engine})"]
        GLOBAL_PROGRESS["result"] = None
        GLOBAL_PROGRESS["error"] = None

    def _progress_cb(info: Dict[str, Any]):
        _update_progress_cb(info, default_total_stages=4)

    def _worker():
        try:
            res = run_generate_short(
                source=req.source,
                topic=req.topic,
                voice=req.voice,
                gameplay=req.gameplay,
                progress_callback=_progress_cb,
                tts_engine=req.tts_engine,
                cancel_event=_CANCEL_EVENT,
            )
            rel = os.path.relpath(res["output_path"], ASSETS_DIR).replace("\\", "/")
            res["preview_url"] = f"/assets_media/{rel}"
            res["status"] = "ok"
            res["video_path"] = res["output_path"]
            res["duration"] = res.get("duration_sec", 0)

            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "completed"
                GLOBAL_PROGRESS["percent"] = 100.0
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["message"] = f"Short generated successfully ({res['duration']}s)"
                GLOBAL_PROGRESS["result"] = res
        except InterruptedError:
            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "cancelled"
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["message"] = "Short generation cancelled by user."
                _CANCEL_EVENT.clear()
        except Exception as e:
            import traceback
            traceback.print_exc()
            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "error"
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["error"] = str(e)
                GLOBAL_PROGRESS["message"] = f"Failed to generate short: {e}"
                _CANCEL_EVENT.clear()

    if sync:
        _worker()
        with _PROGRESS_LOCK:
            if GLOBAL_PROGRESS["status"] == "error":
                raise HTTPException(status_code=500, detail=GLOBAL_PROGRESS["error"])
            return GLOBAL_PROGRESS["result"]

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {
        "status": "started",
        "job_id": job_id,
        "type": "short",
        "message": "Viral Short generation started in background. Polling live progress."
    }

@router.post("/generate_longform")
def api_generate_longform(req: LongformGenRequest, sync: bool = False):
    """Generates a broadcast-quality Mr. Nightmare horror compilation asynchronously with live progress tracking."""
    with _PROGRESS_LOCK:
        if GLOBAL_PROGRESS["active"]:
            return {
                "status": "already_running",
                "job_id": GLOBAL_PROGRESS["job_id"],
                "message": "A video generation job is already active. Please wait for it to complete."
            }

        _CANCEL_EVENT.clear()
        now_ts = time.time()
        job_id = f"long_{uuid.uuid4().hex[:8]}"
        GLOBAL_PROGRESS["active"] = True
        GLOBAL_PROGRESS["job_id"] = job_id
        GLOBAL_PROGRESS["type"] = "longform"
        GLOBAL_PROGRESS["status"] = "running"
        GLOBAL_PROGRESS["tts_engine"] = req.tts_engine
        GLOBAL_PROGRESS["stage"] = 1
        GLOBAL_PROGRESS["total_stages"] = 5
        GLOBAL_PROGRESS["stage_name"] = "Initializing"
        GLOBAL_PROGRESS["stage_start_time"] = now_ts
        GLOBAL_PROGRESS["current_step"] = 0
        GLOBAL_PROGRESS["total_steps"] = 1
        GLOBAL_PROGRESS["total_scenes"] = 0
        GLOBAL_PROGRESS["avg_scene_sec"] = 0.0
        GLOBAL_PROGRESS["percent"] = 1.0
        GLOBAL_PROGRESS["start_time"] = now_ts
        GLOBAL_PROGRESS["elapsed_sec"] = 0.0
        GLOBAL_PROGRESS["eta_sec"] = 0.0
        GLOBAL_PROGRESS["message"] = f"Initializing Mr. Nightmare Horror Studio for {req.topic} ({req.target_duration_minutes}m target)..."
        GLOBAL_PROGRESS["recent_logs"] = [f"[{datetime.now().strftime('%H:%M:%S')}] Started Mr. Nightmare long-form job for topic '{req.topic}' (Target: {req.target_duration_minutes} min, Engine: {req.tts_engine})"]
        GLOBAL_PROGRESS["result"] = None
        GLOBAL_PROGRESS["error"] = None

    seg_count = req.segments or req.segments_count
    effective_max = req.max_stories if req.max_stories is not None else (req.story_count if req.target_duration_minutes >= 30 else None)

    def _progress_cb(info: Dict[str, Any]):
        _update_progress_cb(info, default_total_stages=5)

    def _worker():
        try:
            res = run_generate_longform(
                topic=req.topic,
                source=req.source,
                custom_text=req.custom_text,
                voice=req.voice,
                visual_mode=req.visual_mode,
                segments=seg_count,
                target_duration_minutes=req.target_duration_minutes,
                max_stories=effective_max,
                ambient_track=req.ambient_track,
                progress_callback=_progress_cb,
                tts_engine=req.tts_engine,
                cancel_event=_CANCEL_EVENT,
            )
            rel = os.path.relpath(res["output_path"], ASSETS_DIR).replace("\\", "/")
            res["preview_url"] = f"/assets_media/{rel}"
            res["status"] = "ok"
            res["video_path"] = res["output_path"]
            res["duration"] = res.get("duration_sec", 0)

            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "completed"
                GLOBAL_PROGRESS["percent"] = 100.0
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["message"] = f"Mr. Nightmare Video complete: {res.get('duration_min', 0)} min rendered in {res.get('elapsed_sec', 0)}s!"
                GLOBAL_PROGRESS["result"] = res
        except InterruptedError:
            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "cancelled"
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["message"] = "Long-form generation cancelled cleanly by user."
                _CANCEL_EVENT.clear()
        except Exception as e:
            import traceback
            traceback.print_exc()
            with _PROGRESS_LOCK:
                GLOBAL_PROGRESS["active"] = False
                GLOBAL_PROGRESS["status"] = "error"
                GLOBAL_PROGRESS["elapsed_sec"] = round(time.time() - GLOBAL_PROGRESS["start_time"], 1)
                GLOBAL_PROGRESS["eta_sec"] = 0.0
                GLOBAL_PROGRESS["error"] = str(e)
                GLOBAL_PROGRESS["message"] = f"Failed to generate horror video: {e}"
                _CANCEL_EVENT.clear()

    if sync:
        _worker()
        with _PROGRESS_LOCK:
            if GLOBAL_PROGRESS["status"] == "error":
                raise HTTPException(status_code=500, detail=GLOBAL_PROGRESS["error"])
            return GLOBAL_PROGRESS["result"]

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {
        "status": "started",
        "job_id": job_id,
        "type": "longform",
        "message": f"Mr. Nightmare {req.target_duration_minutes}m video generation started in background ({req.tts_engine}). Polling live progress."
    }

@router.post("/enqueue_creation")
def enqueue_creation(req: EnqueueCreationRequest, background_tasks: BackgroundTasks):
    """Enqueues an already-rendered Studio video directly into AutoPoster's smart publishing queue."""
    target_video = req.video_path or req.relative_path
    if not target_video:
        raise HTTPException(status_code=400, detail="Missing video_path or relative_path")

    resolved_path = target_video
    if not os.path.isfile(resolved_path):
        cands = [
            os.path.join(ASSETS_DIR, target_video),
            os.path.join(OUTPUT_SHORTS_DIR, os.path.basename(target_video)),
            os.path.join(OUTPUT_LONGFORM_DIR, os.path.basename(target_video))
        ]
        found = False
        for c in cands:
            if os.path.isfile(c):
                resolved_path = c
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail=f"Video file not found at: {target_video}")

    is_long = (req.type == "longform" or "output/longform" in resolved_path.replace("\\", "/") or "output\\longform" in resolved_path)
    created_ids = []
    caption = req.caption or f"{req.title} #horror #truescarystories #mrnightmare #creepy #redditstories"

    # For long-form horror videos, normalize target platforms so they post to YouTube Long-Form (not Shorts shelf!)
    targets = []
    for plat in req.target_platforms:
        if is_long:
            if plat in ("youtube", "youtube_shorts", "yt", "youtube_longform"):
                targets.append("youtube_longform")
            elif plat in ("tiktok", "instagram"):
                # If user selected tiktok/instagram for a 45m video, pass it through
                targets.append(plat)
        else:
            targets.append(plat)

    if not targets:
        targets = ["youtube_longform"] if is_long else ["youtube_shorts"]

    for plat in targets:
        recipe_to_use = "youtube_longform" if plat == "youtube_longform" else "ai_studio_generated"
        job_id = create_job(
            workflow_id=None,
            source_url="",
            target_platform=plat,
            recipe=recipe_to_use,
            metadata={
                "title": req.title,
                "caption": caption,
                "rendered_video": resolved_path,
                "is_pre_rendered": True,
                "is_shorts": not is_long
            }
        )
        created_ids.append(job_id)
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE jobs SET status = 'queued', current_step = 'process', step_progress = 75.0, processed_media_path = ? WHERE id = ?",
                (resolved_path, job_id)
            )
            conn.commit()
        finally:
            conn.close()

    return {
        "success": True,
        "job_ids": created_ids,
        "message": f"Successfully queued {len(created_ids)} post(s) into AutoPoster with dynamic cooldown."
    }
