import os
import re
import json
import random
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import BASE_DIR, get_gemini_api_key

REDDIT_CACHE_PATH = BASE_DIR / "storage" / "data" / "real_reddit_horror_cache.json"

HORROR_SUBREDDITS = [
    "LetsNotMeet",
    "BackwoodsCreepy",
    "creepyencounters",
    "nosleep",
    "scarystories",
    "Thetruthishere",
]

def clean_script_text(text: str) -> str:
    """Removes URLs, markdown formatting, emojis, and Reddit edit notes for natural TTS speech."""
    if not text:
        return ""
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = re.sub(r'(?i)^\s*(edit|update|tl;?dr|tldr|part\s+\d+)\b.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'[*_#~>`]|&amp;|&lt;|&gt;|&#x200B;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def split_story_into_segments(
    story_text: str,
    words_per_segment: int = 72,
    max_segments: int = 120
) -> List[Dict[str, Any]]:
    """
    Splits a full-length horror story into ~30-35s narration segments at sentence boundaries
    without ever truncating the ending of the story.
    """
    clean = clean_script_text(story_text)
    words = clean.split()
    if not words:
        return []

    scenes = []
    current_words = []
    for w in words:
        current_words.append(w)
        if len(current_words) >= words_per_segment and w.endswith((".", "!", "?", '"', "\u201d")):
            scenes.append(" ".join(current_words))
            current_words = []
            if len(scenes) >= max_segments:
                break
    if current_words and len(scenes) < max_segments:
        if scenes and len(current_words) < 25:
            scenes[-1] = scenes[-1] + " " + " ".join(current_words)
        else:
            scenes.append(" ".join(current_words))

    segments = []
    for idx, s in enumerate(scenes):
        snippet = " ".join(s.split()[:10])
        segments.append({
            "index": idx,
            "text": s,
            "image_prompt": f"dark eerie night photography matching: {snippet}, 35mm analog horror, heavy fog and shadows"
        })
    return segments

def load_cached_reddit_stories() -> List[Dict[str, Any]]:
    """Loads cached real Reddit horror stories from storage/data/real_reddit_horror_cache.json."""
    if REDDIT_CACHE_PATH.is_file():
        try:
            with open(REDDIT_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass
    return []

def scrape_reddit_horror_stories(
    subreddit: str = "LetsNotMeet",
    timeframe: str = "all",
    limit: int = 25,
    min_words: int = 250,
    max_words: int = 5000,
    prefer_long_stories: bool = False
) -> List[Dict[str, Any]]:
    """
    Scrapes real, top-voted horror stories from Reddit via Arctic Shift archive API
    and merges with our local cache of real scraped Reddit stories so 45+ minute
    compilations always have a deep pool of full-length uncut stories.
    """
    import httpx

    sub_map = {
        "night_shift": ["LetsNotMeet", "creepyencounters"],
        "cabin_woods": ["BackwoodsCreepy", "Thetruthishere"],
        "woods": ["BackwoodsCreepy"],
        "stalker": ["LetsNotMeet", "creepyencounters"],
        "paranormal": ["Thetruthishere", "nosleep"],
        "all": ["LetsNotMeet", "BackwoodsCreepy", "creepyencounters", "nosleep", "scarystories", "Thetruthishere"],
    }
    target_subs = sub_map.get(
        subreddit,
        [subreddit] if subreddit in HORROR_SUBREDDITS else ["LetsNotMeet", "BackwoodsCreepy"]
    )

    results = []
    seen_ids = set()

    # First load matching stories from our local cache for instant availability
    cached = load_cached_reddit_stories()
    allowed_lower = {x.lower() for x in target_subs}
    for s in cached:
        wc = s.get("word_count", 0)
        if s.get("subreddit", "").lower() in allowed_lower and min_words <= wc <= max_words:
            sid = s.get("id")
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                results.append(s)

    # Query Arctic Shift live if pool needs more stories or custom subreddit
    if len(results) < limit or subreddit not in HORROR_SUBREDDITS:
        before_ts = 1714500000 if timeframe in ("all", "year") else (1712000000 if timeframe == "month" else 1714000000)

        try:
            with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=12.0) as client:
                for sub in target_subs:
                    cur_before = before_ts
                    for _page in range(2):
                        if len(results) >= limit * 2:
                            break
                        url = f"https://arctic-shift.photon-reddit.com/api/posts/search?subreddit={sub}&before={cur_before}&limit=100"
                        resp = client.get(url)
                        if resp.status_code != 200:
                            break
                        posts = resp.json().get("data", [])
                        if not posts:
                            break
                        min_utc = min(int(p.get("created_utc", cur_before) or cur_before) for p in posts)
                        posts.sort(key=lambda x: x.get("score", 0), reverse=True)
                        for p in posts:
                            pid = p.get("id")
                            if not pid or pid in seen_ids:
                                continue
                            raw = p.get("selftext", "")
                            title = p.get("title", "").strip()
                            if not raw or raw in ("[removed]", "[deleted]") or p.get("stickied"):
                                continue
                            if any(bad in title.lower() for bad in ("meta", "mod", "rule", "announcement", "monthly")):
                                continue
                            cleaned = clean_script_text(raw)
                            wc = len(cleaned.split())
                            if min_words <= wc <= max_words:
                                seen_ids.add(pid)
                                results.append({
                                    "id": pid,
                                    "subreddit": sub,
                                    "title": title,
                                    "author": p.get("author", "anonymous"),
                                    "score": p.get("score", 0),
                                    "num_comments": p.get("num_comments", 0),
                                    "word_count": wc,
                                    "estimated_duration_sec": round((wc / 158.0) * 60.0, 1),
                                    "url": f"https://www.reddit.com{p.get('permalink', '')}",
                                    "text": cleaned,
                                })
                        if min_utc >= cur_before:
                            break
                        cur_before = min_utc - 1
        except Exception as ex:
            print(f"[ContentEngine] Live Arctic Shift Reddit scrape note: {ex}")

    if not results and cached:
        results = [s for s in cached if min_words <= s.get("word_count", 0) <= max_words] or cached

    if prefer_long_stories:
        results.sort(
            key=lambda x: (1 if x.get("word_count", 0) >= 1250 else 0, x.get("score", 0)),
            reverse=True
        )
    else:
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:limit]

