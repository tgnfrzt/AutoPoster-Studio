"""
AutoPoster Cloud Relay — Cloud Server (flask_app.py)
Deploy location example: /home/<your-username>/mysite/flask_app.py

Features:
- MacroDroid mobile sync (POST or GET /add_link)
- AutoPoster background worker polling (/get_and_clear_queue)
- Legacy compatibility (/list and /clear)
- Instagram Graph API temporary video hosting (/upload_video, /uploads/<filename>, /delete_video/<filename>)
- Automatic disk quota protection: auto-prunes files older than 2 hours to prevent filling PythonAnywhere storage
- Modern mobile-friendly web dashboard with real-time stats
"""

import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request, send_from_directory, render_template_string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LIST_FILE = os.path.join(BASE_DIR, "list.txt")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DOMAIN = os.getenv("RELAY_DOMAIN", "").strip().rstrip("/")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)

# ==========================================
# STORAGE & QUEUE HELPERS
# ==========================================

def _cleanup_stale_uploads(max_age_seconds: int = 7200):
    """Deletes temporary video files older than 2 hours to protect PythonAnywhere disk limits."""
    now = time.time()
    if not os.path.isdir(UPLOAD_DIR):
        return
    for fname in os.listdir(UPLOAD_DIR):
        fpath = os.path.join(UPLOAD_DIR, fname)
        if os.path.isfile(fpath):
            try:
                if now - os.path.getmtime(fpath) > max_age_seconds:
                    os.remove(fpath)
                    print(f"[Cleanup] Removed stale file: {fname}")
            except Exception as e:
                print(f"[Cleanup] Error deleting {fname}: {e}")

def _read_links() -> list[str]:
    """Reads all queued links from list.txt."""
    if not os.path.exists(LIST_FILE):
        return []
    with open(LIST_FILE, "r", encoding="utf-8", errors="replace") as f:
        return [line.strip() for line in f if line.strip()]

def _write_links(links: list[str]) -> None:
    """Overwrites list.txt with specified links."""
    with open(LIST_FILE, "w", encoding="utf-8") as f:
        if links:
            f.write("\n".join(links) + "\n")

def _append_links(new_links: list[str]) -> int:
    """Appends unique links to the queue."""
    existing = set(_read_links())
    added = 0
    with open(LIST_FILE, "a", encoding="utf-8") as f:
        for link in new_links:
            clean = link.strip()
            if clean and clean not in existing:
                f.write(clean + "\n")
                existing.add(clean)
                added += 1
    return added

def _get_uploads_stats():
    """Calculates number of files and total size in uploads/ folder."""
    if not os.path.isdir(UPLOAD_DIR):
        return 0, 0.0
    files = [f for f in os.listdir(UPLOAD_DIR) if os.path.isfile(os.path.join(UPLOAD_DIR, f))]
    total_bytes = sum(os.path.getsize(os.path.join(UPLOAD_DIR, f)) for f in files)
    return len(files), round(total_bytes / (1024 * 1024), 2)

