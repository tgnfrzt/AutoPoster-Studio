import json
import os
import subprocess
import threading
from typing import Dict, Any, Optional, List
try:
    from core.config import settings
except ImportError:
    from backend.core.config import settings

_nvenc_available: Optional[bool] = None
_active_job_procs: Dict[str, List[subprocess.Popen]] = {}
_proc_lock = threading.Lock()

def register_job_process(job_id: str, proc: subprocess.Popen) -> None:
    """Registers an active background subprocess (FFmpeg, yt-dlp, etc.) for a specific job."""
    if not job_id:
        return
    with _proc_lock:
        if job_id not in _active_job_procs:
            _active_job_procs[job_id] = []
        _active_job_procs[job_id].append(proc)

def unregister_job_process(job_id: str, proc: subprocess.Popen) -> None:
    """Unregisters a completed subprocess."""
    if not job_id:
        return
    with _proc_lock:
        if job_id in _active_job_procs and proc in _active_job_procs[job_id]:
            _active_job_procs[job_id].remove(proc)

def kill_job_processes(job_id: str) -> int:
    """Instantly kills all active OS subprocesses running for a specific job."""
    if not job_id:
        return 0
    with _proc_lock:
        procs = _active_job_procs.pop(job_id, [])
    killed = 0
    for p in procs:
        try:
            p.kill()
            killed += 1
        except Exception:
            pass
    return killed

def is_nvenc_available() -> bool:
    """Checks if NVIDIA NVENC hardware acceleration is functional on this system."""
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available
    try:
        cmd = [
            settings.FFMPEG_PATH,
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
            "-c:v", "h264_nvenc",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        _nvenc_available = (res.returncode == 0)
    except Exception:
        _nvenc_available = False
    return _nvenc_available

def get_fast_encoder_args(quality: int = 21, preset: str = "p4") -> List[str]:
    """
    Returns hardware-accelerated NVENC video encoding arguments for NVIDIA GTX 1650,
    falling back to CPU libx264 if NVENC is unavailable.
    """
    if is_nvenc_available():
        return [
            "-c:v", "h264_nvenc",
            "-preset", preset,
            "-cq", str(quality),
            "-b:v", "3200k",
            "-maxrate", "4500k",
            "-bufsize", "8000k",
            "-spatial-aq", "1",
            "-temporal-aq", "1"
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(quality)
    ]

def probe_video(file_path: str) -> Dict[str, Any]:
    """Inspects a video file and returns its technical metadata using ffprobe."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    cmd = [
        settings.FFPROBE_PATH,
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json",
        file_path
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=True)
    data = json.loads(res.stdout)

    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration", 0.0))
    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0

    fps = 30.0
    if video_stream and "r_frame_rate" in video_stream:
        try:
            num, den = video_stream["r_frame_rate"].split("/")
            fps = float(num) / float(den)
        except Exception:
            fps = 30.0

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": audio_stream is not None,
        "audio_sample_rate": audio_stream.get("sample_rate") if audio_stream else None,
        "audio_channels": audio_stream.get("channels") if audio_stream else None,
        "size_bytes": int(data.get("format", {}).get("size", 0)),
    }

def run_ffmpeg(args: List[str], job_id: Optional[str] = None) -> None:
    """Executes FFmpeg with process tracking and cancellation support."""
    cmd = [settings.FFMPEG_PATH, "-y", "-hide_banner", "-loglevel", "error"] + args
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

    if proc.returncode != 0:
        # Check if process was killed intentionally
        if proc.returncode in (-9, 1, 15) and "kill" in stderr.lower():
            raise InterruptedError(f"FFmpeg process was stopped for job {job_id}")
        err_msg = stderr[-600:] if stderr else "Unknown FFmpeg failure"
        raise RuntimeError(f"FFmpeg error ({proc.returncode}): {err_msg}")
