"""
Local GPU Horror Scene Image Generator (runs on GTX 1650 4GB VRAM via RVC CUDA runtime).
All HuggingFace/PyTorch caches are pinned strictly to E:\\models to keep C:\\ drive lean.
"""
import os
import sys
import json
import argparse
from pathlib import Path

# Pin ALL caches to E: before importing huggingface/transformers/diffusers/torch
os.environ["HF_HOME"] = r"E:\models\hf"
os.environ["HUGGINGFACE_HUB_CACHE"] = r"E:\models\hf\hub"
os.environ["TORCH_HOME"] = r"E:\models\torch"
os.environ["DISABLE_TELEMETRY"] = "1"

SD_SITE = r"E:\models\sd_site"
if os.path.isdir(SD_SITE) and SD_SITE not in sys.path:
    sys.path.insert(0, SD_SITE)

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import torch

# RVC runtime has an incomplete xformers package; force diffusers to use PyTorch 2.0 native SDPA/slicing
sys.modules["xformers"] = None
sys.modules["xformers.ops"] = None
import diffusers.utils.import_utils as _du
_du._xformers_available = False

from diffusers import AutoPipelineForText2Image


DEFAULT_MODEL_ID = "stabilityai/sd-turbo"


def apply_cinematic_horror_grade(img: Image.Image, target_w: int = 1920, target_h: int = 1080) -> Image.Image:
    """
    Upscales SD-Turbo output cleanly to 16:9 1080p, enforces nocturnal Mr. Nightmare
    grading while guaranteeing visibility (mean_luma >= 46, never black), and adds
    subtle 35mm analog grain.
    """
    img = img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    luma = float(arr.mean())

    # Keep scene eerie & moody without crushing into a black void
    if luma < 44.0:
        gain = min(2.2, 52.0 / max(luma, 12.0))
        img = ImageEnhance.Brightness(img).enhance(gain)
    elif luma > 105.0:
        gain = max(0.58, 72.0 / luma)
        img = ImageEnhance.Brightness(img).enhance(gain)

    img = ImageEnhance.Color(img).enhance(0.76)
    img = ImageEnhance.Contrast(img).enhance(1.12)

    # Subtle 35mm grain + soft edge vignette
    arr_out = np.asarray(img, dtype=np.float32)
    rng = np.random.default_rng(42)
    grain = rng.normal(0.0, 3.5, size=(target_h, target_w, 1)).astype(np.float32)
    yy, xx = np.ogrid[:target_h, :target_w]
    cx, cy = target_w / 2.0, target_h / 2.0
    dist_sq = ((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2
    vignette = np.clip(1.0 - 0.28 * (dist_sq ** 1.3), 0.50, 1.0)[..., None]

    arr_out = np.clip((arr_out + grain) * vignette, 0, 255).astype(np.uint8)
    return Image.fromarray(arr_out)


def generate_batch(tasks_json_path: str, width: int = 512, height: int = 288, steps: int = 3):
    with open(tasks_json_path, "r", encoding="utf-8") as f:
        tasks = json.load(f)

    if not tasks:
        print("[LocalSD] No image tasks provided.")
        return

    use_cuda = torch.cuda.is_available()
    device = "cuda" if use_cuda else "cpu"
    load_dtype = torch.float16 if use_cuda else torch.float32
    print(f"[LocalSD] Loading {DEFAULT_MODEL_ID} on {device} (fp32 Turing-safe inference)...", flush=True)

    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            DEFAULT_MODEL_ID,
            torch_dtype=load_dtype,
            variant="fp16" if use_cuda else None,
        )
    except Exception:
        pipe = AutoPipelineForText2Image.from_pretrained(
            DEFAULT_MODEL_ID,
            torch_dtype=torch.float32,
        )

    pipe.set_progress_bar_config(disable=True)
    if use_cuda:
        # GTX 1650 (Turing TU117) overflows to NaN under fp16 SD2.1 UNet/VAE; cast cached weights to fp32 on CUDA
        pipe = pipe.to("cuda", dtype=torch.float32)
        pipe.enable_attention_slicing(1)
        try:
            pipe.enable_vae_slicing()
        except Exception:
            pass
    else:
        pipe = pipe.to("cpu")

    # Round to multiples of 8 (required by SD VAE); 512x288 is exact 16:9
    gen_w = max(512, (width // 8) * 8)
    gen_h = max(256, (height // 8) * 8)

    for idx, t in enumerate(tasks):
        out_path = t["output_path"]
        raw_prompt = (t.get("prompt") or "dark eerie misty suburban house at night, 35mm analog photography").strip()
        seed = int(t.get("seed", 1000 + idx))

        if os.path.isfile(out_path) and os.path.getsize(out_path) > 30_000:
            try:
                arr_chk = np.asarray(Image.open(out_path).convert("RGB"), dtype=np.float32)
                if float(arr_chk.mean()) >= 20.0:
                    print(f"[LocalSD] Cache hit {idx+1}/{len(tasks)}: {out_path}", flush=True)
                    continue
            except Exception:
                pass

        # Avoid negation tokens ("NOT pitch black" triggers CLIP "black"); emphasize ambient lighting & midtones
        full_prompt = (
            f"{raw_prompt}, realistic nighttime photography, moonlight and warm tungsten porch glow, "
            f"illuminated mist, ambient sodium streetlamp lighting, 35mm film grain, visible detail, atmospheric depth"
        )

        generator = torch.Generator(device=device).manual_seed(seed)
        try:
            with torch.inference_mode():
                raw_img = pipe(
                    prompt=full_prompt,
                    width=gen_w,
                    height=gen_h,
                    num_inference_steps=steps,
                    guidance_scale=0.0,  # sd-turbo works best at guidance_scale=0.0 with 2-4 steps
                    generator=generator,
                ).images[0]

            raw_luma = float(np.asarray(raw_img.convert("RGB"), dtype=np.float32).mean())
            if raw_luma < 8.0:
                print(f"[LocalSD] Warning on task {idx+1}: decoded image too dark (luma={raw_luma:.1f}), skipping.", flush=True)
                continue

            final_img = apply_cinematic_horror_grade(raw_img, target_w=1920, target_h=1080)
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            final_img.save(out_path, "JPEG", quality=92)
            out_luma = float(np.asarray(final_img, dtype=np.float32).mean())
            print(f"[LocalSD] Generated {idx+1}/{len(tasks)}: {os.path.basename(out_path)} (luma={out_luma:.1f})", flush=True)
        except Exception as ex:
            print(f"[LocalSD] Warning on task {idx+1}: {ex}", flush=True)
        finally:
            if use_cuda:
                torch.cuda.empty_cache()

    del pipe
    if use_cuda:
        torch.cuda.empty_cache()
    print("[LocalSD] Batch complete, VRAM released.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Local GPU SD-Turbo Horror Scene Generator")
    parser.add_argument("--tasks", required=True, help="JSON file of [{output_path, prompt, seed}]")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=288)
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    generate_batch(args.tasks, width=args.width, height=args.height, steps=args.steps)


if __name__ == "__main__":
    main()