# ==========================================
# WEB DASHBOARD (MOBILE & DESKTOP)
# ==========================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoPoster Cloud Relay — PythonAnywhere</title>
    <style>
        :root {
            --bg-base: #090d16;
            --bg-card: #111827;
            --border: #1f2937;
            --text-main: #f9fafb;
            --text-muted: #9ca3af;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
            --success: #10b981;
            --danger: #ef4444;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            padding: 1.5rem;
            max-width: 760px;
            margin: 0 auto;
        }
        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1rem;
        }
        .brand { display: flex; align-items: center; gap: 0.6rem; }
        .logo { font-size: 1.5rem; }
        h1 { font-size: 1.25rem; font-weight: 700; }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.2rem 0.6rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            background: rgba(16, 185, 129, 0.1);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.25);
        }
        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.25rem;
            margin-bottom: 1.25rem;
        }
        .stat-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
        .stat-value { font-size: 1.75rem; font-weight: 800; margin-top: 0.25rem; color: var(--text-main); }
        .stat-sub { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem; }
        textarea {
            width: 100%;
            height: 90px;
            background: #0d131f;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 0.75rem;
            color: var(--text-main);
            font-family: monospace;
            font-size: 0.85rem;
            margin-top: 0.5rem;
            resize: vertical;
        }
        textarea:focus { outline: none; border-color: var(--accent); }
        .btn {
            background: var(--accent);
            color: white;
            border: none;
            padding: 0.6rem 1.2rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .btn:hover { background: var(--accent-hover); }
        .btn-danger { background: rgba(239, 68, 68, 0.15); color: var(--danger); border: 1px solid rgba(239, 68, 68, 0.3); }
        .btn-danger:hover { background: rgba(239, 68, 68, 0.25); }
        .list-container {
            max-height: 250px;
            overflow-y: auto;
            margin-top: 0.75rem;
        }
        ul { list-style: none; display: flex; flex-direction: column; gap: 0.4rem; }
        li {
            background: #0d131f;
            padding: 0.5rem 0.75rem;
            border-radius: 6px;
            font-size: 0.8rem;
            word-break: break-all;
            border-left: 3px solid var(--accent);
            font-family: monospace;
        }
        .actions-row { display: flex; justify-content: space-between; align-items: center; margin-top: 0.75rem; }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="logo">⚡</span>
            <div>
                <h1>AutoPoster Cloud Relay</h1>
                <div style="font-size: 0.72rem; color: var(--text-muted);">PythonAnywhere & MacroDroid Bridge</div>
            </div>
        </div>
        <div class="status-badge">● Online 24/7</div>
    </header>

    <div class="stats-grid">
        <div class="card" style="margin-bottom: 0;">
            <div class="stat-label">Queue Length</div>
            <div class="stat-value">{{ queue_count }}</div>
            <div class="stat-sub">Pending links for AutoPoster</div>
        </div>
        <div class="card" style="margin-bottom: 0;">
            <div class="stat-label">Temporary Videos</div>
            <div class="stat-value">{{ uploads_count }} <span style="font-size: 1rem; font-weight: 600; color: var(--text-muted);">({{ uploads_mb }} MB)</span></div>
            <div class="stat-sub">Auto-pruned after 2h</div>
        </div>
    </div>

    <div class="card">
        <h2 style="font-size: 0.95rem; font-weight: 700;">📥 Add Reel / Video Links</h2>
        <form method="POST" action="/add_link">
            <textarea name="link" placeholder="Paste Instagram Reels or YouTube URLs (one per line)..."></textarea>
            <div class="actions-row">
                <button type="submit" class="btn">+ Add to Queue</button>
                <span style="font-size: 0.72rem; color: var(--text-muted);">MacroDroid webhook target: /add_link</span>
            </div>
        </form>
    </div>

    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <h2 style="font-size: 0.95rem; font-weight: 700;">📋 Current Queue ({{ queue_count }})</h2>
            {% if queue_count > 0 %}
            <a href="/clear" onclick="return confirm('Clear all links in queue?')" style="text-decoration: none;">
                <button class="btn btn-danger" style="padding: 0.35rem 0.75rem; font-size: 0.75rem;">Clear Queue</button>
            </a>
            {% endif %}
        </div>
        <div class="list-container">
            {% if links %}
            <ul>
                {% for link in links %}
                <li>{{ link }}</li>
                {% endfor %}
            </ul>
            {% else %}
            <p style="color: var(--text-muted); font-size: 0.8rem; padding: 1rem 0; text-align: center;">Queue is empty. Forward links from phone via MacroDroid.</p>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    """Renders the dark mobile dashboard."""
    _cleanup_stale_uploads()
    links = _read_links()
    uploads_count, uploads_mb = _get_uploads_stats()
    return render_template_string(
        DASHBOARD_HTML,
        links=links,
        queue_count=len(links),
        uploads_count=uploads_count,
        uploads_mb=uploads_mb
    )

# ==========================================
# MACRODROID & QUEUE ENDPOINTS
# ==========================================

@app.route("/add_link", methods=["POST", "GET"])
def add_link():
    """
    Accepts links from MacroDroid via POST (form, json, plaintext) or simple GET (?url=... or ?link=...).
    """
    raw = ""
    # Support GET request from MacroDroid shortcuts
    if request.method == "GET":
        raw = request.args.get("link") or request.args.get("url") or ""
    elif request.mimetype == "text/plain" or request.content_type == "text/plain":
        raw = request.get_data(as_text=True) or ""
    elif request.is_json:
        payload = request.get_json(silent=True) or {}
        raw = payload.get("link") or payload.get("url") or payload.get("text") or ""
        if isinstance(raw, list):
            raw = "\n".join(str(item) for item in raw)
    else:
        raw = (
            request.form.get("link")
            or request.form.get("text")
            or request.form.get("url")
            or request.get_data(as_text=True)
            or ""
        )

    links = [line.strip() for line in raw.replace("\r", "").split("\n") if line.strip()]
    if not links:
        if request.method == "GET":
            return jsonify({"ok": False, "error": "missing_query_param", "usage": "/add_link?url=https://..."}), 400
        return jsonify({"ok": False, "error": "empty_input"}), 400

    added = _append_links(links)

    # Browser form redirect
    if request.form and not request.is_json and request.content_type != "text/plain":
        return f"<script>alert('Added {added} new link(s)!'); window.location.href='/';</script>"

    return jsonify({"ok": True, "added": added, "total_queue": len(_read_links())})

@app.route("/get_and_clear_queue", methods=["GET"])
def get_and_clear_queue():
    """
    Main AutoPoster integration endpoint:
    Atomically retrieves all accumulated links and clears the queue on the server.
    """
    _cleanup_stale_uploads()
    links = _read_links()
    _write_links([])
    return jsonify({"links": links, "count": len(links)})

@app.route("/list", methods=["GET"])
def list_links_legacy():
    """Legacy plain-text endpoint returning links separated by newlines."""
    links = _read_links()
    return "\n".join(links), 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route("/clear", methods=["GET", "POST"])
def clear_queue():
    """Clears all links from the queue."""
    _write_links([])
    if request.headers.get("Accept", "").find("html") != -1 and request.method == "GET":
        return "<script>window.location.href='/';</script>"
    return jsonify({"ok": True, "cleared": True})

# ==========================================
# TEMPORARY VIDEO HOSTING (INSTAGRAM GRAPH API)
# ==========================================

@app.route("/upload_video", methods=["POST"])
def upload_video():
    """
    Receives rendered video from AutoPoster, saves to uploads/,
    and returns the public HTTPS URL for Instagram Graph API.
    """
    _cleanup_stale_uploads()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"ok": False, "error": "missing_file"}), 400

    original = os.path.basename(uploaded.filename)
    name, ext = os.path.splitext(original)
    if not ext:
        ext = ".mp4"

    safe_name = f"{name}_{uuid.uuid4().hex[:8]}{ext}"
    dest = os.path.join(UPLOAD_DIR, safe_name)
    uploaded.save(dest)

    base_domain = DOMAIN or request.host_url.rstrip("/")
    public_url = f"{base_domain}/uploads/{safe_name}"
    return jsonify({"ok": True, "video_url": public_url, "filename": safe_name})