def build_compilation_from_reddit_stories(
    stories_list: List[Dict[str, Any]],
    topic_label: str = "True Reddit Horror Encounters",
    max_stories: Optional[int] = None,
    target_duration_minutes: float = 46.0
) -> Dict[str, Any]:
    """
    Formats real scraped Reddit horror stories into a full-length Mr. Nightmare compilation.
    Calibrated to 162 words/minute (with 45m+ marathons guaranteed >= 7,450 words across
    4 to 6 uncut long stories).
    """
    target_words = int(target_duration_minutes * 162) if target_duration_minutes else 0
    if target_duration_minutes and target_duration_minutes >= 45.0:
        target_words = max(target_words, 7450)

    min_stories = 4 if (target_duration_minutes and target_duration_minutes >= 42.0) else (
        3 if (target_duration_minutes and target_duration_minutes >= 25.0) else 1
    )

    pool = list(stories_list)
    if target_duration_minutes and target_duration_minutes >= 42.0:
        # Prioritize 1250-2350w long stories first so 4-5 stories naturally land around 7,500-9,000 words (~47-55 min)
        pool.sort(
            key=lambda s: (
                2 if 1250 <= s.get("word_count", 0) <= 2350 else (1 if s.get("word_count", 0) > 2350 else 0),
                s.get("score", 0),
            ),
            reverse=True,
        )

    chosen = []
    accumulated_words = 0

    for st in pool:
        if max_stories == 1 and target_words <= 0 and len(chosen) >= 1:
            break
        if target_words > 0 and accumulated_words >= target_words and len(chosen) >= min_stories:
            next_wc = st.get("word_count", len(st.get("text", "").split()))
            if max_stories is not None and len(chosen) < max_stories and (accumulated_words + next_wc) <= int(target_words * 1.25):
                pass
            else:
                break
        if max_stories is not None and len(chosen) >= max_stories and (target_words <= 0 or accumulated_words >= target_words):
            break
        chosen.append(st)
        accumulated_words += st.get("word_count", len(st.get("text", "").split()))

    formatted_stories = []
    for idx, st in enumerate(chosen):
        s_num = idx + 1
        title = st.get("title", f"Story {s_num}")
        sub = st.get("subreddit", "LetsNotMeet")
        author = st.get("author", "anonymous")
        score = st.get("score", 0)
        segs = split_story_into_segments(st.get("text", ""), words_per_segment=72, max_segments=120)
        if segs:
            segs[0]["text"] = f"Story Number {s_num}: {title}. {segs[0]['text']}"
            formatted_stories.append({
                "story_number": s_num,
                "title": title,
                "subreddit": sub,
                "author": author,
                "score": score,
                "word_count": st.get("word_count", 0),
                "url": st.get("url", ""),
                "segments": segs
            })

    comp_title = f"{len(formatted_stories)} Disturbing True Stories ({topic_label})"
    if len(formatted_stories) == 1:
        comp_title = formatted_stories[0]["title"]

    return {
        "title": comp_title,
        "theme": topic_label,
        "total_words": accumulated_words,
        "estimated_duration_min": round(accumulated_words / 158.0, 1),
        "intro": (
            f"Welcome back. Tonight, we have {len(formatted_stories)} disturbing true horror "
            f"{'story' if len(formatted_stories) == 1 else 'stories'} shared on Reddit. "
            f"Make sure your doors are locked, turn off the lights, and let's begin."
        ),
        "outro": (
            "Thank you for listening to tonight's stories. If you have experienced something you cannot explain, "
            "share your story in the comments below. Stay safe out there, and goodnight."
        ),
        "stories": formatted_stories
    }

