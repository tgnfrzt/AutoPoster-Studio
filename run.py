import sys
import os
import argparse

# Force UTF-8 on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
if hasattr(sys.stderr, "reconfigure"):
    try: sys.stderr.reconfigure(encoding="utf-8")
    except Exception: pass

# Ensure pipeline and backend packages are on sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from pipeline.src.pipeline import run_generate_short, run_generate_longform

def main():
    parser = argparse.ArgumentParser(
        description="AutoPoster Pipeline 2: Autonomous Shorts & Long-Form Video Generator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: generate-short
    p_short = subparsers.add_parser("generate-short", help="Generate a vertical 9:16 split-screen Short with word-level subtitles")
    p_short.add_argument("--source", type=str, default="reddit", choices=["reddit", "gemini", "curated"], help="Content source")
    p_short.add_argument("--topic", type=str, default="shortscarystories", help="Reddit subreddit or custom story topic")
    p_short.add_argument("--voice", type=str, default="en-US-ChristopherNeural", help="TTS voice name")
    p_short.add_argument("--gameplay", type=str, default=None, help="Path or keyword for gameplay footage")
    p_short.add_argument("--output", type=str, default=None, help="Custom output MP4 path")

    # Command: generate-longform
    p_long = subparsers.add_parser("generate-longform", help="Generate a horizontal 16:9 horror story with Ken Burns motion & ambient sound")
    p_long.add_argument("--topic", type=str, default="skinwalker encounter in appalachia", help="Horror story theme or prompt")
    p_long.add_argument("--segments", type=int, default=5, help="Number of story segments")
    p_long.add_argument("--voice", type=str, default="en-US-EricNeural", help="TTS voice name")
    p_long.add_argument("--output", type=str, default=None, help="Custom output MP4 path")

    # Command: server
    p_srv = subparsers.add_parser("server", help="Start the AutoPoster Web UI and API server")
    p_srv.add_argument("--port", type=int, default=17420, help="Port to listen on")
    p_srv.add_argument("--host", type=str, default="0.0.0.0", help="Host interface to bind to")

    args = parser.parse_args()

    if args.command == "generate-short":
        result = run_generate_short(
            source=args.source,
            topic=args.topic,
            voice=args.voice,
            gameplay=args.gameplay,
            output_path=args.output
        )
        print(f"Generated Short: {result['output_path']}")

    elif args.command == "generate-longform":
        result = run_generate_longform(
            topic=args.topic,
            segments=args.segments,
            voice=args.voice,
            output_path=args.output
        )
        print(f"Generated Long-Form: {result['output_path']}")

    elif args.command == "server":
        import uvicorn
        print(f"[AutoPoster] Starting server on http://localhost:{args.port}...")
        uvicorn.run("main:app", host=args.host, port=args.port, app_dir=os.path.join(BASE_DIR, "backend"), reload=False)

if __name__ == "__main__":
    main()
