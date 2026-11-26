"""
Batched Chatterbox Turbo GPU TTS Worker (runs under E:\ai-agent\tts_lab\venv_chatterbox\Scripts\python.exe).
Loads ChatterboxTurboTTS once in pure fp32 on CUDA, caches speaker conditionals once,
synthesizes every scene sequentially, and streams JSON progress events on stdout.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Pin ALL HuggingFace and PyTorch caches strictly to E: before importing ML libraries
os.environ.setdefault("HF_HOME", r"E:\ai-agent\models\tts_lab\hf_cache")
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", r"E:\ai-agent\models\tts_lab\hf_cache\hub")
os.environ.setdefault("TORCH_HOME", r"E:\models\torch")
os.environ.setdefault("DISABLE_TELEMETRY", "1")

import numpy as np
import soundfile as sf
import torch
from chatterbox.tts_turbo import ChatterboxTurboTTS


def _emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def split_into_sentence_batches(text: str, max_batch_chars: int = 220) -> List[str]:
    """Split scene text along natural sentence/clause boundaries within safe chunk length."""
    clean = re.sub(r"\.{2,}", ", ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return []

    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    batches: List[str] = []
    current = ""

    for idx, sent in enumerate(raw_sentences):
        if idx == 0 and sent.lower().startswith("story number"):
            batches.append(sent)
            continue

        if len(sent) > max_batch_chars and any(sep in sent for sep in (",", "—", ";", "-")):
            if current:
                batches.append(current)
                current = ""
            subparts = re.split(r"(?<=[,;—])\s+", sent)
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
        elif current and (len(current) + 1 + len(sent) <= max_batch_chars):
            current = f"{current} {sent}"
        else:
            if current:
                batches.append(current)
            current = sent

    if current:
        batches.append(current)
    return batches


def _synth_chunk_arr(model: ChatterboxTurboTTS, chunk_text: str, temperature: float = 0.78) -> np.ndarray:
    """Synthesize a single short text chunk with cached conditionals in pure fp32."""
    with torch.inference_mode():
        wav = model.generate(
            chunk_text,
            audio_prompt_path=None,
            temperature=temperature,
            repetition_penalty=1.18,
            top_p=0.95,
        )
    arr = wav.squeeze().detach().cpu().numpy().astype(np.float32).reshape(-1)
    return arr


def _validate_and_trim(arr: np.ndarray, sample_rate: int) -> np.ndarray:
    """Ensure finite non-empty waveform and trim dead digital silence tails."""
    if arr.size == 0 or not np.isfinite(arr).all():
        raise ValueError("Non-finite or empty Chatterbox output")
    active = np.where(np.abs(arr) > 0.002)[0]
    if len(active) == 0:
        raise ValueError("Silent Chatterbox output")
    start_i = max(0, int(active[0]) - int(0.03 * sample_rate))
    end_i = min(len(arr), int(active[-1]) + int(0.07 * sample_rate))
    trimmed = arr[start_i:end_i]
    if trimmed.size == 0:
        raise ValueError("Trimmed audio empty")
    return trimmed


def run_manifest(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ref_wav = str(manifest["reference_wav"])
    if not os.path.isfile(ref_wav):
        raise FileNotFoundError(f"Reference WAV not found: {ref_wav}")

    dark_mastering = bool(manifest.get("dark_mastering", True))
    scenes = manifest.get("scenes", [])
    if not scenes:
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t_load0 = time.perf_counter()
    # Pure fp32 on GTX 1650 (Turing TU117 lacks Tensor Cores; amp fp16 is >2x slower)
    model = ChatterboxTurboTTS.from_pretrained(device=device)
    model.prepare_conditionals(ref_wav)
    sample_rate = int(getattr(model, "sr", 24000) or 24000)
    load_sec = round(time.perf_counter() - t_load0, 2)

    _emit({"event": "ready", "device": device, "load_sec": load_sec, "sample_rate": sample_rate})

    try:
        for count_idx, scene in enumerate(scenes):
            scene_idx = int(scene["index"])
            text = str(scene.get("text") or "").strip()
            out_wav = Path(scene["output_wav"])
            max_chars = int(scene.get("max_batch_chars", 220 if dark_mastering else 250))

            t_scene0 = time.perf_counter()
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            batches = split_into_sentence_batches(text, max_batch_chars=max_chars)

            parts: List[np.ndarray] = [np.zeros(int(0.20 * sample_rate), dtype=np.float32)]
            for b_idx, batch_text in enumerate(batches):
                clean_s = batch_text.strip()
                if not clean_s:
                    continue
                if not clean_s.endswith((".", "!", "?")):
                    clean_s += "."

                arr = None
                last_exc: Exception | None = None
                for temp in (0.78, 0.65):
                    try:
                        raw_arr = _synth_chunk_arr(model, clean_s, temperature=temp)
                        arr = _validate_and_trim(raw_arr, sample_rate)
                        break
                    except Exception as exc:
                        last_exc = exc
                if arr is None:
                    raise RuntimeError(f"Scene {scene_idx} chunk failed ({last_exc}): {clean_s[:60]}")

                parts.append(arr)
                if dark_mastering:
                    pause_sec = 0.65 if (b_idx == 0 and clean_s.lower().startswith("story number")) else 0.38
                else:
                    pause_sec = 0.18
                parts.append(np.zeros(int(pause_sec * sample_rate), dtype=np.float32))

            if len(parts) <= 1:
                # Fallback brief room tone if scene text was blank
                parts.append(np.zeros(int(0.8 * sample_rate), dtype=np.float32))

            full_audio = np.concatenate(parts).astype(np.float32)
            sf.write(str(out_wav), full_audio, sample_rate, subtype="PCM_16")
            dur = round(len(full_audio) / float(sample_rate), 2)
            gen_sec = round(time.perf_counter() - t_scene0, 2)

            if (count_idx + 1) % 8 == 0:
                gc.collect()
                if device == "cuda":
                    torch.cuda.empty_cache()

            _emit({
                "event": "scene_done",
                "index": scene_idx,
                "output_wav": str(out_wav),
                "audio_sec": dur,
                "gen_sec": gen_sec,
            })
    finally:
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chatterbox Turbo GPU Batch TTS Worker")
    parser.add_argument("--manifest", required=True, help="Path to batch manifest JSON")
    args = parser.parse_args()
    run_manifest(Path(args.manifest))


if __name__ == "__main__":
    main()