@app.route("/uploads/<path:filename>", methods=["GET"])
def serve_upload(filename):
    """Serves the video file publicly to Meta's servers."""
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)

@app.route("/delete_video/<path:filename>", methods=["DELETE", "GET", "POST"])
def delete_video_path(filename):
    """Deletes temporary video using path parameter."""
    clean_target = os.path.basename(filename)
    target = os.path.join(UPLOAD_DIR, clean_target)
    if os.path.isfile(target):
        try:
            os.remove(target)
            return jsonify({"ok": True, "deleted": clean_target})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": False, "error": "file_not_found"}), 404

@app.route("/delete_video", methods=["POST"])
def delete_video_post():
    """Deletes temporary video using form or json payload."""
    data = request.get_json(silent=True) or request.form or {}
    filename = data.get("filename") or ""
    if not filename:
        return jsonify({"ok": False, "error": "missing_filename"}), 400
    return delete_video_path(filename)

# ==========================================
# HEALTH & METRICS ENDPOINT
# ==========================================

@app.route("/health", methods=["GET"])
@app.route("/status", methods=["GET"])
def health_status():
    """Returns server operational status and storage metrics."""
    links = _read_links()
    uploads_count, uploads_mb = _get_uploads_stats()
    base_domain = DOMAIN or request.host_url.rstrip("/")
    return jsonify({
        "status": "healthy",
        "domain": base_domain,
        "queue_count": len(links),
        "uploads_count": uploads_count,
        "uploads_mb": uploads_mb,
        "timestamp": datetime.utcnow().isoformat()
    })

