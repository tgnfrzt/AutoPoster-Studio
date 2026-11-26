"""Backend Shorts horror voiceover engine powered by Qwen3-TTS 0.6B GPU."""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

QWEN_TTS_SERVER_URL = os.environ.get("QWEN_TTS_URL", "http://127.0.0.1:8880")
QWEN_VENV_PYTHON = r"E:\ai-agent\tts_lab\venv_qwen\Scripts\python.exe"
QWEN_CLI_SCRIPT = r"E:\ai-agent\tts_lab\qwen_tts_cli.py"

VOICE_PRESETS: Dict[str, Dict[str, Any]] = {
    "mr_nightmare": {
        "engine": "qwen3_tts",
        "voice": "mr_nightmare",
        "label": "🎙️ Mr. Nightmare (Qwen3-TTS · Ryan Dark)",
        "description": "Deep, calm late-night horror storyteller powered by Qwen3-TTS 0.6B GPU",
    },
    "mr_nightmare_calm": {
        "engine": "qwen3_tts",
        "voice": "mr_nightmare_calm",
        "label": "🕯️ Velvet Midnight (Qwen3-TTS · Aiden Calm)",
        "description": "Soft, unsettling midnight horror narrator with deliberate pauses",
    },
    "chilling_deep": {
        "engine": "qwen3_tts",
        "voice": "chilling_deep",
        "label": "🌑 Deep Suspense (Qwen3-TTS · True Crime)",
        "description": "Somber, serious investigative horror tone",
    },
    "dark_storyteller": {
        "engine": "qwen3_tts",
        "voice": "dark_storyteller",
        "label": "⚡ Viral Short Storyteller (Qwen3-TTS · Punchy)",
        "description": "Engaging fast-paced dark storyteller for 9:16 Shorts",
    },
    "ominous_british": {
        "engine": "qwen3_tts",
        "voice": "ominous_british",
        "label": "🏰 Ominous Gothic (Qwen3-TTS · Aiden)",
        "description": "Dark academic horror atmosphere",
    },
    "qwen_ryan_deep": {
        "engine": "qwen3_tts",
        "voice": "ryan_documentary",
        "label": "🎬 Ryan Documentary (Qwen3-TTS · Clear Deep)",
        "description": "Clean documentary narrator with strong emphasis",
    },
    "qwen_aiden_suspense": {
        "engine": "qwen3_tts",
        "voice": "aiden_narrator",
        "label": "🎧 Aiden Storyteller (Qwen3-TTS · Natural)",
        "description": "Warm natural storytelling cadence",
    },
}


def get_available_voices() -> List[Dict[str, Any]]:
    """Return list of voice presets for the frontend UI."""
    result = []
    for k, v in VOICE_PRESETS.items():
        item = dict(v)
        item["preset_id"] = k
        result.append(item)
    return result


def _find_ffmpeg() -> str:
    try:
        from core.config import settings

        if getattr(settings, "FFMPEG_PATH", "") and os.path.isfile(settings.FFMPEG_PATH):
            return settings.FFMPEG_PATH
    except Exception:
        pass
    return os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"


