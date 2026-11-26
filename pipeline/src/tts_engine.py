"""Unified GPU TTS engine supporting batched Chatterbox Turbo (default) and Qwen3-TTS 0.6B."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import soundfile as sf

from .config import FFMPEG_PATH, FFPROBE_PATH

QWEN_TTS_SERVER_URL = os.environ.get("QWEN_TTS_URL", "http://127.0.0.1:8880")
QWEN_VENV_PYTHON = r"E:\ai-agent\tts_lab\venv_qwen\Scripts\python.exe"
QWEN_CLI_SCRIPT = r"E:\ai-agent\tts_lab\qwen_tts_cli.py"

CHATTERBOX_VENV_PYTHON = r"E:\ai-agent\tts_lab\venv_chatterbox\Scripts\python.exe"
CHATTERBOX_WORKER_SCRIPT = str(Path(__file__).resolve().parent / "chatterbox_tts_worker.py")

# Clean studio Ryan voice reference first; 05 has baked-in reverb so keep as secondary fallback
CHATTERBOX_REF_CANDIDATES: Dict[str, List[str]] = {
    "mr_nightmare": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
    "mr_nightmare_calm": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
    "chilling_deep": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
    "ryan_documentary": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
    "dark_storyteller": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
    "ominous_british": [
        r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
        r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
    ],
}
DEFAULT_CHATTERBOX_REFS = [
    r"E:\ai-agent\tts_lab\samples\02_qwen3_tts_0.6b_ryan.wav",
    r"E:\ai-agent\tts_lab\samples\05_autoposter_mr_nightmare_qwen3.wav",
]

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _pick_chatterbox_reference(voice_name: str) -> str:
    key = (voice_name or "mr_nightmare").strip().lower()
    cands = CHATTERBOX_REF_CANDIDATES.get(key, DEFAULT_CHATTERBOX_REFS)
    for path in cands:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"No Chatterbox reference WAV found in: {cands}")


def _unload_qwen_tts_server() -> None:
    """Best-effort unload of Qwen3-TTS model from GTX 1650 VRAM before other GPU tasks."""
    url = f"{QWEN_TTS_SERVER_URL.rstrip('/')}/audio/unload"
    try:
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp.read()
    except Exception:
        pass


def get_audio_duration(file_path: str) -> float:
    """Use ffprobe to obtain exact audio duration in seconds."""
    if not os.path.isfile(file_path):
        return 0.0
    try:
        cmd = [
            FFPROBE_PATH,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        dur = float(res.stdout.strip())
        if dur > 0:
            return dur
    except Exception:
        pass

    size = os.path.getsize(file_path)
    if file_path.lower().endswith(".wav"):
        return max(1.0, size / (24000 * 2))
    return max(1.0, size / 24000.0)


def split_into_sentence_batches(text: str, max_batch_chars: int = 220) -> List[str]:
    """
    Split text into natural 1-to-2 sentence batches so TTS synthesizes
    smooth multi-sentence prosody with deliberate storyteller pauses between batches.
    """
    text = re.sub(r"\.{2,}", ", ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    batches: List[str] = []
    current = ""

    for idx, s in enumerate(raw_sentences):
        if idx == 0 and s.lower().startswith("story number"):
            batches.append(s)
            continue

        if len(s) > max_batch_chars and any(sep in s for sep in (",", "—", ";")):
            if current:
                batches.append(current)
                current = ""
            subparts = re.split(r"(?<=[,;—])\s+", s)
            sub_cur = ""
            for p in subparts:
                if len(sub_cur) + len(p) + 1 <= max_batch_chars:
                    sub_cur = f"{sub_cur} {p}".strip() if sub_cur else p
                else:
                    if sub_cur:
                        batches.append(sub_cur)
                    sub_cur = p
            if sub_cur:
                batches.append(sub_cur)
        else:
            if current and (len(current) + 1 + len(s) <= max_batch_chars):
                current = f"{current} {s}"
            else:
                if current:
                    batches.append(current)
                current = s

    if current:
        batches.append(current)
    return batches


def _master_wav_to_mp3(
    temp_wav: str,
    output_path: str,
    dark_mastering: bool = True,
    voice_name: str = "mr_nightmare",
) -> float:
    """Apply broadcast dark condenser mastering and encode WAV -> MP3/WAV, removing temp_wav."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    voice_key = (voice_name or "mr_nightmare").strip().lower()

    if dark_mastering:
        if voice_key in ("mr_nightmare_calm", "soothing_horror", "ominous_british"):
            rate_part = "asetrate=22400,aresample=44100,atempo=0.98,"
        else:
            rate_part = "asetrate=22800,aresample=44100,atempo=1.01,"
        af_chain = (
            f"{rate_part}"
            "highpass=f=60,"
            "equalizer=f=105:t=q:w=1.1:g=3.5,"
            "equalizer=f=3000:t=q:w=1.4:g=-2.0,"
            "lowpass=f=11500,"
            "acompressor=threshold=-20dB:ratio=2.5:attack=15:release=180:makeup=2,"
            "aecho=0.8:0.85:28:0.06,"
            "loudnorm=I=-15:TP=-1.5:LRA=9"
        )
    else:
        af_chain = (
            "highpass=f=65,"
            "equalizer=f=115:t=q:w=1.1:g=2.5,"
            "equalizer=f=3200:t=q:w=1.4:g=-1.5,"
            "acompressor=threshold=-18dB:ratio=2.5:attack=10:release=120:makeup=2,"
            "loudnorm=I=-14:TP=-1.5:LRA=9"
        )

    if output_path.lower().endswith(".mp3"):
        cmd = [FFMPEG_PATH, "-y", "-i", temp_wav, "-af", af_chain, "-c:a", "libmp3lame", "-b:a", "192k", output_path]
    else:
        cmd = [FFMPEG_PATH, "-y", "-i", temp_wav, "-af", af_chain, output_path]

    subprocess.run(cmd, capture_output=True, check=True, creationflags=CREATE_NO_WINDOW)
    try:
        if os.path.abspath(temp_wav) != os.path.abspath(output_path):
            os.remove(temp_wav)
    except OSError:
        pass
    return get_audio_duration(output_path)


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