def parse_custom_script_into_compilation(raw_text: str, title: str = "True Horror Encounter") -> Dict[str, Any]:
    """Parses a raw custom or pasted Reddit horror story into a multi-scene compilation without truncation."""
    segs = split_story_into_segments(raw_text, words_per_segment=72, max_segments=250)
    if segs:
        segs[0]["text"] = f"Story Number 1: {title}. {segs[0]['text']}"
    wc = len(raw_text.split())
    return {
        "title": title,
        "theme": "custom story",
        "total_words": wc,
        "estimated_duration_min": round(wc / 128.0, 1),
        "intro": "",
        "outro": "Thank you for listening. Stay safe, and goodnight.",
        "stories": [
            {
                "story_number": 1,
                "title": title,
                "word_count": wc,
                "segments": segs
            }
        ]
    }

def generate_ollama_script(
    topic: str = "night shift encounter",
    model: str = "qwen2.5-coder-abliterated:7b"
) -> Optional[Dict[str, Any]]:
    """Generates an authentic Mr. Nightmare style horror script via local Ollama."""
    import httpx
    prompt = (
        f"You are a master horror storyteller in the style of Mr. Nightmare.\n"
        f"Write a realistic, terrifying first-person encounter about: '{topic}'.\n"
        "Strict rules:\n"
        "- First-person perspective ('I worked at...', 'It was 2:40 AM...').\n"
        "- NO clichés like 'a chill ran down my spine' or 'little did I know'.\n"
        "- Grounded realism: real jobs, cars, clocks, doors, human stalkers, creepy strangers.\n"
        "- Output 6 paragraphs of 65 to 80 words each.\n"
        "Return pure text, no markdown headers or bullet points."
    )
    payload = {"model": model, "prompt": prompt, "stream": False}
    try:
        r = httpx.post("http://localhost:11434/api/generate", json=payload, timeout=60)
        if r.status_code == 200:
            text = r.json().get("response", "").strip()
            if len(text.split()) > 80:
                return parse_custom_script_into_compilation(text, title=f"The {topic.title()} Incident")
    except Exception as ex:
        print(f"[ContentEngine] Ollama generation error: {ex}")
    return None