def _write_proportional_srt(text: str, total_duration: float, output_srt_path: str) -> None:
    words = text.split()
    dur = max(total_duration, 1.0)
    time_per_word = dur / max(1, len(words))

    def fmt_time(s: float) -> str:
        hrs = int(s // 3600)
        mins = int((s % 3600) // 60)
        secs = int(s % 60)
        ms = int((s - int(s)) * 1000)
        return f"{hrs:02d}:{mins:02d}:{secs:02d},{ms:03d}"

    lines = []
    chunk_size = 5
    for idx in range(0, len(words), chunk_size):
        chunk_words = words[idx : idx + chunk_size]
        start_sec = idx * time_per_word
        end_sec = min(dur, (idx + len(chunk_words)) * time_per_word)
        sub_idx = (idx // chunk_size) + 1
        lines.append(f"{sub_idx}\n{fmt_time(start_sec)} --> {fmt_time(end_sec)}\n{' '.join(chunk_words)}\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_srt_path)), exist_ok=True)
    with open(output_srt_path, "w", encoding="utf-8") as f_srt:
        f_srt.write("\n".join(lines))


async def _synthesize_edge_tts(
    text: str,
    output_audio_path: str,
    output_srt_path: str,
    voice: str = "en-US-ChristopherNeural",
    pitch: str = "-4Hz",
    rate: str = "-4%",
) -> float:
    import edge_tts
    from edge_tts import SubMaker

    submaker = SubMaker()
    comm = edge_tts.Communicate(text, voice, pitch=pitch, rate=rate)

    os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(output_srt_path)), exist_ok=True)

    with open(output_audio_path, "wb") as f_audio:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f_audio.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                submaker.feed(chunk)

    with open(output_srt_path, "w", encoding="utf-8") as f_srt:
        f_srt.write(submaker.get_srt())
    return 0.0


def _resolve_qwen_tts_url() -> str:
    try:
        import sys

        tts_lab = r"E:\ai-agent\tts_lab"
        if tts_lab not in sys.path:
            sys.path.insert(0, tts_lab)
        from qwen_tts_client import ensure_qwen_tts_server

        ensured = ensure_qwen_tts_server(QWEN_TTS_SERVER_URL)
        if ensured:
            return ensured
    except Exception:
        pass
    return QWEN_TTS_SERVER_URL.rstrip("/")


def _synthesize_qwen3(
    text: str,
    output_audio_path: str,
    output_srt_path: str,
    voice: str = "mr_nightmare",
) -> float:
    os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)
    wav_bytes = b""

    payload = json.dumps(
        {
            "model": "qwen3-tts-0.6b",
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "pause_sec": 0.25,
            "max_chunk_chars": 220,
        }
    ).encode("utf-8")
    try:
        server_url = _resolve_qwen_tts_url()
        req = urllib.request.Request(
            f"{server_url}/v1/audio/speech",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=240) as resp:
            if resp.status == 200:
                wav_bytes = resp.read()
    except Exception as exc:
        print(f"[TTS] Qwen3 server unreachable ({exc}); invoking venv_qwen CLI fallback...")

    with tempfile.TemporaryDirectory() as td:
        raw_wav = Path(td) / "qwen_raw.wav"
        if len(wav_bytes) > 1000:
            raw_wav.write_bytes(wav_bytes)
        else:
            if not (os.path.isfile(QWEN_VENV_PYTHON) and os.path.isfile(QWEN_CLI_SCRIPT)):
                raise RuntimeError("Qwen3-TTS server offline and CLI missing.")
            txt_file = Path(td) / "script.txt"
            txt_file.write_text(text, encoding="utf-8")
            env = dict(os.environ)
            env.setdefault("HF_HOME", r"E:\ai-agent\models\tts_lab\hf_cache")
            res = subprocess.run(
                [QWEN_VENV_PYTHON, QWEN_CLI_SCRIPT, "--text-file", str(txt_file), "--voice", voice, "--pause", "0.25", "--out", str(raw_wav)],
                env=env,
                capture_output=True,
                text=True,
                timeout=360,
            )
            if res.returncode != 0 or not raw_wav.is_file():
                raise RuntimeError(f"Qwen3 CLI failed: {(res.stderr or res.stdout)[-400:]}")

        ffmpeg = _find_ffmpeg()
        af = "highpass=f=65,equalizer=f=110:t=q:w=1.1:g=2.5,acompressor=threshold=-19dB:ratio=2.5:attack=12:release=140:makeup=2,loudnorm=I=-14:TP=-1.5:LRA=9"
        cmd = [ffmpeg, "-y", "-i", str(raw_wav), "-af", af]
        if output_audio_path.lower().endswith(".mp3"):
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k", output_audio_path])
        else:
            cmd.append(output_audio_path)
        subprocess.run(cmd, capture_output=True, check=True)

    duration = 0.0
    try:
        from utils.ffmpeg_runner import probe_video

        duration = float(probe_video(output_audio_path).get("duration") or 0.0)
    except Exception:
        pass
    _write_proportional_srt(text, duration, output_srt_path)
    return duration


def generate_horror_narration(
    text: str,
    output_audio_path: str,
    output_srt_path: str,
    preset_name: str = "mr_nightmare",
) -> Dict[str, Any]:
    """
    Main entrypoint for generating horror narration via Qwen3-TTS 0.6B GPU.
    Produces broadcast-mastered audio + synchronized .srt subtitles.
    """
    preset = VOICE_PRESETS.get(preset_name, VOICE_PRESETS["mr_nightmare"])
    engine = preset["engine"]

    try:
        _synthesize_qwen3(
            text=text,
            output_audio_path=output_audio_path,
            output_srt_path=output_srt_path,
            voice=preset["voice"],
        )
    except Exception as exc:
        print(f"[TTS] Qwen3-TTS failed ({exc}), falling back to edge-tts...")
        engine = "edge-tts"
        asyncio.run(
            _synthesize_edge_tts(
                text=text,
                output_audio_path=output_audio_path,
                output_srt_path=output_srt_path,
                voice="en-US-ChristopherNeural",
                pitch="-4Hz",
                rate="-4%",
            )
        )

    try:
        from utils.ffmpeg_runner import probe_video

        duration = float(probe_video(output_audio_path).get("duration") or 0.0)
    except Exception:
        duration = 0.0

    return {
        "audio_path": output_audio_path,
        "srt_path": output_srt_path,
        "duration": duration,
        "preset_used": preset_name,
        "engine": engine,
    }
