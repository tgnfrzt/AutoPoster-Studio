"""
AI Horror Director:
1. Manages local Qwen3.6-35B-A3B (llama-server.exe on port 8080) on-demand lifecycle:
   auto-starts before scene planning and stops cleanly afterward to free RAM/VRAM.
2. Integrates Hermes Agent + Arctic Shift live Reddit long-form horror scraping.
3. Analyzes story scenes to assign context-matched Mr. Nightmare visual types
   (stock_clip, ai_horror_image, contextual_photo), concrete visual search queries,
   35mm AI photography prompts, foley SFX cues, and dynamic music moods.
"""
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import BASE_DIR

LLAMA_SERVER_EXE = Path(r"E:\llama-cpp\llama-server.exe")
QWEN_PRIMARY_MODEL = Path(r"D:\models\Qwen3.6-35B-A3B-UD-IQ3_S.gguf")
QWEN_FALLBACK_MODEL = Path(r"E:\models\Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf")
QWEN_MODEL_ALIAS = "Qwen3.6-35B-A3B"
QWEN_BASE_URL = "http://127.0.0.1:8080"
QWEN_TTS_UNLOAD_URL = "http://127.0.0.1:8880/audio/unload"

HERMES_EXE = Path(os.getenv("HERMES_EXE", str(Path.home() / "AppData" / "Local" / "hermes" / "bin" / "hermes.exe")))
SERVICEHUB_URL = os.getenv("SERVICEHUB_URL", "http://127.0.0.1:31950").rstrip("/")
REDDIT_CACHE_PATH = BASE_DIR / "storage" / "data" / "real_reddit_horror_cache.json"

ALLOWED_VISUAL_TYPES = ("stock_clip", "ai_horror_image", "contextual_photo")
ALLOWED_SFX_CUES = (
    "none",
    "door_knock",
    "door_handle_rattle",
    "footsteps_creak",
    "window_tap",
    "rain_thunder",
    "dog_bark",
    "phone_buzz",
    "closet_creak",
    "car_pull_up",
    "wind_gust",
)
ALLOWED_MUSIC_MOODS = ("room_tone_only", "very_low_drone", "low_tense_piano")