def generate_gemini_compilation(
    topic: str = "graveyard shift",
    num_stories: int = 5
) -> Optional[Dict[str, Any]]:
    """Generates a complete multi-story Mr. Nightmare compilation via Gemini API."""
    api_key = get_gemini_api_key()
    if not api_key:
        return None

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)

        prompt = (
            f"You are the head writer for Mr. Nightmare's YouTube horror channel.\n"
            f"Create a YouTube horror compilation containing strictly {num_stories} distinct, realistic, disturbing true horror stories about: '{topic}'.\n"
            "Requirements:\n"
            "- Grounded first-person realism: authentic psychological tension, unnerving human stalkers or mysterious figures, 2 AM settings, detailed sensory cues.\n"
            "- Strictly NO fantasy monsters, NO cheesy jump scares, and NO clichés.\n"
            "- Provide an eerie intro (25-40 words), and outro (20-30 words).\n"
            "- For each story, divide into 8 segments of 65 to 80 words each.\n"
            "Return valid JSON matching this schema:\n"
            "{\n"
            '  "title": "5 Disturbing Real Life Stories ...",\n'
            '  "theme": "...",\n'
            '  "intro": "Welcome back...",\n'
            '  "outro": "Thank you for listening...",\n'
            '  "stories": [\n'
            '    {\n'
            '      "story_number": 1,\n'
            '      "title": "...",\n'
            '      "segments": [\n'
            '        {"index": 0, "text": "...", "image_prompt": "..."}\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        data = json.loads(resp.text)
        data["intro"] = clean_script_text(data.get("intro", ""))
        data["outro"] = clean_script_text(data.get("outro", ""))
        for s in data.get("stories", []):
            for seg in s.get("segments", []):
                seg["text"] = clean_script_text(seg.get("text", ""))
        return data
    except Exception as ex:
        print(f"[ContentEngine] Gemini compilation error: {ex}")
        return None

def generate_mr_nightmare_compilation(
    topic: str = "LetsNotMeet",
    source: str = "qwen_hermes",
    custom_text: Optional[str] = None,
    target_duration_minutes: float = 46.0,
    max_stories: Optional[int] = None,
    story_count: Optional[int] = None
) -> Dict[str, Any]:
    """
    Master entrypoint for long-form Mr. Nightmare horror videos (supports 45+ minute marathons).
    Prioritizes real, top-voted long-form Reddit horror stories scraped from r/LetsNotMeet,
    r/BackwoodsCreepy, r/creepyencounters, r/nosleep, r/scarystories, and r/Thetruthishere.
    """
    effective_max_stories = max_stories if max_stories is not None else story_count

    if source == "custom" and custom_text:
        return parse_custom_script_into_compilation(
            custom_text,
            title=topic if topic and topic not in ("night_shift", "LetsNotMeet") else "True Horror Encounter"
        )

    if source == "gemini":
        n_st = effective_max_stories or (5 if target_duration_minutes >= 25 else 3)
        g_data = generate_gemini_compilation(topic=topic, num_stories=n_st)
        if g_data:
            return g_data

    is_marathon = bool(target_duration_minutes and target_duration_minutes >= 30.0)
    min_words = 1100 if is_marathon else 250
    max_words = 5000
    prefer_long = is_marathon
    desired_count = max(effective_max_stories or 0, 5 if (target_duration_minutes and target_duration_minutes >= 42.0) else 4)

    if source in ("qwen_hermes", "ollama", "qwen", "reddit_live"):
        cached = load_cached_reddit_stories()
        matching_long = [
            s for s in cached
            if (topic == "all" or s.get("subreddit", "").lower() == topic.lower())
            and min_words <= s.get("word_count", 0) <= max_words
        ]
        if len(matching_long) < desired_count:
            try:
                from .ai_director import scrape_with_hermes
                scrape_with_hermes(
                    subreddit=topic,
                    min_words=min_words,
                    limit=max(10, desired_count * 2),
                    use_hermes_cli=(source == "qwen_hermes"),
                )
            except Exception as ex:
                print(f"[ContentEngine] Hermes/ArcticShift top-up note: {ex}")

    stories = scrape_reddit_horror_stories(
        subreddit=topic,
        limit=35,
        min_words=min_words,
        max_words=max_words,
        prefer_long_stories=prefer_long,
    )
    required_pool_words = max(int(target_duration_minutes * 165), 7600 if (target_duration_minutes and target_duration_minutes >= 45.0) else 0)
    if sum(s.get("word_count", 0) for s in stories) < required_pool_words or (is_marathon and len(stories) < desired_count):
        extra = scrape_reddit_horror_stories(
            subreddit="all",
            limit=35,
            min_words=min_words,
            max_words=max_words,
            prefer_long_stories=prefer_long,
        )
        seen = {s.get("id") for s in stories}
        for e in extra:
            if e.get("id") not in seen:
                seen.add(e.get("id"))
                stories.append(e)

    if stories:
        label_map = {
            "night_shift": "r/LetsNotMeet & r/creepyencounters",
            "cabin_woods": "r/BackwoodsCreepy",
            "LetsNotMeet": "r/LetsNotMeet",
            "BackwoodsCreepy": "r/BackwoodsCreepy",
            "creepyencounters": "r/creepyencounters",
            "nosleep": "r/nosleep",
            "scarystories": "r/scarystories",
            "Thetruthishere": "r/Thetruthishere",
            "all": "Top Reddit Horror",
        }
        return build_compilation_from_reddit_stories(
            stories_list=stories,
            topic_label=label_map.get(topic, f"r/{topic}"),
            max_stories=effective_max_stories,
            target_duration_minutes=target_duration_minutes
        )

    raise RuntimeError("No horror stories found.")

def generate_shorts_script(source: str = "reddit", topic: str = "shortscarystories") -> Dict[str, Any]:
    """Fetches a real short scary story from Reddit or cached real stories."""
    stories = scrape_reddit_horror_stories(subreddit=topic, min_words=100, max_words=450, limit=12)
    if not stories:
        stories = load_cached_reddit_stories()
    if stories:
        chosen = random.choice(stories[:8])
        # Trim at sentence boundary around ~120-145 words for a tight 50-58s Short
        words = chosen["text"].split()
        selected_words = []
        for w in words:
            selected_words.append(w)
            if len(selected_words) >= 120 and w.endswith((".", "!", "?", '"', "\u201d")):
                break
            if len(selected_words) >= 145:
                break
        short_text = f"{chosen['title']}. " + " ".join(selected_words)
        return {
            "title": chosen["title"],
            "hook": chosen["title"],
            "full_script": short_text,
            "image_prompt": "creepy dark foggy forest road at 3am, analog horror 35mm film",
            "target_duration_sec": 52
        }
    return {
        "title": "The Knock at 3:15 AM",
        "hook": "At exactly 3:15 AM, three gentle knocks sounded on my apartment door.",
        "full_script": "At exactly 3:15 AM, three gentle knocks sounded on my apartment door. I lived on the fourth floor of a secure building. Looking through the peephole, the hallway looked completely empty and pitch black.",
        "image_prompt": "creepy dark apartment hallway at 3am, analog horror",
        "target_duration_sec": 45
    }

def generate_longform_script(topic: str = "BackwoodsCreepy", num_segments: int = 5) -> Dict[str, Any]:
    """Flattens compilation stories into a list of segments for backward compatibility."""
    comp = generate_mr_nightmare_compilation(topic=topic, source="reddit", target_duration_minutes=15.0)
    all_segs = []
    idx = 0
    for s in comp.get("stories", []):
        for seg in s.get("segments", []):
            seg_copy = dict(seg)
            seg_copy["index"] = idx
            all_segs.append(seg_copy)
            idx += 1
    return {
        "title": comp.get("title", "Horror Story"),
        "theme": topic,
        "segments": all_segs[:num_segments]
    }