# ==========================================
# SCHEDULE SYNC API (DUALSCHEDULE PC <-> MOBILE)
# ==========================================

SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")

@app.route("/api/schedule", methods=["GET", "POST"])
@app.route("/api/orar", methods=["GET", "POST"])
def schedule_sync_endpoint():
    """Receives or serves the centralized schedule JSON for PC & Android sync."""
    import json
    if request.method == "POST":
        try:
            data = request.get_json(force=True, silent=True)
            if not data:
                return jsonify({"error": "Invalid JSON payload"}), 400
            with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return jsonify({
                "status": "success",
                "message": "Orar salvat cu succes pe serverul PythonAnywhere!",
                "entries_count": len(data.get("entries", []))
            })
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500
    else:
        if os.path.exists(SCHEDULE_FILE):
            try:
                with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
                    return jsonify(json.load(f))
            except Exception as exc:
                return jsonify({"status": "error", "error": str(exc)}), 500
        return jsonify({"status": "empty", "message": "Niciun orar nu a fost incarcat inca pe server"}), 404

# ==========================================
# ORCHESTRATOR CLOUD RELAY (/orch/*)
# ==========================================

ORCH_DB_PATH = os.path.join(BASE_DIR, "orch_relay.db")
ORCH_ENV_PATH = os.path.join(BASE_DIR, "orch_relay.env")
ORCH_APK_DIR = os.path.join(BASE_DIR, "orchestrator_apk")
ORCH_URL_RE = re.compile(r"https?://[^\s<>\]\"']+", re.IGNORECASE)
_ORCH_LOCK = threading.RLock()
_ORCH_DB_INITIALIZED = False

ORCH_DEFAULT_GROUPS = (
    (
        "video_lab",
        "Video Lab",
        "🎬",
        "Deconstruct AI/viral videos, extract hooks & Google Flow/Veo recipes, and draft new episode concepts.",
        r"E:\ai-agent\director",
        "antigravity",
        1,
    ),
    (
        "tech_research",
        "Local AI & Tech",
        "🧠",
        "Research local AI models, GGUF quants, TTS, & tools tested for GTX 1650 (4GB VRAM) + 32GB RAM, saved to Obsidian.",
        r"E:\ai-agent\notes",
        "antigravity",
        2,
    ),
    (
        "trading_bot",
        "Trading Bot",
        "📈",
        "Evaluate crypto/quant ideas directly against Argon Quant V5 code and propose safe patch diffs without auto-editing live trading.",
        r"E:\unity\ai_workspace\trading-bot",
        "antigravity",
        3,
    ),
    (
        "quick_decode",
        "Quick Decode",
        "⚡",
        "Fast, direct breakdowns of any video, post, repo, or question with zero IDE clutter.",
        r"E:\ai-agent",
        "antigravity",
        4,
    ),
    (
        "hermes",
        "Hermes Lab",
        "🤖",
        "Route prompts/links to local Hermes Agent (Qwen3.6-35B-A3B via llama.cpp) or swap back to Antigravity anytime.",
        r"E:\ai-agent",
        "hermes",
        5,
    ),
)


