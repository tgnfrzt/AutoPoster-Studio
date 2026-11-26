import json
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
try:
    from core.config import settings, get_cookies_path
    from utils.ffmpeg_runner import register_job_process, unregister_job_process
except ImportError:
    from backend.core.config import settings, get_cookies_path
    from backend.utils.ffmpeg_runner import register_job_process, unregister_job_process

class AgeRestrictedError(RuntimeError):
    """Raised when YouTube or another platform requires age confirmation or sign-in."""
    pass

def download_video(
    url: str,
    output_path: str,
    cookies_path: Optional[str] = None,
    job_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Downloads a video from url using yt-dlp to output_path.
    Automatically resolves and attaches cookies.txt to avoid Instagram CSRF blocks.
    Registers active process for job cancellation.
    Returns metadata dictionary including title, duration, dimensions.
    """
    out_dir = os.path.dirname(output_path)
    os.makedirs(out_dir, exist_ok=True)

    # Resolve cookies if not explicitly passed
    resolved_cookies = cookies_path or get_cookies_path()

    cmd = [
        settings.YTDLP_PATH,
        "--no-playlist",
        "--merge-output-format", "mp4",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "-o", output_path,
        "--force-overwrites",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "--socket-timeout", "30",
        "--retries", "3",
        "--no-simulate",
        "--print-json",
        url
    ]

    if resolved_cookies and os.path.isfile(resolved_cookies):
        print(f"[Downloader] Using cookies from: {resolved_cookies}")
        cmd.extend(["--cookies", resolved_cookies])
    else:
        print("[Downloader] Warning: No cookies.txt found. Instagram downloads may require cookies.")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace"
    )

    if job_id:
        register_job_process(job_id, proc)

    try:
        stdout, stderr = proc.communicate()
    finally:
        if job_id:
            unregister_job_process(job_id, proc)

    # Check for age and audience restriction errors
    err_lower = (stderr or "").lower()
    if (
        "sign in to confirm your age" in err_lower
        or "confirm your age" in err_lower
        or "inappropriate for some users" in err_lower
        or "age-restricted" in err_lower
        or "isn't available to everyone" in err_lower
        or "certain audiences" in err_lower
    ):
        raise AgeRestrictedError(f"Video is age-restricted or audience-locked and requires login/cookies: {stderr[-400:]}")

    metadata = {}
    if stdout:
        try:
            for line in stdout.splitlines():
                if line.strip().startswith("{") and line.strip().endswith("}"):
                    metadata = json.loads(line)
                    break
        except Exception:
            pass

    # Check if yt-dlp produced the file (or with another container extension)
    if not os.path.isfile(output_path):
        stem = os.path.splitext(output_path)[0]
        for ext in (".mp4", ".mkv", ".webm"):
            alt = stem + ext
            if os.path.isfile(alt):
                if alt != output_path:
                    if os.path.isfile(output_path):
                        os.remove(output_path)
                    os.rename(alt, output_path)
                break

    if not os.path.isfile(output_path):
        err_msg = stderr[-600:] if stderr else "Unknown error"
        raise RuntimeError(f"Download failed: yt-dlp did not produce output file. Stderr: {err_msg}")

    return {
        "file_path": output_path,
        "title": metadata.get("title", ""),
        "description": metadata.get("description", ""),
        "duration": metadata.get("duration", 0),
        "uploader": metadata.get("uploader", ""),
        "webpage_url": metadata.get("webpage_url", url),
    }
