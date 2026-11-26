#!/usr/bin/env python3
"""
🕯️ Mr. Nightmare Horror Studio - Standalone CLI Video Generator
Generates full-length, broadcast-quality YouTube horror narrations:
- Real top-voted Reddit horror stories (r/LetsNotMeet, r/BackwoodsCreepy, r/nosleep, r/creepyencounters)
- Deep, soothing, dark baritone narrator voice (Qwen3-TTS 0.6B GPU + dark condenser mic mastering)
- Real 1080p atmospheric night/fog photography with 4K-supersampled sub-pixel Ken Burns motion
- Real YouTube horror ambient background music (rain, distant thunder, eerie piano)
- Fast GTX 1650 NVENC GPU hardware acceleration
"""

import sys
import os
import argparse
from pathlib import Path

# Ensure repo root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pipeline.src.pipeline import run_generate_longform
from pipeline.src.config import is_nvenc_available

def main():
    parser = argparse.ArgumentParser(description="Mr. Nightmare YouTube Horror Video Generator")
    parser.add_argument(
        "--topic", "-t",
        default="LetsNotMeet",
        help="Subreddit or theme: 'LetsNotMeet', 'BackwoodsCreepy', 'creepyencounters', 'nosleep', 'scarystories', 'Thetruthishere'"
    )
    parser.add_argument(
        "--source", "-s",
        default="qwen_hermes",
        choices=["qwen_hermes", "reddit", "reddit_live", "curated", "gemini", "ollama", "qwen", "custom"],
        help="Story source engine: 'qwen_hermes' (Qwen3.6 + Hermes live Reddit horror), 'reddit', 'gemini', 'ollama', 'custom'"
    )
    parser.add_argument(
        "--voice", "-v",
        default="mr_nightmare",
        choices=["mr_nightmare", "mr_nightmare_calm", "chilling_deep", "ryan_documentary"],
        help="TTS Voice profile: 'mr_nightmare', 'mr_nightmare_calm', 'chilling_deep', 'ryan_documentary'"
    )
    parser.add_argument(
        "--visuals",
        default="hybrid_director",
        choices=["hybrid_director", "stock_footage", "local_ai_gpu", "local_library", "reddit_live", "pollinations"],
        help="Visual engine: 'hybrid_director' (Stock clips + Local GPU SD-Turbo + Photos + SFX), 'stock_footage', 'local_ai_gpu', 'local_library', 'reddit_live'"
    )
    parser.add_argument(
        "--ambient", "-a",
        default="creepy_stories_nightmare_ambience.mp3",
        help="Ambient horror music file in assets/ambient/ (default: creepy_stories_nightmare_ambience.mp3)"
    )
    parser.add_argument(
        "--file", "-f",
        default=None,
        help="Path to custom text or story file to narrate"
    )
    parser.add_argument(
        "--duration-minutes", "-d",
        type=float,
        default=46.0,
        help="Target video duration in minutes (default: 46.0 for 45+ min 4-5 story marathon compilations)"
    )
    parser.add_argument(
        "--max-stories",
        type=int,
        default=5,
        help="Optional target/max number of stories (default: 5)"
    )
    parser.add_argument(
        "--segments",
        type=int,
        default=None,
        help="Limit total scenes/segments (default: all)"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Custom output MP4 path"
    )

    args = parser.parse_args()

    custom_text = None
    if args.file and os.path.isfile(args.file):
        with open(args.file, "r", encoding="utf-8") as f:
            custom_text = f.read()
        args.source = "custom"

    print("=" * 65)
    print("🕯️  MR. NIGHTMARE HORROR STUDIO - GENERATOR")
    print(f"GPU Hardware NVENC Acceleration: {'ACTIVE (GTX 1650)' if is_nvenc_available() else 'CPU Fallback'}")
    print(f"Subreddit/Theme: {args.topic} | Source: {args.source} | Voice: {args.voice}")
    print(f"Visuals: {args.visuals} | Ambient: {args.ambient}")
    print("=" * 65)

    res = run_generate_longform(
        topic=args.topic,
        source=args.source,
        custom_text=custom_text,
        voice=args.voice,
        visual_mode=args.visuals,
        ambient_track=args.ambient,
        target_duration_minutes=args.duration_minutes,
        max_stories=args.max_stories,
        segments=args.segments,
        output_path=args.output
    )

    if res.get("success"):
        dur = res.get("duration_sec", 0)
        mins = int(dur // 60)
        secs = int(dur % 60)
        print("\n" + "=" * 65)
        print("✓ VIDEO GENERATION COMPLETE!")
        print(f"• Title: {res.get('title')}")
        print(f"• Runtime: {mins}m {secs}s ({dur:.1f}s)")
        print(f"• Scenes rendered: {res.get('scenes_count')}")
        print(f"• Elapsed time: {res.get('elapsed_sec')}s")
        print(f"• Output Path: {res.get('output_path')}")
        print("=" * 65)
    else:
        print("\n❌ Video generation failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