def _request_qwen3_chunk_wav(text: str, voice_name: str, out_wav: Path) -> bool:
    """Call local Qwen3-TTS HTTP server (:8880). Only falls back to CLI if server is unreachable."""
    payload = json.dumps(
        {
            "model": "qwen3-tts-0.6b",
            "input": text,
            "voice": voice_name,
            "response_format": "wav",
            "pause_sec": 0.16,
            "max_chunk_chars": 230,
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
                if len(wav_bytes) > 1000:
                    out_wav.write_bytes(wav_bytes)
                    return True
    except Exception as exc:
        # Never spawn a competing 2nd Qwen3-TTS subprocess onto CPU while :8880 holds VRAM
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            raise RuntimeError(f"Qwen3-TTS server timed out after 240s on chunk: {text[:40]}...") from exc
        print(f"[TTSEngine] Qwen3-TTS server unreachable ({exc}); using venv_qwen CLI fallback...")

    if os.path.isfile(QWEN_VENV_PYTHON) and os.path.isfile(QWEN_CLI_SCRIPT):
        env = dict(os.environ)
        env.setdefault("HF_HOME", r"E:\ai-agent\models\tts_lab\hf_cache")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as tf:
            tf.write(text)
            txt_file = tf.name
        try:
            res = subprocess.run(
                [
                    QWEN_VENV_PYTHON,
                    QWEN_CLI_SCRIPT,
                    "--text-file", txt_file,
                    "--voice", voice_name,
                    "--out", str(out_wav),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=360,
                creationflags=CREATE_NO_WINDOW,
            )
            if res.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 1000:
                return True
            print(f"[TTSEngine] Qwen3-TTS CLI error: {(res.stderr or res.stdout)[-400:]}")
        finally:
            try:
                os.remove(txt_file)
            except OSError:
                pass
    return False


def synthesize_qwen3_chunked(
    text: str,
    output_path: str,
    voice_name: str = "mr_nightmare",
    dark_mastering: bool = True,
    cancel_event: Optional[Any] = None,
) -> float:
    """
    Synthesize narration using Qwen3-TTS 0.6B GPU with deliberate storyteller pauses
    and broadcast condenser mic mastering.
    """
    batches = split_into_sentence_batches(text, max_batch_chars=220 if dark_mastering else 260)
    if not batches:
        return 0.0

    sample_rate = 24000
    audio_segments: List[np.ndarray] = [np.zeros(int(0.20 * sample_rate), dtype=np.float32)]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        for idx, batch_text in enumerate(batches):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            clean_s = batch_text.strip()
            if not clean_s:
                continue
            if not clean_s.endswith((".", "!", "?")):
                clean_s += "."
            chunk_wav = tmp_root / f"chunk_{idx:03d}.wav"
            if not _request_qwen3_chunk_wav(clean_s, voice_name, chunk_wav):
                raise RuntimeError(f"Qwen3-TTS synthesis failed on chunk: {clean_s[:40]}...")

            samples, sr = sf.read(str(chunk_wav), dtype="float32")
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            if sr != sample_rate and len(samples) > 0:
                n_out = max(1, int(round(len(samples) * sample_rate / float(sr))))
                samples = np.interp(
                    np.linspace(0.0, len(samples) - 1, num=n_out),
                    np.arange(len(samples)),
                    samples,
                ).astype(np.float32)

            non_silent = np.where(np.abs(samples) > 0.002)[0]
            if len(non_silent) > 0:
                start_i = max(0, int(non_silent[0]) - int(0.03 * sample_rate))
                end_i = min(len(samples), int(non_silent[-1]) + int(0.07 * sample_rate))
                samples = samples[start_i:end_i]
            audio_segments.append(samples)

            if dark_mastering:
                pause_sec = 0.65 if (idx == 0 and clean_s.lower().startswith("story number")) else 0.40
            else:
                pause_sec = 0.18
            audio_segments.append(np.zeros(int(pause_sec * sample_rate), dtype=np.float32))

    if len(audio_segments) <= 1:
        raise RuntimeError("Qwen3-TTS synthesis produced no audio frames.")

    final_audio = np.concatenate(audio_segments)
    temp_wav = output_path[:-4] + "_raw.wav" if "." in os.path.basename(output_path) else output_path + "_raw.wav"
    sf.write(temp_wav, final_audio, sample_rate)
    return _master_wav_to_mp3(temp_wav, output_path, dark_mastering=dark_mastering, voice_name=voice_name)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_chatterbox_batch(
    scene_items: List[Dict[str, Any]],
    run_tmp_dir: Path,
    uid: str,
    voice: str = "mr_nightmare",
    dark_mastering: bool = True,
    progress_callback: Optional[Callable[..., Any]] = None,
    cancel_event: Optional[Any] = None,
) -> float:
    """Synthesize all scenes in one persistent Chatterbox Turbo CUDA worker process."""
    if cancel_event and cancel_event.is_set():
        raise InterruptedError("Cancelled by user")

    if not os.path.isfile(CHATTERBOX_VENV_PYTHON) or not os.path.isfile(CHATTERBOX_WORKER_SCRIPT):
        raise FileNotFoundError("Chatterbox Turbo worker environment not found")

    _unload_qwen_tts_server()
    ref_wav = _pick_chatterbox_reference(voice)
    run_tmp_dir = Path(run_tmp_dir)
    run_tmp_dir.mkdir(parents=True, exist_ok=True)

    manifest_scenes = []
    for idx, item in enumerate(scene_items):
        raw_wav = run_tmp_dir / f"cb_raw_{uid}_{idx}.wav"
        manifest_scenes.append({
            "index": idx,
            "text": item.get("text", ""),
            "output_wav": str(raw_wav),
            "max_batch_chars": 220 if dark_mastering else 250,
        })

    manifest_path = run_tmp_dir / f"chatterbox_manifest_{uid}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "reference_wav": ref_wav,
                "dark_mastering": dark_mastering,
                "scenes": manifest_scenes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env.setdefault("HF_HOME", r"E:\ai-agent\models\tts_lab\hf_cache")
    env.setdefault("TORCH_HOME", r"E:\models\torch")

    proc = subprocess.Popen(
        [CHATTERBOX_VENV_PYTHON, "-u", CHATTERBOX_WORKER_SCRIPT, "--manifest", str(manifest_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )

    stderr_tail: deque = deque(maxlen=80)

    def _drain_stderr() -> None:
        if not proc.stderr:
            return
        try:
            for line in proc.stderr:
                stderr_tail.append(line.rstrip())
        except Exception:
            pass

    stop_watch = threading.Event()

    def _watch_cancel() -> None:
        if not cancel_event:
            return
        while not stop_watch.is_set() and proc.poll() is None:
            if cancel_event.is_set():
                print(f"[TTSEngine] Cancellation requested — terminating Chatterbox worker (PID {proc.pid})...")
                _kill_process_tree(proc)
                return
            time.sleep(0.25)

    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()
    t_cancel = threading.Thread(target=_watch_cancel, daemon=True)
    t_cancel.start()

    total_dur = 0.0
    completed_indices = set()
    total_scenes = len(scene_items)

    try:
        if proc.stdout:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    print(f"[ChatterboxWorker] {line}")
                    continue

                ev_type = ev.get("event")
                if ev_type == "ready":
                    print(
                        f"[TTSEngine] Chatterbox Turbo GPU ready ({ev.get('device')}) "
                        f"in {ev.get('load_sec')}s | ref={Path(ref_wav).name}"
                    )
                elif ev_type == "scene_done":
                    idx = int(ev["index"])
                    raw_wav = ev["output_wav"]
                    gen_sec = float(ev.get("gen_sec", 0.0))
                    seg_audio = str(run_tmp_dir / f"seg_{uid}_{idx}.mp3")
                    dur = _master_wav_to_mp3(
                        raw_wav,
                        seg_audio,
                        dark_mastering=dark_mastering,
                        voice_name=voice,
                    )
                    scene_items[idx]["audio_path"] = seg_audio
                    scene_items[idx]["duration"] = dur
                    total_dur += dur
                    completed_indices.add(idx)

                    if progress_callback:
                        try:
                            progress_callback(len(completed_indices), total_scenes, dur, gen_sec, scene_items[idx])
                        except Exception:
                            pass
                    print(
                        f"    Scene {len(completed_indices)}/{total_scenes} "
                        f"[{scene_items[idx].get('visual_type')}|sfx={scene_items[idx].get('sfx_cue')}]: "
                        f"{dur:.1f}s in {gen_sec:.1f}s (cumul: {total_dur/60.0:.1f}m) | "
                        f"'{scene_items[idx]['text'][:40]}...'"
                    )
        rc = proc.wait()
    finally:
        stop_watch.set()
        _kill_process_tree(proc)
        t_err.join(timeout=2.0)

    if cancel_event and cancel_event.is_set():
        raise InterruptedError("Cancelled by user")

    if rc != 0 or len(completed_indices) != total_scenes:
        tail = "\n".join(stderr_tail)[-600:]
        raise RuntimeError(
            f"Chatterbox Turbo batch failed (rc={rc}, completed {len(completed_indices)}/{total_scenes}).\n{tail}"
        )

    return total_dur


def synthesize_scenes_batch(
    scene_items: List[Dict[str, Any]],
    run_tmp_dir: Path,
    uid: str,
    voice: str = "mr_nightmare",
    tts_engine: str = "chatterbox_turbo",
    dark_mastering: bool = True,
    progress_callback: Optional[Callable[..., Any]] = None,
    cancel_event: Optional[Any] = None,
) -> float:
    """
    Synthesize all scenes using the selected GPU engine:
    - 'chatterbox_turbo' (default): persistent CUDA fp32 worker (~0.97x RTF)
    - 'qwen3_tts': Qwen3-TTS 0.6B GPU server with clean teardown
    """
    if not scene_items:
        return 0.0

    engine_key = (tts_engine or "chatterbox_turbo").strip().lower()
    if engine_key in ("chatterbox", "chatterbox_turbo", "turbo") and os.path.isfile(CHATTERBOX_VENV_PYTHON):
        return _run_chatterbox_batch(
            scene_items=scene_items,
            run_tmp_dir=run_tmp_dir,
            uid=uid,
            voice=voice,
            dark_mastering=dark_mastering,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

    # Qwen3-TTS path
    total_dur = 0.0
    total_scenes = len(scene_items)
    try:
        for idx, item in enumerate(scene_items):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            t0 = time.perf_counter()
            seg_audio = str(Path(run_tmp_dir) / f"seg_{uid}_{idx}.mp3")
            dur = synthesize_qwen3_chunked(
                text=item["text"],
                output_path=seg_audio,
                voice_name=voice,
                dark_mastering=dark_mastering,
                cancel_event=cancel_event,
            )
            gen_sec = round(time.perf_counter() - t0, 2)
            item["audio_path"] = seg_audio
            item["duration"] = dur
            total_dur += dur
            if progress_callback:
                try:
                    progress_callback(idx + 1, total_scenes, dur, gen_sec, item)
                except Exception:
                    pass
            print(
                f"    Scene {idx+1}/{total_scenes} [{item.get('visual_type')}|sfx={item.get('sfx_cue')}]: "
                f"{dur:.1f}s in {gen_sec:.1f}s (cumul: {total_dur/60.0:.1f}m) | '{item['text'][:42]}...'"
            )
    finally:
        _unload_qwen_tts_server()

    return total_dur


async def _synthesize_edge_tts(text: str, voice: str, output_path: str, rate: str = "+0%", pitch: str = "+0Hz"):
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)


def generate_audio(
    text: str,
    voice: str = "mr_nightmare",
    output_path: str = "narration.mp3",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    tts_engine: str = "chatterbox_turbo",
    cancel_event: Optional[Any] = None,
) -> float:
    """
    Unified single-clip voice synthesis entrypoint (Shorts or standalone callers):
    Uses Chatterbox Turbo GPU by default or Qwen3-TTS 0.6B GPU, with edge-tts as last-resort fallback.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    is_fast_short = "+10%" in rate or "+15%" in rate or voice in ("shorts", "fast", "energetic")
    dark_mastering = not is_fast_short
    engine_key = (tts_engine or "chatterbox_turbo").strip().lower()

    if engine_key in ("chatterbox", "chatterbox_turbo", "turbo") and os.path.isfile(CHATTERBOX_VENV_PYTHON):
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp_dir = Path(td)
                items = [{"text": text, "visual_type": "short", "sfx_cue": "none"}]
                _run_chatterbox_batch(
                    scene_items=items,
                    run_tmp_dir=tmp_dir,
                    uid="single",
                    voice=voice,
                    dark_mastering=dark_mastering,
                    cancel_event=cancel_event,
                )
                generated = items[0].get("audio_path")
                if generated and os.path.isfile(generated):
                    shutil.move(generated, output_path)
                    dur = get_audio_duration(output_path)
                    if dur > 0.4:
                        return dur
        except InterruptedError:
            raise
        except Exception as ex:
            print(f"[TTSEngine] Chatterbox single-clip warning ({ex}); trying Qwen3-TTS...")

    try:
        dur = synthesize_qwen3_chunked(
            text=text,
            output_path=output_path,
            voice_name=voice,
            dark_mastering=dark_mastering,
            cancel_event=cancel_event,
        )
        if dur > 0.4:
            return dur
    except InterruptedError:
        raise
    except Exception as ex:
        print(f"[TTSEngine] Qwen3-TTS warning ({ex}); trying last-resort edge-tts fallback...")
    finally:
        _unload_qwen_tts_server()

    edge_voice = voice if voice.startswith("en-") else "en-US-EricNeural"
    try:
        asyncio.run(_synthesize_edge_tts(text=text, voice=edge_voice, output_path=output_path, rate=rate, pitch=pitch))
        duration = get_audio_duration(output_path)
        if duration > 0.4:
            return duration
    except Exception as ex:
        print(f"[TTSEngine] edge-tts fallback failed: {ex}")

    raise RuntimeError(f"All TTS synthesis engines failed for text: {text[:50]}...")
