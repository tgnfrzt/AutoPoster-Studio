import io
import json
import os
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ASSETS_DIR, BASE_DIR

CREEPY_AMBIENT_DIR = BASE_DIR / "resources" / "backgrounds" / "creepy_ambient"
STOCK_CLIPS_DIR = ASSETS_DIR / "stock_clips"
STOCK_CATALOG_PATH = STOCK_CLIPS_DIR / "catalog.json"
RVC_CUDA_PYTHON = Path(r"D:\RVC1006Nvidia\runtime\python.exe")
LOCAL_SD_WORKER = Path(__file__).resolve().parent / "local_sd_worker.py"

# Cache of live-scraped i.redd.it dark landscape URLs so we don't repeat within a video
_REDDIT_PHOTO_URLS: List[str] = []


def get_local_horror_images() -> List[str]:
    """Returns all curated real atmospheric horror background photographs sorted by name."""
    if not CREEPY_AMBIENT_DIR.is_dir():
        return []
    candidates = []
    for f in sorted(os.listdir(CREEPY_AMBIENT_DIR)):
        if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            full_p = CREEPY_AMBIENT_DIR / f
            if full_p.stat().st_size > 25_000:
                candidates.append(str(full_p))
    return candidates


def _lift_image_luma_if_too_dark(img, min_luma: float = 46.0):
    """Gently lifts shadows via gamma + brightness curve so nocturnal photos never render pitch black."""
    import numpy as np
    from PIL import Image

    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    cur_luma = float(arr.mean())
    if cur_luma < min_luma:
        # Smooth shadow-preserving lift using gamma < 1.0 + linear gain
        norm = np.clip(arr / 255.0, 0.0, 1.0)
        gamma = 0.68 if cur_luma < 26.0 else 0.78
        lifted = (np.power(norm, gamma) * 255.0)
        new_luma = float(lifted.mean())
        if new_luma < min_luma:
            gain = min(1.85, (min_luma + 4.0) / max(new_luma, 10.0))
            lifted = lifted * gain
        return Image.fromarray(np.clip(lifted, 0, 255).astype(np.uint8))
    return img


def ensure_visible_photo_file(src_path: str, output_path: str, width: int = 1920, height: int = 1080) -> str:
    """
    Copies/resizes a photograph to target resolution and ensures mean luminance >= 45
    so moody night imagery remains crisp and clearly visible on any screen.
    """
    import numpy as np
    from PIL import Image, ImageEnhance, ImageOps

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img = Image.open(src_path).convert("RGB")
    if img.size != (width, height):
        img = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS)

    arr = np.asarray(img, dtype=np.float32)
    luma = float(arr.mean())
    if luma < 45.0:
        img = _lift_image_luma_if_too_dark(img, min_luma=48.0)
        img = ImageEnhance.Contrast(img).enhance(1.06)

    img.save(output_path, "JPEG", quality=93)
    return output_path


