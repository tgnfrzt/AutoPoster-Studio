import sys
import os
from typing import Dict, Any, List, Optional

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from pipeline.src.content_engine import scrape_reddit_horror_stories, clean_script_text

RECOMMENDED_SUBREDDITS = [
    {"name": "LetsNotMeet", "label": "🚪 r/LetsNotMeet (True Creepy Encounters & Stalkers)", "default_for": "both"},
    {"name": "BackwoodsCreepy", "label": "🌲 r/BackwoodsCreepy (Deep Woods & Wilderness Horror)", "default_for": "longform"},
    {"name": "creepyencounters", "label": "👀 r/creepyencounters (Disturbing Real-Life Strangers)", "default_for": "both"},
    {"name": "nosleep", "label": "💀 r/nosleep (Top-Voted Long-Form Horror Stories)", "default_for": "longform"},
    {"name": "scarystories", "label": "🕯️ r/scarystories (Chilling Late-Night Stories)", "default_for": "both"},
    {"name": "Thetruthishere", "label": "🌑 r/Thetruthishere (Unexplained True Encounters)", "default_for": "longform"},
]

def clean_story_text(text: str) -> str:
    return clean_script_text(text)

def fetch_reddit_horror_stories(
    subreddit: str = "LetsNotMeet",
    sort: str = "top",
    time_filter: str = "all",
    limit: int = 15,
    min_words: int = 200,
    max_words: Optional[int] = 1800
) -> List[Dict[str, Any]]:
    """
    Fetches real, top-voted horror stories from Reddit via Arctic Shift archive API
    with local cache fallback.
    """
    return scrape_reddit_horror_stories(
        subreddit=subreddit,
        timeframe=time_filter,
        limit=limit,
        min_words=min_words,
        max_words=max_words or 1800
    )
