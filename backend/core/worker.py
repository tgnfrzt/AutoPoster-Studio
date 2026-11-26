import time
import threading
import requests
from datetime import datetime
from typing import Optional
from core.config import settings
from core.database import get_db
from core.workflow_engine import execute_job, create_job
from modules.sources.sanitizer import clean_url, is_duplicate
from modules.dispatch.scheduler import evaluate_posting_schedule

_worker_running = False
_worker_thread: Optional[threading.Thread] = None
_pull_thread: Optional[threading.Thread] = None

_last_pull_info = {
    "last_check_time": None,
    "last_success_time": None,
    "last_count": 0,
    "last_status": "Not started yet"
}

def get_pull_status() -> dict:
    return {
        **_last_pull_info,
        "interval_min": settings.PULL_INTERVAL_MIN,
        "remote_url": settings.REMOTE_HOSTING_URL
    }

def pull_remote_links() -> int:
    """
    Pulls link list accumulated on the PythonAnywhere server (populated 24/7 by MacroDroid on mobile).
    Supports both /get_and_clear_queue and /list + /clear endpoints.
    Sanitizes, deduplicates, and enqueues new unique links into the local database queue.
    """
    global _last_pull_info
    now_iso = datetime.now().strftime("%H:%M:%S")
    _last_pull_info["last_check_time"] = now_iso

    if not settings.REMOTE_HOSTING_URL:
        _last_pull_info["last_status"] = "No remote hosting URL configured"
        return 0

    incoming_links = []
    host = settings.REMOTE_HOSTING_URL.rstrip("/")

    # Strategy 1: /get_and_clear_queue (Standard JSON endpoint from flask_app.py)
    try:
        url = f"{host}/get_and_clear_queue"
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            try:
                data = r.json()
                incoming_links = data.get("links", [])
            except Exception:
                pass
    except Exception as e:
        print(f"[Worker] /get_and_clear_queue attempt: {e}")

    # Strategy 2: Fallback to /list + /clear (Plain text list)
    if not incoming_links:
        try:
            url_list = f"{host}/list"
            r = requests.get(url_list, timeout=15)
            if r.status_code == 200 and r.text.strip():
                incoming_links = [l.strip() for l in r.text.splitlines() if l.strip()]
                # Clear server list
                try:
                    requests.get(f"{host}/clear", timeout=10)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Worker] /list fallback attempt: {e}")

    if not incoming_links:
        _last_pull_info["last_count"] = 0
        _last_pull_info["last_status"] = f"Checked at {now_iso} - Server queue was empty"
        print(f"[Worker] Checked PythonAnywhere at {now_iso}: 0 new links in queue.")
        return 0

    new_count = 0
    for raw in incoming_links:
        clean = clean_url(raw)
        if clean and not is_duplicate(clean):
            create_job(
                workflow_id=None,
                source_url=clean,
                target_platform="instagram",
                recipe="standard",
                metadata={
                    "caption": settings.DEFAULT_INSTAGRAM_CAPTION,
                    "watermark_alpha": 1.0,
                    "stretch_to_9x16": True
                }
            )
            new_count += 1

    _last_pull_info["last_count"] = new_count
    if new_count > 0:
        _last_pull_info["last_success_time"] = now_iso
        _last_pull_info["last_status"] = f"Pulled {new_count} new link(s) at {now_iso}"
        print(f"[Worker] Successfully pulled {new_count} new link(s) from PythonAnywhere into local queue.")
    else:
        _last_pull_info["last_status"] = f"Checked at {now_iso}: {len(incoming_links)} received but all duplicates"
        print(f"[Worker] Checked PythonAnywhere: {len(incoming_links)} link(s) received, but all were already in local history.")

    return new_count

def run_pull_loop():
    """Dedicated background loop checking PythonAnywhere periodically without blocking video execution."""
    global _worker_running
    print(f"[Worker] Background pull loop started (interval: {settings.PULL_INTERVAL_MIN}m).")

    while _worker_running:
        try:
            pull_remote_links()
        except Exception as exc:
            print(f"[Worker] Error in pull_remote_links: {exc}")

        # Sleep interval between pulls
        poll_interval_sec = max(30, int(settings.PULL_INTERVAL_MIN * 60))
        for _ in range(poll_interval_sec):
            if not _worker_running:
                break
            time.sleep(1)

def run_worker_loop():
    """Main background loop checking queue and evaluating posting scheduler."""
    global _worker_running
    print("[Worker] Background scheduler and queue worker started.")

    # 0. Startup recovery: reset any jobs stuck in downloading/processing state from a previous restart
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE jobs SET status = 'queued' WHERE status IN ('downloading', 'processing', 'dispatching', 'publishing', 'splitting')")
        recovered = cursor.rowcount
        conn.commit()
        conn.close()
        if recovered > 0:
            print(f"[Worker] Recovered {recovered} previously interrupted job(s) back to 'queued'.")
    except Exception as exc:
        print(f"[Worker] Error recovering interrupted jobs: {exc}")

    while _worker_running:
        # Independent per-platform queue dispatcher: Instagram, TikTok, and YouTube
        for plat in ("instagram", "tiktok", "youtube"):
            if not _worker_running:
                break
            try:
                can_post, batch_count, reason = evaluate_posting_schedule(plat)
                if not can_post:
                    continue

                if plat == "instagram":
                    plat_clause = "LOWER(target_platform) IN ('instagram', 'instagram_reels', 'ig')"
                elif plat == "tiktok":
                    plat_clause = "LOWER(target_platform) IN ('tiktok', 'tt')"
                elif plat == "youtube":
                    plat_clause = "LOWER(target_platform) IN ('youtube', 'youtube_shorts', 'yt', 'youtube_longform')"
                else:
                    plat_clause = "LOWER(target_platform) = ?"

                conn = get_db()
                cursor = conn.cursor()
                query = f"""
                    SELECT * FROM jobs 
                    WHERE status = 'queued' AND {plat_clause}
                    ORDER BY 
                        CASE WHEN series_id IS NOT NULL THEN 0 ELSE 1 END,
                        series_id ASC,
                        series_order ASC,
                        created_at ASC 
                    LIMIT 1
                """
                cursor.execute(query)
                job_row = cursor.fetchone()
                conn.close()

                if job_row:
                    series_tag = f"[{job_row['series_id']} Part {job_row['series_order']}] " if job_row["series_id"] else ""
                    print(f"[Worker] Dispatching {plat.upper()} job {series_tag}{job_row['id']}: {reason}")
                    execute_job(job_row["id"])

            except Exception as e:
                print(f"[Worker] Loop exception for {plat}: {e}")

        # Sleep interval between queue scans
        for _ in range(15):
            if not _worker_running:
                break
            time.sleep(1)

def start_worker():
    global _worker_running, _worker_thread, _pull_thread
    if not _worker_running:
        _worker_running = True
        _worker_thread = threading.Thread(target=run_worker_loop, daemon=True)
        _worker_thread.start()
        _pull_thread = threading.Thread(target=run_pull_loop, daemon=True)
        _pull_thread.start()

def stop_worker():
    global _worker_running
    _worker_running = False