def grade_and_crop_dark_photo(raw_bytes: bytes, output_path: str, width: int = 1920, height: int = 1080) -> bool:
    """
    Validates that a downloaded photograph is high-resolution landscape, crops to 16:9 (or target ratio),
    and applies a subtle dark nocturnal Mr. Nightmare grade while guaranteeing visibility (mean_luma >= 45).
    """
    try:
        import numpy as np
        from PIL import Image, ImageEnhance, ImageOps

        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        w, h = img.size
        if width >= height and (w < 1200 or w < h * 1.15):
            return False

        arr = np.asarray(img, dtype=np.float32)
        mean_luma = float(arr.mean())
        if mean_luma > 180 or mean_luma < 10:
            return False

        fitted = ImageOps.fit(img, (width, height), method=Image.Resampling.LANCZOS)
        if mean_luma > 88.0:
            factor = max(0.54, 58.0 / mean_luma)
            fitted = ImageEnhance.Brightness(fitted).enhance(factor)
        elif mean_luma < 48.0:
            fitted = _lift_image_luma_if_too_dark(fitted, min_luma=52.0)

        fitted = ImageEnhance.Color(fitted).enhance(0.76)
        fitted = ImageEnhance.Contrast(fitted).enhance(1.08)

        arr_out = np.asarray(fitted, dtype=np.float32)
        yy, xx = np.ogrid[:height, :width]
        cx, cy = width / 2.0, height / 2.0
        dist_sq = ((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2
        vignette = np.clip(1.0 - 0.25 * (dist_sq ** 1.25), 0.54, 1.0)[..., None]
        arr_out = np.clip(arr_out * vignette, 0, 255).astype(np.uint8)

        final_img = Image.fromarray(arr_out)
        if float(arr_out.mean()) < 45.0:
            final_img = _lift_image_luma_if_too_dark(final_img, min_luma=46.0)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        final_img.save(output_path, "JPEG", quality=93)
        return True
    except Exception:
        return False


def fetch_reddit_atmospheric_photo(
    prompt: str,
    output_path: str,
    scene_index: int = 0,
    width: int = 1920,
    height: int = 1080
) -> Optional[str]:
    """
    Fetches a real, high-resolution atmospheric night/fog/liminal photograph from i.redd.it
    via Arctic Shift API (r/TheNightFeeling, r/FoggyPics, r/LiminalSpace, r/kenopsia).
    """
    global _REDDIT_PHOTO_URLS
    import httpx

    prompt_lower = prompt.lower()
    if any(k in prompt_lower for k in ("fog", "woods", "forest", "tree", "cabin", "highway", "road", "lake")):
        preferred_subs = ["FoggyPics", "TheNightFeeling", "LiminalSpace"]
    elif any(k in prompt_lower for k in ("hallway", "corridor", "room", "building", "stair", "gym", "store")):
        preferred_subs = ["LiminalSpace", "kenopsia", "TheNightFeeling"]
    else:
        preferred_subs = ["TheNightFeeling", "FoggyPics", "LiminalSpace"]

    try:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=10.0, follow_redirects=True) as client:
            if len(_REDDIT_PHOTO_URLS) < 18:
                for sub in preferred_subs:
                    url = f"https://arctic-shift.photon-reddit.com/api/posts/search?subreddit={sub}&before=1700000000&limit=60"
                    resp = client.get(url)
                    if resp.status_code == 200:
                        posts = resp.json().get("data", [])
                        posts.sort(key=lambda x: x.get("score", 0), reverse=True)
                        for p in posts:
                            img_url = p.get("url", "")
                            if img_url.startswith("https://i.redd.it/") and img_url.lower().endswith((".jpg", ".jpeg", ".png")):
                                if img_url not in _REDDIT_PHOTO_URLS:
                                    _REDDIT_PHOTO_URLS.append(img_url)

            if _REDDIT_PHOTO_URLS:
                start_idx = (scene_index * 3) % len(_REDDIT_PHOTO_URLS)
                for offset in range(min(6, len(_REDDIT_PHOTO_URLS))):
                    candidate_url = _REDDIT_PHOTO_URLS[(start_idx + offset) % len(_REDDIT_PHOTO_URLS)]
                    r_img = client.get(candidate_url)
                    if r_img.status_code == 200 and len(r_img.content) > 60_000:
                        if grade_and_crop_dark_photo(r_img.content, output_path, width=width, height=height):
                            return output_path
    except Exception as ex:
        print(f"[VisualEngine] Live Reddit photo fetch note: {ex}")

    return None


def _load_stock_catalog() -> List[Dict[str, Any]]:
    if not STOCK_CATALOG_PATH.is_file():
        return []
    try:
        data = json.loads(STOCK_CATALOG_PATH.read_text(encoding="utf-8"))
        return [c for c in data if (STOCK_CLIPS_DIR / c.get("filename", "")).is_file()]
    except Exception:
        return []


def get_contextual_stock_clip(
    visual_query: str,
    scene_index: int = 0,
    used_clips: Optional[List[str]] = None,
) -> Optional[Path]:
    """
    Matches a visual query / scene keywords against curated stock footage clips in
    assets/stock_clips/catalog.json while avoiding recent back-to-back repeats.
    """
    catalog = _load_stock_catalog()
    if not catalog:
        if STOCK_CLIPS_DIR.is_dir():
            mp4s = sorted(STOCK_CLIPS_DIR.glob("*.mp4"))
            if mp4s:
                return mp4s[scene_index % len(mp4s)]
        return None

    tokens = {t.strip(".,!?;:\"'") for t in (visual_query or "").lower().split() if len(t) > 2}
    recent_set = set((used_clips or [])[-4:])

    scored = []
    for idx, entry in enumerate(catalog):
        fname = entry.get("filename", "")
        kws = {k.lower() for k in entry.get("keywords", [])}
        slug = (entry.get("slug") or "").lower()
        title_tokens = {w.lower().strip(".,!?") for w in (entry.get("title") or "").split()}
        all_tags = kws | {slug} | title_tokens

        overlap = len(tokens & all_tags)
        slug_bonus = 2 if slug in tokens else 0
        recent_penalty = -5 if fname in recent_set else 0
        tie_breaker = ((scene_index + idx) % 7) * 0.01
        score = overlap + slug_bonus + recent_penalty + tie_breaker
        scored.append((score, fname))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen_fname = scored[0][1]
    if used_clips is not None:
        used_clips.append(chosen_fname)
    return STOCK_CLIPS_DIR / chosen_fname


def batch_generate_local_sd_images(
    scene_items: List[Dict[str, Any]],
    work_dir: Path,
    max_unique_images: int = 8,
) -> Dict[int, Path]:
    """
    Runs local GPU SD-Turbo (via RVC CUDA Python runtime + E:\\models caches) in a single
    batch for all scenes tagged visual_type='ai_horror_image', storing generated paths on items.
    If more than max_unique_images request AI imagery, generated assets are cycled cleanly.
    """
    results: Dict[int, Path] = {}
    if not RVC_CUDA_PYTHON.is_file() or not LOCAL_SD_WORKER.is_file():
        print(f"[VisualEngine] Local GPU SD worker unavailable ({RVC_CUDA_PYTHON}); skipping AI image batch.")
        return results

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    ai_dir = work_dir / "sd_scenes"
    ai_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    idx_map = []
    extra_ai_indices = []
    for idx, item in enumerate(scene_items):
        if item.get("visual_type") == "ai_horror_image":
            if len(tasks) < max_unique_images:
                out_p = ai_dir / f"ai_scene_{idx:03d}.jpg"
                prompt = item.get("ai_prompt") or item.get("image_prompt") or item.get("visual_query") or "dark foggy suburban street at night"
                tasks.append({
                    "output_path": str(out_p),
                    "prompt": prompt,
                    "seed": 4200 + idx * 13,
                })
                idx_map.append((idx, out_p))
            else:
                extra_ai_indices.append(idx)

    if not tasks:
        return results

    jobs_json = work_dir / "sd_jobs.json"
    jobs_json.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    print(f"[VisualEngine] Generating {len(tasks)} eerie 16:9 horror images on GTX 1650 (SD-Turbo fp32)...")
    try:
        cmd = [
            str(RVC_CUDA_PYTHON),
            "-u",
            str(LOCAL_SD_WORKER),
            "--tasks",
            str(jobs_json),
            "--width",
            "512",
            "--height",
            "288",
            "--steps",
            "3",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            print(f"[VisualEngine] LocalSD worker warning: {(res.stderr or res.stdout)[-400:]}")
    except Exception as ex:
        print(f"[VisualEngine] LocalSD batch exception: {ex}")

    generated_paths = []
    for idx, out_p in idx_map:
        if out_p.is_file() and out_p.stat().st_size > 20_000:
            results[idx] = out_p
            generated_paths.append(out_p)
            scene_items[idx]["resolved_visual_path"] = str(out_p)
            scene_items[idx]["resolved_is_video"] = False

    # Any AI-tagged scenes beyond max_unique_images get reassigned to stock_clip/contextual_photo cleanly
    # so visuals stay varied without repeating images back-to-back
    for pos, extra_idx in enumerate(extra_ai_indices):
        scene_items[extra_idx]["visual_type"] = "stock_clip" if pos % 2 == 0 else "contextual_photo"

    print(f"[VisualEngine] LocalSD batch produced {len(results)}/{len(tasks)} horror scene images.")
    return results


def resolve_scene_visual_asset(
    item: Dict[str, Any],
    work_dir: Path,
    scene_index: int = 0,
    visual_mode: str = "hybrid_director",
    used_assets: Optional[set] = None,
    used_clips: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Unified asset resolver returning {"path": Path, "is_video": bool, "visual_type": str}.
    Guarantees visible horror imagery (never pitch black) across stock clips, AI horror images,
    and real atmospheric photographs.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    visual_query = (
        item.get("visual_query")
        or item.get("ai_prompt")
        or item.get("image_prompt")
        or "dark misty night suburban street"
    )

    # 1. Pre-generated Local GPU AI Horror Image
    pre_path = item.get("resolved_visual_path")
    if pre_path and os.path.isfile(pre_path):
        return {
            "path": Path(pre_path),
            "is_video": bool(item.get("resolved_is_video", False)),
            "visual_type": item.get("visual_type", "ai_horror_image"),
        }

    desired_type = item.get("visual_type", "stock_clip")
    if visual_mode == "stock_footage":
        desired_type = "stock_clip" if scene_index % 3 != 2 else "contextual_photo"
    elif visual_mode == "reddit_live":
        desired_type = "contextual_photo"
    elif visual_mode == "local_library":
        desired_type = "contextual_photo"
    elif visual_mode == "local_ai_gpu":
        desired_type = "ai_horror_image" if item.get("resolved_visual_path") else "stock_clip"

    # 2. Contextual Stock Video Clip (~40% of hybrid scenes)
    if desired_type == "stock_clip":
        clip_path = get_contextual_stock_clip(
            visual_query=f"{visual_query} {item.get('text', '')[:120]}",
            scene_index=scene_index,
            used_clips=used_clips,
        )
        if clip_path and clip_path.is_file():
            return {
                "path": clip_path,
                "is_video": True,
                "visual_type": "stock_clip",
            }

    # 3. Contextual Photograph (live i.redd.it + visible curated creepy_ambient)
    out_img = work_dir / f"scene_photo_{scene_index:03d}.jpg"
    if visual_mode == "reddit_live" or (desired_type == "contextual_photo" and scene_index % 3 == 0):
        live = fetch_reddit_atmospheric_photo(
            visual_query,
            str(out_img),
            scene_index=scene_index,
            width=1920,
            height=1080,
        )
        if live and os.path.isfile(live):
            return {
                "path": Path(live),
                "is_video": False,
                "visual_type": "contextual_photo",
            }

    local_images = get_local_horror_images()
    if local_images:
        chosen = local_images[scene_index % len(local_images)]
        ensure_visible_photo_file(chosen, str(out_img), width=1920, height=1080)
        return {
            "path": out_img,
            "is_video": False,
            "visual_type": "contextual_photo",
        }

    # Final fallback to stock clip if no local photos
    clip_fallback = get_contextual_stock_clip(visual_query, scene_index=scene_index, used_clips=used_clips)
    if clip_fallback and clip_fallback.is_file():
        return {"path": clip_fallback, "is_video": True, "visual_type": "stock_clip"}

    raise RuntimeError("No visual assets available in assets/stock_clips or resources/backgrounds/creepy_ambient.")


def generate_pollinations_image(
    prompt: str,
    output_path: str,
    width: int = 1920,
    height: int = 1080,
    model: str = "flux",
    seed: Optional[int] = None,
    scene_index: int = 0
) -> str:
    """Obtains a real visible dark horror photograph for backward compatibility."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    local_images = get_local_horror_images()
    if local_images:
        chosen = local_images[scene_index % len(local_images)]
        return ensure_visible_photo_file(chosen, output_path, width=width, height=height)

    live_res = fetch_reddit_atmospheric_photo(prompt, output_path, scene_index=scene_index, width=width, height=height)
    if live_res and os.path.isfile(live_res):
        return live_res

    raise RuntimeError("No real horror background photographs available.")


def get_scene_visual(
    image_prompt: str,
    output_path: str,
    mode: str = "local_library",
    scene_index: int = 0,
    width: int = 1920,
    height: int = 1080
) -> str:
    """Legacy visual helper (preserved for Shorts and direct image callers), guaranteed mean_luma >= 45."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if mode == "reddit_live":
        live = fetch_reddit_atmospheric_photo(image_prompt, output_path, scene_index=scene_index, width=width, height=height)
        if live and os.path.isfile(live):
            return live

    local_images = get_local_horror_images()
    if local_images:
        chosen = local_images[scene_index % len(local_images)]
        return ensure_visible_photo_file(chosen, output_path, width=width, height=height)

    return generate_pollinations_image(image_prompt, output_path, width=width, height=height, scene_index=scene_index)
