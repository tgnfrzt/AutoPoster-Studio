# ⚡ AutoPoster Studio

![Status: Active](https://img.shields.io/badge/Status-Active%20Production-10b981?style=flat)
![Horror Studio: Experimental](https://img.shields.io/badge/AI%20Horror%20Pipeline-Experimental-f59e0b?style=flat)

**AutoPoster** is a containerized, modular automation service designed to run 99% in the background, paired with an **n8n-inspired visual web interface** for configuring workflows, monitoring runs, and repairing failed jobs.

> **Note on Experimental Module:** The long-form **AI Horror Studio pipeline** (`pipeline/` and `run_horror.py`, integrating local `llama.cpp`, Qwen3-TTS, and hybrid visuals) is currently in an **Experimental** phase, while the core multi-platform Reels/Shorts/TikTok scheduler (`backend/`, `frontend/`, `run.py`) is active and stable.

---

## 🌟 Key Architecture & Features

### 1. Platform-Specific Processors + Universal Publisher
- Unlike monolithic scripts that try to force every video into all platforms simultaneously, AutoPoster uses **dedicated target modules**:
  - **Instagram Reel Processor**: Conforms to 1080x1920 (9:16) with blurred-background padding, blends watermark with adjustable opacity, splices outro clips with audio continuity, checks 90-second limits.
  - **YouTube Shorts Processor**: Conforms to 1080x1920, enforces strict `<= 60.0s` duration for YouTube Shorts shelf compliance, normalizes audio to `-14 LUFS`, and auto-tags `#Shorts`.
  - **TikTok Processor**: Optimizes 9:16 vertical canvas and respects right-hand interaction icon safe zones.
- **Universal Publishing Hub**: A single dispatching interface that handles Instagram Graph API (with automated temporary hosting & guaranteed cleanup), YouTube Data API v3 chunked uploads, and TikTok drivers.

### 2. Intelligent Anti-Spam Scheduling & Catch-Up Mode
- **Time-of-Day Randomized Cooldowns**: Mimics human behavior to avoid platform spam detection:
  - Daytime (06:00 – 15:00): 45 to 70 minutes randomized interval.
  - Peak Evening (15:00 – 24:00): 20 to 45 minutes randomized interval.
- **Downtime Catch-Up Mode**: If your machine was powered off or the service was idle for `> 2 hours`, AutoPoster automatically enters catch-up mode and posts a controlled batch of up to 4 videos to restore schedule without flooding.
- **URL Sanitization & Deduplication**: Automatically strips tracking query tokens (`?igsh=...`, `?utm_source=...`), normalizes canonical URLs (`/reel/` ➔ `/p/`), and checks the database cache to prevent reposting the same video.

### 3. Interactive Repair Station & Dead-Letter Queue (DLQ)
- **Immutable Artifacts**: Every step saves its intermediate files (`raw.mp4`, `rendered_9x16.mp4`, metadata, receipts).
- **Step-Level Resumption**: If an upload fails due to an expired token, container timeout, or network glitch, the job is held in `Needs Repair ⚠️` status. You can open the Web UI, view the exact error, preview the video with **Platform Safe Zone Overlays**, tweak caption or settings, and click **"Retry Publishing Step"** without re-downloading or re-rendering!

---

## 🚀 Build & Run Instructions

### Option A: Native Windows (Quickest Local Setup)

1. Make sure Python 3.12+ and FFmpeg are installed (AutoPoster automatically detects them in PATH).
2. Double-click or run:
   ```cmd
   d:\AutoPoster\run_local.bat
   ```
3. Open your browser at:
   ```
   http://localhost:8080
   ```
   Interactive Swagger API docs are at `http://localhost:8080/docs`.

### Option B: Running as a Windows Background Service (NSSM)

To have AutoPoster start silently with Windows on boot:
```cmd
nssm install AutoPoster "D:\AutoPoster\.venv\Scripts\python.exe" "-m uvicorn main:app --host 0.0.0.0 --port 8080 --app-dir D:\AutoPoster\backend"
nssm set AutoPoster AppDirectory "D:\AutoPoster\backend"
nssm start AutoPoster
```

### Option C: Docker Containers

When Docker Desktop or a remote Docker host is available:
```bash
docker compose up -d --build
```
AutoPoster will start isolated inside containers with automatic restart policies and persistent volumes mounted to `./storage`.

---

## 📁 Directory Structure

```
d:\AutoPoster/
├── docker-compose.yml           # Container orchestration
├── .env                         # Active configuration & API credentials
├── .env.example                 # Configuration template
├── run_local.bat                # Windows native launcher
├── backend/
│   ├── Dockerfile               # Container build instructions
│   ├── requirements.txt         # Python dependencies
│   ├── main.py                  # FastAPI application entrypoint
│   ├── core/
│   │   ├── config.py            # Environment & binary resolution
│   │   ├── database.py          # SQLite schema & persistence
│   │   ├── workflow_engine.py   # Step-based execution engine
│   │   └── worker.py            # Background scheduler loop & remote puller
│   ├── modules/
│   │   ├── sources/             # Sanitizer & yt-dlp downloader
│   │   ├── processors/          # Instagram, YouTube, TikTok video renderers
│   │   ├── publishers/          # Platform API upload drivers
│   │   └── dispatch/            # Universal publisher & smart scheduler
│   ├── utils/
│   │   ├── ffmpeg_runner.py     # FFprobe metadata & FFmpeg execution
│   │   └── safezones.py         # Safe-zone margin specs
│   └── api/
│       ├── jobs.py              # Job queue & Repair Station endpoints
│       └── system.py            # Health, scheduler & history endpoints
├── frontend/
│   └── index.html               # n8n-style Web UI & video inspector
└── storage/                     # Persistent shared volume
    ├── raw/                     # Ingested source videos
    ├── rendered/                # Rendered 9:16 platform videos
    ├── templates/               # Watermark and outro assets
    └── data/                    # SQLite database (autoposter.db)
```