def _clean_reddit_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"(?i)^\s*(edit|update|tl;?dr|tldr|part\s+\d+)\b.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_#~>`]|&amp;|&lt;|&gt;|&#x200B;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class QwenServerManager:
    """
    Auto-starts local llama-server.exe with Qwen3.6-35B-A3B on port 8080 if not
    already running, and stops it cleanly if this manager started it.
    """

    def __init__(self, base_url: str = QWEN_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.proc: Optional[subprocess.Popen] = None
        self.started_by_us: bool = False
        self.started_via_hub: bool = False
        self._log_handle = None
        self._log_path: Optional[Path] = None

    def is_healthy(self, timeout: float = 2.0) -> bool:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=timeout)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        try:
            r2 = httpx.get(f"{self.base_url}/v1/models", timeout=timeout)
            return r2.status_code == 200
        except Exception:
            return False

    def _find_model_path(self) -> Optional[Path]:
        for cand in (QWEN_PRIMARY_MODEL, QWEN_FALLBACK_MODEL):
            if cand.is_file():
                return cand
        return None

    def _ensure_via_servicehub(self, timeout_sec: int = 95, cancel_event: Optional[Any] = None) -> Optional[bool]:
        """Try waking llama-server via Antigravity ServiceHub so telemetry & Tailscale stay synced."""
        try:
            wait_s = max(5, min(int(timeout_sec), 45))
            resp = httpx.post(
                f"{SERVICEHUB_URL}/api/ensure",
                json={"service_id": "llamacpp", "wait": wait_s},
                timeout=float(wait_s) + 6.0,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not data.get("ok"):
                return None
            started_now = bool(data.get("started_now", False))
            self.started_via_hub = started_now
            self.started_by_us = started_now
            if self.is_healthy(timeout=2.0):
                print("[AIDirector] Qwen3.6 server ready via ServiceHub.")
                return True
            deadline = time.time() + max(10, int(timeout_sec) - wait_s)
            while time.time() < deadline:
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Cancelled by user")
                if self.is_healthy(timeout=1.5):
                    print("[AIDirector] Qwen3.6 server ready via ServiceHub.")
                    return True
                time.sleep(1.2)
            return False
        except InterruptedError:
            raise
        except Exception:
            return None

    def ensure_running(self, timeout_sec: int = 95, cancel_event: Optional[Any] = None) -> bool:
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")
        if self.is_healthy(timeout=2.0):
            self.started_by_us = False
            self.started_via_hub = False
            print(f"[AIDirector] Reusing active Qwen3.6 server at {self.base_url}")
            return True

        self._unload_qwen_tts()
        hub_result = self._ensure_via_servicehub(timeout_sec=timeout_sec, cancel_event=cancel_event)
        if hub_result is True:
            return True
        if hub_result is False:
            self.stop_if_started_by_us()

        if not LLAMA_SERVER_EXE.is_file():
            print(f"[AIDirector] llama-server.exe not found at {LLAMA_SERVER_EXE}")
            return False

        models_to_try = [p for p in (QWEN_PRIMARY_MODEL, QWEN_FALLBACK_MODEL) if p.is_file()]
        if not models_to_try:
            print("[AIDirector] No Qwen3.6 GGUF model found on D:\\models or E:\\models")
            return False

        for model_path in models_to_try:
            if self._launch_and_wait(model_path, timeout_sec=timeout_sec, cancel_event=cancel_event):
                return True
            self.stop_if_started_by_us()

        return False

    def _unload_qwen_tts(self):
        try:
            httpx.post(QWEN_TTS_UNLOAD_URL, json={}, timeout=3.0)
        except Exception:
            pass

    def _launch_and_wait(self, model_path: Path, timeout_sec: int = 95, cancel_event: Optional[Any] = None) -> bool:
        self._unload_qwen_tts()
        cmd = [
            str(LLAMA_SERVER_EXE),
            "-m", str(model_path),
            "--alias", QWEN_MODEL_ALIAS,
            "-c", "16384",
            "-ctk", "q8_0",
            "-ctv", "q8_0",
            "--load-mode", "none",
            "-fa", "on",
            "--no-mmproj",
            "--fit-target", "512",
            "--jinja",
            "--reasoning", "off",
            "--reasoning-format", "deepseek",
            "-np", "1",
            "--host", "127.0.0.1",
            "--port", "8080",
        ]
        print(f"[AIDirector] Auto-starting Qwen3.6 ({model_path.name}) on port 8080...")
        log_dir = BASE_DIR / "storage" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / "llama_server_director.log"
        self._log_handle = open(self._log_path, "w", encoding="utf-8", errors="replace")

        creation_flags = 0
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        self.proc = subprocess.Popen(
            cmd,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        self.started_by_us = True

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            if self.proc.poll() is not None:
                print(f"[AIDirector] llama-server exited early with code {self.proc.returncode}")
                break
            if self.is_healthy(timeout=1.5):
                print(f"[AIDirector] Qwen3.6 server ready ({model_path.name}).")
                return True
            time.sleep(1.5)

        print(f"[AIDirector] Timed out waiting for Qwen3.6 ({model_path.name}).")
        return False

    def stop_if_started_by_us(self):
        if self.started_via_hub:
            print("[AIDirector] Stopping Qwen3.6 server via ServiceHub to free RAM/VRAM...")
            try:
                httpx.post(f"{SERVICEHUB_URL}/api/stop", json={"service_id": "llamacpp"}, timeout=8.0)
            except Exception:
                pass
            time.sleep(1.5)
            self.started_via_hub = False
            self.started_by_us = False
            return

        if self.started_by_us and self.proc is not None:
            pid = self.proc.pid
            print(f"[AIDirector] Stopping Qwen3.6 server (PID {pid}) to free RAM/VRAM for GPU imaging & NVENC...")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True,
                            timeout=8,
                        )
                    else:
                        self.proc.kill()
                except Exception:
                    pass
            time.sleep(2.0)

        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self.proc = None
        self.started_by_us = False


def scrape_with_hermes(
    subreddit: str = "nosleep",
    min_words: int = 1200,
    limit: int = 12,
    use_hermes_cli: bool = True,
) -> List[Dict[str, Any]]:
    """
    Scrapes long-form horror stories (>= min_words) from Reddit via Arctic Shift archive API
    and optionally invokes Hermes Agent CLI if additional discovery is needed, then merges
    results into storage/data/real_reddit_horror_cache.json.
    """
    sub_map = {
        "night_shift": ["LetsNotMeet", "creepyencounters"],
        "cabin_woods": ["BackwoodsCreepy", "Thetruthishere"],
        "woods": ["BackwoodsCreepy"],
        "stalker": ["LetsNotMeet", "creepyencounters"],
        "paranormal": ["Thetruthishere", "nosleep"],
        "all": ["nosleep", "LetsNotMeet", "BackwoodsCreepy", "creepyencounters", "Thetruthishere", "scarystories"],
    }
    target_subs = sub_map.get(subreddit, [subreddit])

    existing: List[Dict[str, Any]] = []
    if REDDIT_CACHE_PATH.is_file():
        try:
            existing = json.loads(REDDIT_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    seen_ids = {str(s.get("id")) for s in existing if s.get("id")}
    newly_added: List[Dict[str, Any]] = []
    found_matching: List[Dict[str, Any]] = []

    before_windows = [1714500000, 1702000000, 1685000000]
    try:
        with httpx.Client(headers={"User-Agent": "AutoPosterHorrorStudio/2.0"}, timeout=15.0) as client:
            for sub in target_subs:
                for before_ts in before_windows:
                    if len(found_matching) >= limit:
                        break
                    url = (
                        f"https://arctic-shift.photon-reddit.com/api/posts/search"
                        f"?subreddit={sub}&before={before_ts}&limit=100"
                    )
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    posts = resp.json().get("data", [])
                    posts.sort(key=lambda x: x.get("score", 0), reverse=True)
                    for p in posts:
                        pid = str(p.get("id", ""))
                        if not pid:
                            continue
                        raw = p.get("selftext", "")
                        title = (p.get("title") or "").strip()
                        if not raw or raw in ("[removed]", "[deleted]") or p.get("stickied"):
                            continue
                        if any(bad in title.lower() for bad in ("meta", "mod ", "rule", "announcement", "monthly", "megathread")):
                            continue
                        cleaned = _clean_reddit_text(raw)
                        wc = len(cleaned.split())
                        if min_words <= wc <= 5500:
                            entry = {
                                "id": pid,
                                "subreddit": sub,
                                "title": title,
                                "author": p.get("author", "anonymous"),
                                "score": p.get("score", 0),
                                "num_comments": p.get("num_comments", 0),
                                "word_count": wc,
                                "estimated_duration_sec": round((wc / 160.0) * 60.0, 1),
                                "url": f"https://www.reddit.com{p.get('permalink', '')}",
                                "text": cleaned,
                            }
                            if not any(x.get("id") == pid for x in found_matching):
                                found_matching.append(entry)
                            if pid not in seen_ids:
                                seen_ids.add(pid)
                                newly_added.append(entry)
    except Exception as ex:
        print(f"[AIDirector] Arctic Shift scrape note: {ex}")

    # Optional Hermes CLI live discovery if still below desired count
    if use_hermes_cli and len(found_matching) < max(3, limit // 2) and HERMES_EXE.is_file():
        mgr = QwenServerManager()
        try:
            if mgr.ensure_running(timeout_sec=80):
                prompt_text = (
                    f"Search Reddit (r/{target_subs[0]}) via web or API for {limit} real long-form horror stories "
                    f"(over {min_words} words each). Return ONLY a JSON array of objects with keys: "
                    f'"id", "subreddit", "title", "author", "url", "text".'
                )
                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
                    tf.write(prompt_text)
                    qfile = tf.name
                try:
                    cmd = [
                        str(HERMES_EXE), "chat",
                        "--query-file", qfile,
                        "--oneshot", "-Q", "--yolo",
                        "--provider", "custom",
                        "--base-url", f"{QWEN_BASE_URL}/v1",
                        "--api-key", "local",
                        "-m", QWEN_MODEL_ALIAS,
                        "--source", "tool",
                        "--max-turns", "6",
                        "--run-budget", "60",
                    ]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=75)
                    if res.returncode == 0 and res.stdout:
                        m = re.search(r"\[\s*\{.*\}\s*\]", res.stdout, re.S)
                        if m:
                            parsed = json.loads(m.group(0))
                            for item in parsed:
                                if not isinstance(item, dict):
                                    continue
                                txt = _clean_reddit_text(item.get("text", ""))
                                wc = len(txt.split())
                                if wc >= min_words:
                                    pid = str(item.get("id") or f"hermes_{abs(hash(txt)) % 10**8}")
                                    entry = {
                                        "id": pid,
                                        "subreddit": item.get("subreddit") or target_subs[0],
                                        "title": (item.get("title") or "True Horror Encounter").strip(),
                                        "author": item.get("author") or "anonymous",
                                        "score": int(item.get("score") or 500),
                                        "num_comments": 0,
                                        "word_count": wc,
                                        "estimated_duration_sec": round((wc / 160.0) * 60.0, 1),
                                        "url": item.get("url") or "",
                                        "text": txt,
                                    }
                                    found_matching.append(entry)
                                    if pid not in seen_ids:
                                        seen_ids.add(pid)
                                        newly_added.append(entry)
                finally:
                    try:
                        os.remove(qfile)
                    except Exception:
                        pass
        except Exception as ex:
            print(f"[AIDirector] Hermes CLI scrape note: {ex}")
        finally:
            mgr.stop_if_started_by_us()

    if newly_added:
        existing.extend(newly_added)
        REDDIT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        REDDIT_CACHE_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[AIDirector] Added {len(newly_added)} long horror stories to Reddit cache (total {len(existing)}).")

    found_matching.sort(key=lambda x: (x.get("score", 0), x.get("word_count", 0)), reverse=True)
    return found_matching[:limit]


def _extract_visual_query_and_prompt(text_lower: str, title_lower: str) -> tuple[str, str]:
    combined = f"{title_lower} {text_lower}"
    if any(k in combined for k in ("forest", "woods", "trees", "trail", "hiking", "camp", "tent", "deer", "pine")):
        return (
            "dark forest woods fog night",
            "35mm realistic night photograph of a misty dense pine forest trail lit by faint moonlight and flashlight beam, eerie trees, visible detail",
        )
    if any(k in combined for k in ("rain", "thunder", "lightning", "storm", "pouring", "wet")):
        return (
            "rain storm window night",
            "35mm realistic night photograph looking through a rain-streaked glass window toward a dimly lit suburban streetlamp in heavy rain",
        )
    if any(k in combined for k in ("car", "driving", "highway", "road", "headlights", "truck", "sedan", "driveway", "gas station")):
        return (
            "dark road car headlights night",
            "35mm realistic night photograph from inside a parked car looking down a misty empty country highway illuminated by headlights",
        )
    if any(k in combined for k in ("police", "cop", "siren", "911", "dispatch", "officer", "patrol")):
        return (
            "police patrol lights night",
            "35mm realistic night photograph of a quiet suburban neighborhood street with red and blue police patrol lights glowing through fog",
        )
    if any(k in combined for k in ("snow", "blizzard", "winter", "freezing", "cold", "ice")):
        return (
            "snowy winter night house",
            "35mm realistic night photograph of an isolated wooden house surrounded by deep snow and bare winter trees under pale blue moonlight",
        )
    if any(k in combined for k in ("canal", "lake", "river", "dock", "swamp", "pond", "ocean", "beach", "florida", "boat")):
        return (
            "dark lake canal water night",
            "35mm realistic night photograph of a still black canal and wooden dock behind suburban Florida palm houses under dim porch light",
        )
    if any(k in combined for k in ("hallway", "stairs", "upstairs", "basement", "closet", "bedroom", "corridor", "apartment", "dorm")):
        return (
            "dark empty house hallway night",
            "35mm realistic night photograph of a shadowy suburban house hallway and wooden staircase with soft amber hallway lamp glow and ajar closet door",
        )
    if any(k in combined for k in ("house", "porch", "home alone", "backyard", "neighborhood", "window", "doorbell", "knock")):
        return (
            "suburban house porch night",
            "35mm realistic night photograph of a quiet American suburban house at 2 AM with a dimly lit front porch light and shadowed bushes",
        )
    return (
        "dark misty night street",
        "35mm realistic night photograph of a quiet empty neighborhood street under yellow sodium streetlamps and creeping ground fog",
    )


def _match_sfx_cue(text_lower: str) -> str:
    if any(k in text_lower for k in ("knock", "knocked", "knocking", "banged on the door", "pounded on the door", "bang on the door")):
        return "door_knock"
    if any(k in text_lower for k in ("doorknob", "handle", "rattl", "jiggle", "locked the door", "deadbolt", "tried the door")):
        return "door_handle_rattle"
    if any(k in text_lower for k in ("footstep", "walking upstairs", "heavy steps", "creak", "floorboard", "up the stairs", "down the hall")):
        return "footsteps_creak"
    if any(k in text_lower for k in ("tapped on the window", "tap on the glass", "tapping", "scratching at the window", "glass")):
        return "window_tap"
    if any(k in text_lower for k in ("thunder", "lightning", "downpour", "rainstorm", "pouring outside")):
        return "rain_thunder"
    if any(k in text_lower for k in ("dog bark", "barking", "started barking", "growl", "dog went crazy")):
        return "dog_bark"
    if any(k in text_lower for k in ("phone buzz", "vibrated", "text message", "notification", "phone rang", "called 911")):
        return "phone_buzz"
    if any(k in text_lower for k in ("closet", "creaked open", "slowly opened", "cabinet", "crawlspace")):
        return "closet_creak"
    if any(k in text_lower for k in ("pulled into the driveway", "car pulled up", "headlights", "engine idling", "tires crunch")):
        return "car_pull_up"
    if any(k in text_lower for k in ("wind howl", "gust of wind", "wind blew", "rustling trees")):
        return "wind_gust"
    return "none"


def analyze_scene_deterministic(item: Dict[str, Any], idx: int, total: int) -> Dict[str, Any]:
    """Deterministic keyword-based Mr. Nightmare visual, SFX, and mood baseline for any scene."""
    text = item.get("text", "")
    text_lower = text.lower()
    title_lower = (item.get("chapter_title") or "").lower()

    visual_query, ai_prompt = _extract_visual_query_and_prompt(text_lower, title_lower)
    sfx_cue = _match_sfx_cue(text_lower)
    sfx_offset = round(0.28 + ((idx * 17) % 38) / 100.0, 2)  # 0.28 .. 0.65

    # Visual mix: ~40% stock_clip, ~30% contextual_photo, ~30% ai_horror_image
    slot = idx % 10
    if slot in (0, 2, 5, 8):
        visual_type = "stock_clip"
    elif slot in (1, 4, 7):
        visual_type = "ai_horror_image"
    else:
        visual_type = "contextual_photo"

    if item.get("type") == "intro" or item.get("chapter_title"):
        visual_type = "ai_horror_image" if idx % 2 == 0 else "stock_clip"

    # Tension curve heuristic
    progress = idx / max(1, total - 1)
    tense_words = ("scream", "shadow", "knife", "gun", "ran", "hide", "locked", "breathing", "staring", "police", "911", "blood", "terrified")
    if any(w in text_lower for w in tense_words) or progress > 0.72:
        music_mood = "low_tense_piano"
    elif progress > 0.28 or sfx_cue != "none":
        music_mood = "very_low_drone"
    else:
        music_mood = "room_tone_only"

    return {
        "visual_type": visual_type,
        "visual_query": visual_query,
        "ai_prompt": ai_prompt,
        "sfx_cue": sfx_cue,
        "sfx_offset": sfx_offset,
        "music_mood": music_mood,
    }


def _strip_think_and_fences(raw: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _enforce_ai_image_budget(scene_items: List[Dict[str, Any]], max_ai_images: int = 8):
    """Caps unique local GPU AI images so GTX 1650 generation completes cleanly under ~65s."""
    ai_count = 0
    for idx, item in enumerate(scene_items):
        if item.get("visual_type") == "ai_horror_image":
            ai_count += 1
            if ai_count > max_ai_images:
                item["visual_type"] = "stock_clip" if idx % 2 == 0 else "contextual_photo"


def direct_compilation_scenes(
    scene_items: List[Dict[str, Any]],
    use_qwen: bool = True,
    max_ai_images: int = 8,
    progress_callback: Optional[Any] = None,
    cancel_event: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Runs deterministic baseline scene direction on all scenes, and if use_qwen=True,
    auto-starts Qwen3.6-35B-A3B, sends compact scene batches for Mr. Nightmare Visual/SFX/Music
    direction with a 2-failure circuit breaker, and auto-stops Qwen3.6 afterwards to free RAM/VRAM.
    """
    total = len(scene_items)
    for idx, item in enumerate(scene_items):
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")
        base_dir = analyze_scene_deterministic(item, idx, total)
        for k, v in base_dir.items():
            item.setdefault(k, v)
        item["image_prompt"] = item.get("ai_prompt") or item.get("image_prompt", "")

    if not use_qwen or total == 0:
        _enforce_ai_image_budget(scene_items, max_ai_images=max_ai_images)
        return scene_items

    manager = QwenServerManager()
    try:
        ready = manager.ensure_running(timeout_sec=95, cancel_event=cancel_event)
        if not ready:
            print("[AIDirector] Qwen3.6 unavailable; using deterministic scene direction.")
            _enforce_ai_image_budget(scene_items, max_ai_images=max_ai_images)
            return scene_items

        batch_size = 6
        total_batches = max(1, (total + batch_size - 1) // batch_size)
        system_prompt = (
            "You are the Visual & Sound Director for a Mr. Nightmare true-horror YouTube channel. "
            "For each input scene, return a JSON array of objects in the same order with compact keys:\n"
            '- "index": int\n'
            '- "visual_type": "stock_clip" | "ai_horror_image" | "contextual_photo" (~40% stock_clip, ~30% ai_horror_image, ~30% contextual_photo)\n'
            '- "visual_query": 2-4 word visual search query matching the scene\n'
            '- "ai_prompt": concise 12-18 word 35mm realistic night photo prompt ONLY when visual_type=="ai_horror_image", otherwise ""\n'
            '- "sfx_cue": "none" | "door_knock" | "door_handle_rattle" | "footsteps_creak" | "window_tap" | "rain_thunder" | "dog_bark" | "phone_buzz" | "closet_creak" | "car_pull_up" | "wind_gust"\n'
            '- "sfx_offset": float 0.20..0.70\n'
            '- "music_mood": "room_tone_only" | "very_low_drone" | "low_tense_piano"\n'
            "Respond ONLY with a valid JSON array."
        )

        directed_ok = 0
        consecutive_failures = 0
        with httpx.Client(timeout=150.0) as client:
            for b_idx, start in enumerate(range(0, total, batch_size)):
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Cancelled by user")
                if consecutive_failures >= 2:
                    print(
                        f"[AIDirector] Circuit breaker triggered after {consecutive_failures} consecutive batch failures; "
                        f"using deterministic baseline for remaining scenes.",
                        flush=True,
                    )
                    break

                batch = scene_items[start : start + batch_size]
                payload_scenes = [
                    {
                        "index": start + offset,
                        "chapter": item.get("chapter_title", ""),
                        "text": (item.get("text") or "")[:300],
                    }
                    for offset, item in enumerate(batch)
                ]
                user_msg = json.dumps(payload_scenes, ensure_ascii=False)

                batch_ok = 0
                try:
                    resp = client.post(
                        f"{QWEN_BASE_URL}/v1/chat/completions",
                        json={
                            "model": QWEN_MODEL_ALIAS,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg},
                            ],
                            "temperature": 0.25,
                            "max_tokens": 750,
                        },
                    )
                    if resp.status_code != 200:
                        print(f"[AIDirector] Qwen batch {start} HTTP {resp.status_code}")
                        consecutive_failures += 1
                    else:
                        raw_content = (
                            resp.json()
                            .get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        cleaned = _strip_think_and_fences(raw_content)
                        arr_match = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.S)
                        if not arr_match:
                            consecutive_failures += 1
                        else:
                            parsed_list = json.loads(arr_match.group(0))
                            if not isinstance(parsed_list, list):
                                consecutive_failures += 1
                            else:
                                for pos, d in enumerate(parsed_list):
                                    if not isinstance(d, dict):
                                        continue
                                    target_idx = int(d.get("index", start + pos))
                                    if not (start <= target_idx < min(total, start + batch_size)):
                                        target_idx = start + pos
                                    if not (0 <= target_idx < total):
                                        continue

                                    target_item = scene_items[target_idx]
                                    vt = d.get("visual_type")
                                    if vt in ALLOWED_VISUAL_TYPES:
                                        target_item["visual_type"] = vt
                                    vq = str(d.get("visual_query") or "").strip()
                                    if vq:
                                        target_item["visual_query"] = vq[:80]
                                    ap = str(d.get("ai_prompt") or "").strip()
                                    if ap:
                                        target_item["ai_prompt"] = ap[:260]
                                        target_item["image_prompt"] = target_item["ai_prompt"]
                                    sc = d.get("sfx_cue")
                                    if sc in ALLOWED_SFX_CUES:
                                        target_item["sfx_cue"] = sc
                                    try:
                                        off = float(d.get("sfx_offset", target_item.get("sfx_offset", 0.42)))
                                        target_item["sfx_offset"] = round(min(0.75, max(0.20, off)), 2)
                                    except Exception:
                                        pass
                                    mm = d.get("music_mood")
                                    if mm in ALLOWED_MUSIC_MOODS:
                                        target_item["music_mood"] = mm
                                    directed_ok += 1
                                    batch_ok += 1

                                if batch_ok > 0:
                                    consecutive_failures = 0
                                else:
                                    consecutive_failures += 1

                                print(
                                    f"[AIDirector] Qwen3.6 directed scenes {start+1}..{min(total, start+batch_size)}/{total}",
                                    flush=True,
                                )
                except Exception as ex:
                    consecutive_failures += 1
                    print(f"[AIDirector] Qwen batch {start} fallback ({ex})")

                if progress_callback:
                    try:
                        progress_callback(min(total, start + batch_size), total, b_idx + 1, total_batches)
                    except Exception:
                        pass

        print(f"[AIDirector] Scene direction complete ({directed_ok}/{total} Qwen-directed, rest deterministic).")
    finally:
        manager.stop_if_started_by_us()

    _enforce_ai_image_budget(scene_items, max_ai_images=max_ai_images)
    return scene_items