def _orch_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_orch_env_var(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    if os.path.isfile(ORCH_ENV_PATH):
        try:
            with open(ORCH_ENV_PATH, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    if k.strip() == key:
                        return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _get_orch_token() -> str:
    return _get_orch_env_var("ORCH_API_TOKEN")


def _get_orch_apk_token() -> str:
    return _get_orch_env_var("ORCH_APK_TOKEN") or _get_orch_token()


def _extract_orch_candidate_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        cand = auth[7:].strip()
        if cand:
            return cand
    x_tok = (request.headers.get("X-Orch-Token") or "").strip()
    if x_tok:
        return x_tok
    return (request.args.get("token") or "").strip()


def _orch_authorized() -> bool:
    expected = _get_orch_token()
    cand = _extract_orch_candidate_token()
    return bool(expected and cand and hmac.compare_digest(cand, expected))


def _orch_apk_authorized() -> bool:
    cand = _extract_orch_candidate_token()
    if not cand:
        return False
    for expected in (_get_orch_apk_token(), _get_orch_token()):
        if expected and hmac.compare_digest(cand, expected):
            return True
    return False


def _orch_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(ORCH_DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _init_orch_db() -> None:
    global _ORCH_DB_INITIALIZED
    with _ORCH_LOCK:
        if _ORCH_DB_INITIALIZED and os.path.isfile(ORCH_DB_PATH):
            return
        conn = _orch_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orch_groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    emoji TEXT NOT NULL DEFAULT '💬',
                    description TEXT NOT NULL DEFAULT '',
                    workspace TEXT NOT NULL DEFAULT 'E:\\ai-agent',
                    default_agent TEXT NOT NULL DEFAULT 'antigravity',
                    sort_order INTEGER NOT NULL DEFAULT 100
                );

                CREATE TABLE IF NOT EXISTS orch_items (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'android',
                    agent_id TEXT NOT NULL DEFAULT 'antigravity',
                    url TEXT NOT NULL DEFAULT '',
                    user_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    stage_label TEXT NOT NULL DEFAULT 'Queued',
                    title TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    transcript TEXT NOT NULL DEFAULT '',
                    response_md TEXT NOT NULL DEFAULT '',
                    error_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sync_origin TEXT NOT NULL DEFAULT 'pc'
                );

                CREATE INDEX IF NOT EXISTS idx_orch_items_group ON orch_items(group_id, created_at);

                CREATE TABLE IF NOT EXISTS orch_commands (
                    id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    item_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_orch_cmds_status ON orch_commands(status, created_at);

                CREATE TABLE IF NOT EXISTS orch_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )
            for row in ORCH_DEFAULT_GROUPS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO orch_groups
                        (id, name, emoji, description, workspace, default_agent, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            conn.commit()
            _ORCH_DB_INITIALIZED = True
        finally:
            conn.close()


def _dedup_join_parts(parts: list[str], sep: str) -> str:
    unique: list[str] = []
    for p in parts:
        s = (p or "").strip()
        if not s:
            continue
        if any(s == u or s in u for u in unique):
            continue
        unique = [u for u in unique if u not in s]
        unique.append(s)
    return sep.join(unique)


def _extract_url_and_note(raw_text: str, extra_note: str = "") -> tuple[str, str]:
    text = (raw_text or "").strip()
    extra = (extra_note or "").strip()
    m = ORCH_URL_RE.search(text)
    if not m:
        return "", _dedup_join_parts([text, extra], "\n")
    url = m.group(0).rstrip(".,);!")
    remainder = re.sub(r"\s+", " ", (text[: m.start()] + " " + text[m.end() :])).strip()
    return url, _dedup_join_parts([remainder, extra], " — ")


def _get_pc_last_seen(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM orch_meta WHERE key = 'pc_last_heartbeat'").fetchone()
    return row["value"] if row and row["value"] else None


@app.route("/orch/health", methods=["GET"])
def orch_health():
    _init_orch_db()
    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            pc_last_seen = _get_pc_last_seen(conn)
            pending = conn.execute("SELECT COUNT(*) FROM orch_commands WHERE status = 'pending'").fetchone()[0]
            return jsonify({
                "ok": True,
                "service": "orchestrator_relay",
                "pc_last_seen": pc_last_seen,
                "pending_commands": pending,
            })
        finally:
            conn.close()


@app.route("/orch/groups", methods=["GET", "POST"])
def orch_groups():
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if request.method == "GET":
        with _ORCH_LOCK:
            conn = _orch_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT g.*,
                           (SELECT COUNT(*) FROM orch_items i WHERE i.group_id = g.id) AS item_count,
                           (SELECT COUNT(*) FROM orch_items i WHERE i.group_id = g.id AND i.status IN ('queued', 'running')) AS active_count
                    FROM orch_groups g
                    ORDER BY g.sort_order ASC, g.name ASC
                    """
                ).fetchall()
                return jsonify({
                    "ok": True,
                    "groups": [dict(r) for r in rows],
                    "pc_last_seen": _get_pc_last_seen(conn),
                })
            finally:
                conn.close()

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:40]
    slug = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:32]
    if not slug:
        return jsonify({"ok": False, "error": "Group name must contain letters or digits."}), 400
    default_agent = str(data.get("default_agent") or "antigravity").strip().lower()
    if default_agent not in ("antigravity", "hermes"):
        default_agent = "antigravity"
    emoji = str(data.get("emoji") or "💬").strip()[:4] or "💬"
    description = str(data.get("description") or "").strip()[:400]
    workspace = str(data.get("workspace") or r"E:\ai-agent").strip() or r"E:\ai-agent"

    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            exists = conn.execute("SELECT 1 FROM orch_groups WHERE id = ?", (slug,)).fetchone()
            if exists:
                return jsonify({"ok": False, "error": f"Group '{slug}' already exists."}), 409
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 10) FROM orch_groups").fetchone()[0]
            sort_order = int(max_order) + 1
            conn.execute(
                """
                INSERT INTO orch_groups (id, name, emoji, description, workspace, default_agent, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (slug, name, emoji, description, workspace, default_agent, sort_order),
            )
            payload = {
                "id": slug,
                "name": name,
                "emoji": emoji,
                "description": description,
                "workspace": workspace,
                "default_agent": default_agent,
            }
            conn.execute(
                """
                INSERT INTO orch_commands (id, action, item_id, payload_json, status, created_at)
                VALUES (?, 'create_group', '', ?, 'pending', ?)
                """,
                (uuid.uuid4().hex[:16], json.dumps(payload, ensure_ascii=False), _orch_now_iso()),
            )
            conn.commit()
            grp = conn.execute(
                """
                SELECT g.*, 0 AS item_count, 0 AS active_count FROM orch_groups g WHERE g.id = ?
                """,
                (slug,),
            ).fetchone()
            return jsonify({"ok": True, "group": dict(grp)})
        finally:
            conn.close()


@app.route("/orch/items", methods=["GET", "POST"])
def orch_items():
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if request.method == "GET":
        group_id = (request.args.get("group_id") or "").strip() or None
        try:
            limit = max(1, min(300, int(request.args.get("limit", "100"))))
        except Exception:
            limit = 100
        with _ORCH_LOCK:
            conn = _orch_conn()
            try:
                if group_id:
                    rows = conn.execute(
                        "SELECT * FROM orch_items WHERE group_id = ? ORDER BY created_at ASC LIMIT ?",
                        (group_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM orch_items ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return jsonify({
                    "ok": True,
                    "items": [dict(r) for r in rows],
                    "pc_last_seen": _get_pc_last_seen(conn),
                })
            finally:
                conn.close()

    data = request.get_json(silent=True) or {}
    group_id = str(data.get("group_id") or "quick_decode").strip()
    raw_url = str(data.get("url") or "").strip()
    raw_text = raw_url or str(data.get("text") or "").strip()
    extra_note = str(data.get("note") or (data.get("text") if raw_url else "") or "").strip()
    url, parsed_note = _extract_url_and_note(raw_text, extra_note)
    if not url and not parsed_note:
        return jsonify({"ok": False, "error": "Provide a URL, prompt text, or note."}), 400

    req_agent = (data.get("agent_id") or "").strip().lower()
    if req_agent and req_agent not in ("antigravity", "hermes"):
        return jsonify({"ok": False, "error": f"Unknown agent '{req_agent}'"}), 400
    source = str(data.get("source") or "android").strip()[:32] or "android"

    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            grp = conn.execute("SELECT * FROM orch_groups WHERE id = ?", (group_id,)).fetchone()
            if not grp:
                return jsonify({"ok": False, "error": f"Unknown group '{group_id}'"}), 400
            eff_agent = req_agent or grp["default_agent"] or "antigravity"
            item_id = uuid.uuid4().hex[:12]
            now = _orch_now_iso()
            user_note = parsed_note or url
            conn.execute(
                """
                INSERT INTO orch_items (
                    id, group_id, source, agent_id, url, user_note, status, stage_label,
                    created_at, updated_at, sync_origin
                )
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 'Queued on PythonAnywhere (waiting for PC)', ?, ?, 'mobile_relay')
                """,
                (item_id, group_id, source, eff_agent, url, user_note, now, now),
            )
            payload = {
                "group_id": group_id,
                "agent_id": eff_agent,
                "url": url,
                "text": raw_text,
                "note": user_note,
                "source": source,
            }
            conn.execute(
                """
                INSERT INTO orch_commands (id, action, item_id, payload_json, status, created_at)
                VALUES (?, 'submit', ?, ?, 'pending', ?)
                """,
                (uuid.uuid4().hex[:16], item_id, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM orch_items WHERE id = ?", (item_id,)).fetchone()
            return jsonify({"ok": True, "item": dict(row), "via": "pythonanywhere"})
        finally:
            conn.close()


@app.route("/orch/items/<item_id>/rerun", methods=["POST"])
def orch_rerun_item(item_id: str):
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            it = conn.execute("SELECT * FROM orch_items WHERE id = ?", (item_id,)).fetchone()
            if not it:
                return jsonify({"ok": False, "error": "Item not found"}), 404
            target_agent = str(data.get("agent_id") or it["agent_id"]).strip().lower()
            if target_agent not in ("antigravity", "hermes"):
                return jsonify({"ok": False, "error": f"Unknown agent '{target_agent}'"}), 400
            target_group = str(data.get("group_id") or it["group_id"]).strip()
            grp = conn.execute("SELECT 1 FROM orch_groups WHERE id = ?", (target_group,)).fetchone()
            if not grp:
                return jsonify({"ok": False, "error": f"Unknown group '{target_group}'"}), 400
            now = _orch_now_iso()
            conn.execute(
                """
                UPDATE orch_items
                SET group_id = ?, agent_id = ?, status = 'queued',
                    stage_label = ?, error_text = '', updated_at = ?
                WHERE id = ?
                """,
                (target_group, target_agent, f"Re-queued via PythonAnywhere ({target_agent})", now, item_id),
            )
            payload = {"group_id": target_group, "agent_id": target_agent}
            conn.execute(
                """
                INSERT INTO orch_commands (id, action, item_id, payload_json, status, created_at)
                VALUES (?, 'rerun', ?, ?, 'pending', ?)
                """,
                (uuid.uuid4().hex[:16], item_id, json.dumps(payload, ensure_ascii=False), now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM orch_items WHERE id = ?", (item_id,)).fetchone()
            return jsonify({"ok": True, "item": dict(row), "via": "pythonanywhere"})
        finally:
            conn.close()


@app.route("/orch/items/<item_id>", methods=["DELETE"])
def orch_delete_item(item_id: str):
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            row = conn.execute("SELECT id, sync_origin FROM orch_items WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Item not found"}), 404
            pending_submit = conn.execute(
                "SELECT 1 FROM orch_commands WHERE item_id = ? AND action = 'submit' AND status = 'pending'",
                (item_id,),
            ).fetchone()
            conn.execute(
                "UPDATE orch_commands SET status = 'cancelled' WHERE item_id = ? AND status = 'pending'",
                (item_id,),
            )
            if not pending_submit:
                conn.execute(
                    """
                    INSERT INTO orch_commands (id, action, item_id, payload_json, status, created_at)
                    VALUES (?, 'delete', ?, '{}', 'pending', ?)
                    """,
                    (uuid.uuid4().hex[:16], item_id, _orch_now_iso()),
                )
            conn.execute("DELETE FROM orch_items WHERE id = ?", (item_id,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()


@app.route("/orch/pc/pull", methods=["GET"])
def orch_pc_pull():
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    now = _orch_now_iso()
    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            conn.execute(
                """
                INSERT INTO orch_meta (key, value) VALUES ('pc_last_heartbeat', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
            rows = conn.execute(
                "SELECT * FROM orch_commands WHERE status = 'pending' ORDER BY created_at ASC"
            ).fetchall()
            commands = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d.get("payload_json") or "{}")
                except Exception:
                    d["payload"] = {}
                commands.append(d)
            conn.commit()
            return jsonify({"ok": True, "commands": commands, "server_time": now})
        finally:
            conn.close()


@app.route("/orch/pc/push", methods=["POST"])
def orch_pc_push():
    _init_orch_db()
    if not _orch_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    ack_ids = [str(x) for x in (data.get("ack_command_ids") or []) if x]
    groups = data.get("groups") or []
    items = data.get("items") or []
    full_items_sync = bool(data.get("full_items_sync", True))
    now = _orch_now_iso()

    with _ORCH_LOCK:
        conn = _orch_conn()
        try:
            conn.execute(
                """
                INSERT INTO orch_meta (key, value) VALUES ('pc_last_heartbeat', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
            for cid in ack_ids:
                conn.execute(
                    "UPDATE orch_commands SET status = 'done' WHERE id = ? AND status = 'pending'",
                    (cid,),
                )
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
            conn.execute(
                "DELETE FROM orch_commands WHERE status IN ('done', 'cancelled') AND created_at < ?",
                (cutoff,),
            )

            for g in groups:
                gid = str(g.get("id") or "").strip()
                if not gid:
                    continue
                conn.execute(
                    """
                    INSERT INTO orch_groups (id, name, emoji, description, workspace, default_agent, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        emoji = excluded.emoji,
                        description = excluded.description,
                        workspace = excluded.workspace,
                        default_agent = excluded.default_agent,
                        sort_order = excluded.sort_order
                    """,
                    (
                        gid,
                        str(g.get("name") or gid),
                        str(g.get("emoji") or "💬"),
                        str(g.get("description") or ""),
                        str(g.get("workspace") or r"E:\ai-agent"),
                        str(g.get("default_agent") or "antigravity"),
                        int(g.get("sort_order") or 100),
                    ),
                )

            pending_submits = {
                r[0]
                for r in conn.execute(
                    "SELECT item_id FROM orch_commands WHERE status = 'pending' AND action = 'submit' AND item_id != ''"
                ).fetchall()
            }
            pending_mutations = {
                r[0]
                for r in conn.execute(
                    "SELECT item_id FROM orch_commands WHERE status = 'pending' AND action IN ('rerun', 'delete') AND item_id != ''"
                ).fetchall()
            }

            pushed_ids = set()
            for it in items:
                iid = str(it.get("id") or "").strip()
                if not iid:
                    continue
                pushed_ids.add(iid)
                if iid in pending_mutations:
                    continue
                conn.execute(
                    """
                    INSERT INTO orch_items (
                        id, group_id, source, agent_id, url, user_note, status, stage_label,
                        title, platform, transcript, response_md, error_text,
                        created_at, updated_at, sync_origin
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pc')
                    ON CONFLICT(id) DO UPDATE SET
                        group_id = excluded.group_id,
                        source = excluded.source,
                        agent_id = excluded.agent_id,
                        url = excluded.url,
                        user_note = excluded.user_note,
                        status = excluded.status,
                        stage_label = excluded.stage_label,
                        title = excluded.title,
                        platform = excluded.platform,
                        transcript = excluded.transcript,
                        response_md = excluded.response_md,
                        error_text = excluded.error_text,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at,
                        sync_origin = 'pc'
                    """,
                    (
                        iid,
                        str(it.get("group_id") or "quick_decode"),
                        str(it.get("source") or "pwa"),
                        str(it.get("agent_id") or "antigravity"),
                        str(it.get("url") or ""),
                        str(it.get("user_note") or ""),
                        str(it.get("status") or "queued"),
                        str(it.get("stage_label") or ""),
                        str(it.get("title") or ""),
                        str(it.get("platform") or ""),
                        str(it.get("transcript") or ""),
                        str(it.get("response_md") or ""),
                        str(it.get("error_text") or ""),
                        str(it.get("created_at") or now),
                        str(it.get("updated_at") or now),
                    ),
                )

            if full_items_sync:
                existing_rows = conn.execute("SELECT id FROM orch_items").fetchall()
                for er in existing_rows:
                    eid = er[0]
                    if eid not in pushed_ids and eid not in pending_submits and eid not in pending_mutations:
                        conn.execute("DELETE FROM orch_items WHERE id = ?", (eid,))

            conn.commit()
            return jsonify({
                "ok": True,
                "synced_groups": len(groups),
                "synced_items": len(items),
            })
        finally:
            conn.close()


@app.route("/orch/apk", methods=["GET"])
def orch_download_apk():
    if not _orch_apk_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    apk_name = "Orchestrator.apk"
    apk_path = os.path.join(ORCH_APK_DIR, apk_name)
    if not os.path.isfile(apk_path):
        return jsonify({"ok": False, "error": "apk_not_uploaded"}), 404
    return send_from_directory(
        ORCH_APK_DIR,
        apk_name,
        as_attachment=True,
        download_name=apk_name,
        mimetype="application/vnd.android.package-archive",
        max_age=0,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
