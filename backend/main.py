import os
import sys
from contextlib import asynccontextmanager

# Ensure backend directory is in sys.path
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import settings
from core.database import init_db
from core.worker import start_worker, stop_worker
from api.jobs import router as jobs_router
from api.system import router as system_router
from api.settings import router as settings_router
from api.stories import router as stories_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB and start background worker
    print(f"[{settings.APP_NAME}] Initializing database...")
    init_db()
    print(f"[{settings.APP_NAME}] Starting background scheduler worker...")
    start_worker()
    yield
    # Shutdown: stop worker
    print(f"[{settings.APP_NAME}] Stopping background worker...")
    stop_worker()

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Containerized, modular background service with n8n-style Web UI for multi-platform video posting.",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(jobs_router)
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(stories_router)

# Mount frontend directory
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
if os.path.isdir(assets_dir):
    app.mount("/assets_media", StaticFiles(directory=assets_dir), name="assets_media")


@app.get("/")
def serve_dashboard():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"message": "AutoPoster API active. Frontend index.html not found."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
