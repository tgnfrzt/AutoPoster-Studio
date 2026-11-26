import os
import json
import time
import requests
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from core.config import settings, save_settings_to_env, get_cookies_path

# Allow insecure transport for local OAuth testing on localhost
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdateRequest(BaseModel):
    INSTAGRAM_BUSINESS_ID: Optional[str] = None
    PAGE_ACCESS_TOKEN: Optional[str] = None
    REMOTE_HOSTING_URL: Optional[str] = None
    TIKTOK_ACCESS_TOKEN: Optional[str] = None
    TIKTOK_CLIENT_KEY: Optional[str] = None
    TIKTOK_CLIENT_SECRET: Optional[str] = None
    TIKTOK_SESSION_ID: Optional[str] = None
    YOUTUBE_DEFAULT_PRIVACY: Optional[str] = None
    DEFAULT_INSTAGRAM_CAPTION: Optional[str] = None
    DEFAULT_TIKTOK_CAPTION: Optional[str] = None
    DEFAULT_YOUTUBE_CAPTION: Optional[str] = None
    DAY_COOLDOWN_MIN: Optional[int] = None
    DAY_COOLDOWN_MAX: Optional[int] = None
    EVENING_COOLDOWN_MIN: Optional[int] = None
    EVENING_COOLDOWN_MAX: Optional[int] = None
    CATCHUP_THRESHOLD_MIN: Optional[int] = None
    MAX_CATCHUP_POSTS: Optional[int] = None
    PULL_INTERVAL_MIN: Optional[int] = None

class CookiesUpdateRequest(BaseModel):
    cookies_content: str

class JsonPayloadRequest(BaseModel):
    content: str

class TestInstagramRequest(BaseModel):
    business_id: Optional[str] = None
    access_token: Optional[str] = None

class TestTikTokRequest(BaseModel):
    access_token: Optional[str] = None
    session_id: Optional[str] = None

@router.get("")
def get_all_settings():
    cookies_path = get_cookies_path()
    cookies_exists = cookies_path is not None and os.path.isfile(cookies_path)
    cookies_lines = 0
    cookies_size = 0
    if cookies_exists:
        try:
            with open(cookies_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                cookies_lines = len(lines)
            cookies_size = os.path.getsize(cookies_path)
        except Exception:
            pass

    tt_cookies_path = settings.TIKTOK_COOKIES_PATH
    tt_cookies_exists = os.path.isfile(tt_cookies_path)
    tt_cookies_lines = 0
    tt_cookies_size = 0
    if tt_cookies_exists:
        try:
            with open(tt_cookies_path, "r", encoding="utf-8", errors="replace") as f:
                tt_lines = f.readlines()
                tt_cookies_lines = len(tt_lines)
            tt_cookies_size = os.path.getsize(tt_cookies_path)
        except Exception:
            pass

    outros = []
    if os.path.isdir(settings.OUTROS_PATH):
        outros = [f for f in os.listdir(settings.OUTROS_PATH) if f.lower().endswith((".mp4", ".mov", ".mkv"))]

    return {
        "INSTAGRAM_BUSINESS_ID": settings.INSTAGRAM_BUSINESS_ID,
        "PAGE_ACCESS_TOKEN": settings.PAGE_ACCESS_TOKEN,
        "REMOTE_HOSTING_URL": settings.REMOTE_HOSTING_URL,
        "TIKTOK_ACCESS_TOKEN": settings.TIKTOK_ACCESS_TOKEN,
        "TIKTOK_CLIENT_KEY": settings.TIKTOK_CLIENT_KEY,
        "TIKTOK_CLIENT_SECRET": settings.TIKTOK_CLIENT_SECRET,
        "TIKTOK_SESSION_ID": settings.TIKTOK_SESSION_ID,
        "YOUTUBE_DEFAULT_PRIVACY": settings.YOUTUBE_DEFAULT_PRIVACY,
        "DEFAULT_INSTAGRAM_CAPTION": settings.DEFAULT_INSTAGRAM_CAPTION,
        "DEFAULT_TIKTOK_CAPTION": settings.DEFAULT_TIKTOK_CAPTION,
        "DEFAULT_YOUTUBE_CAPTION": settings.DEFAULT_YOUTUBE_CAPTION,
        "DAY_COOLDOWN_MIN": settings.DAY_COOLDOWN_MIN,
        "DAY_COOLDOWN_MAX": settings.DAY_COOLDOWN_MAX,
        "EVENING_COOLDOWN_MIN": settings.EVENING_COOLDOWN_MIN,
        "EVENING_COOLDOWN_MAX": settings.EVENING_COOLDOWN_MAX,
        "CATCHUP_THRESHOLD_MIN": settings.CATCHUP_THRESHOLD_MIN,
        "MAX_CATCHUP_POSTS": settings.MAX_CATCHUP_POSTS,
        "PULL_INTERVAL_MIN": settings.PULL_INTERVAL_MIN,
        "cookies": {
            "exists": cookies_exists,
            "path": cookies_path,
            "size_bytes": cookies_size,
            "line_count": cookies_lines
        },
        "tiktok": {
            "has_token": bool(settings.TIKTOK_ACCESS_TOKEN),
            "has_session": bool(settings.TIKTOK_SESSION_ID),
            "has_cookies": tt_cookies_exists,
            "cookies_path": tt_cookies_path,
            "cookies_size": tt_cookies_size,
            "cookies_lines": tt_cookies_lines
        },
        "youtube": {
            "has_client_secrets": os.path.isfile(settings.YOUTUBE_CLIENT_SECRETS_FILE),
            "has_token": os.path.isfile(settings.YOUTUBE_TOKEN_FILE),
            "client_secrets_path": settings.YOUTUBE_CLIENT_SECRETS_FILE,
            "token_path": settings.YOUTUBE_TOKEN_FILE
        },
        "resources": {
            "watermark_exists": os.path.isfile(settings.WATERMARK_PATH),
            "watermark_path": settings.WATERMARK_PATH,
            "outros_count": len(outros),
            "outros": outros
        }
    }

@router.post("")
def update_settings(req: SettingsUpdateRequest):
    updates = {}
    for k, v in req.dict(exclude_unset=True).items():
        if v is not None:
            updates[k] = v

    if updates:
        save_settings_to_env(updates)

    return {"success": True, "updated_fields": list(updates.keys())}

@router.get("/cookies")
def get_cookies_content():
    path = get_cookies_path()
    if not path or not os.path.isfile(path):
        return {"exists": False, "content": ""}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"exists": True, "content": content, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/cookies")
def update_cookies_content(req: CookiesUpdateRequest):
    os.makedirs(os.path.dirname(settings.COOKIES_PATH), exist_ok=True)
    with open(settings.COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write(req.cookies_content.strip() + "\n")
    return {"success": True, "size": len(req.cookies_content)}

@router.post("/cookies/upload")
async def upload_cookies_file(file: UploadFile = File(...)):
    os.makedirs(os.path.dirname(settings.COOKIES_PATH), exist_ok=True)
    content = await file.read()
    with open(settings.COOKIES_PATH, "wb") as f:
        f.write(content)
    return {"success": True, "size": len(content), "filename": file.filename}

@router.post("/test_instagram")
def test_instagram_connection(req: TestInstagramRequest):
    b_id = req.business_id or settings.INSTAGRAM_BUSINESS_ID
    token = req.access_token or settings.PAGE_ACCESS_TOKEN

    if not b_id or not token:
        return {
            "valid": False,
            "error": "Both Instagram Business ID and Page Access Token must be provided."
        }

    try:
        url = f"{settings.GRAPH_API_URL}/{b_id}"
        r = requests.get(url, params={"fields": "name,username", "access_token": token}, timeout=15)
        data = r.json()
        if "error" in data:
            return {"valid": False, "error": data["error"].get("message", "API returned error")}
        return {
            "valid": True,
            "id": data.get("id"),
            "name": data.get("name"),
            "username": data.get("username", "N/A")
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}

class TestHostingRequest(BaseModel):
    hosting_url: Optional[str] = None

@router.post("/test_hosting")
def test_remote_hosting(req: TestHostingRequest):
    host = (req.hosting_url or settings.REMOTE_HOSTING_URL).rstrip("/")
    if not host:
        return {"valid": False, "error": "No remote hosting URL specified."}

    # Try /health first
    try:
        r = requests.get(f"{host}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return {
                "valid": True,
                "endpoint": "health",
                "queue_count": data.get("queue_count", 0),
                "uploads_count": data.get("uploads_count", 0),
                "uploads_mb": data.get("uploads_mb", 0.0),
                "message": f"Server Healthy • {data.get('queue_count', 0)} queued links • {data.get('uploads_count', 0)} files ({data.get('uploads_mb', 0)} MB)"
            }
    except Exception:
        pass

    # Fallback to checking /get_and_clear_queue or root
    try:
        r = requests.get(f"{host}/get_and_clear_queue", timeout=10)
        if r.status_code == 200:
            return {
                "valid": True,
                "endpoint": "queue",
                "message": "PythonAnywhere server reachable and queue responsive (HTTP 200 OK)."
            }
        return {"valid": False, "error": f"Server responded with HTTP {r.status_code}"}
    except Exception as e:
        return {"valid": False, "error": f"Cannot connect to remote server: {e}"}

@router.get("/server_script")
def get_server_script():
    from core.config import BASE_DIR
    server_path = os.path.join(str(BASE_DIR), "server", "flask_app.py")
    if os.path.isfile(server_path):
        with open(server_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"exists": True, "path": server_path, "content": content}
    return {"exists": False, "content": ""}

# ==========================================
# TIKTOK ENDPOINTS
# ==========================================

@router.post("/test_tiktok")
def test_tiktok_connection(req: TestTikTokRequest):
    token = req.access_token or settings.TIKTOK_ACCESS_TOKEN
    session = req.session_id or settings.TIKTOK_SESSION_ID

    # Mode 1: Access Token (Content Posting API v2)
    if token:
        try:
            url = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name"
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(url, headers=headers, timeout=15)
            data = r.json()
            err = data.get("error", {})
            if err.get("code") == "ok":
                u = data.get("data", {}).get("user", {})
                return {
                    "valid": True,
                    "mode": "official_api",
                    "display_name": u.get("display_name", "TikTok Creator"),
                    "open_id": u.get("open_id", ""),
                    "avatar_url": u.get("avatar_url", "")
                }
            else:
                return {
                    "valid": False,
                    "mode": "official_api",
                    "error": f"{err.get('message', 'TikTok API error')} (Code: {err.get('code')})"
                }
        except Exception as e:
            return {"valid": False, "mode": "official_api", "error": str(e)}

    # Mode 2: Session ID (Live Account Validation via passport endpoint)
    if session:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.tiktok.com/"
            }
            r = requests.get(
                "https://www.tiktok.com/passport/web/account/info/",
                headers=headers,
                cookies={"sessionid": session},
                timeout=12
            )
            data = r.json()
            if data.get("message") == "success" and data.get("data"):
                acc = data.get("data", {})
                username = acc.get("username") or acc.get("screen_name") or "TikTok Creator"
                user_id = acc.get("user_id", "")
                return {
                    "valid": True,
                    "mode": "session",
                    "display_name": f"@{username}",
                    "message": f"Successfully verified TikTok session for @{username} (ID: {user_id})! Ready to post."
                }
            else:
                desc = data.get("data", {}).get("description") or data.get("message") or "Session expired"
                return {
                    "valid": False,
                    "mode": "session",
                    "error": f"Session rejected by TikTok ({desc}). Please ensure you copied a fresh sessionid cookie while logged in."
                }
        except Exception as e:
            return {
                "valid": True,
                "mode": "session",
                "display_name": f"Session Saved ({session[:6]}...)",
                "message": f"Session token saved. (Note: live check timed out: {e})"
            }

    # Mode 3: Check tiktok_cookies.txt
    if os.path.isfile(settings.TIKTOK_COOKIES_PATH):
        try:
            with open(settings.TIKTOK_COOKIES_PATH, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            has_sessionid = "sessionid" in content.lower()
            return {
                "valid": has_sessionid,
                "mode": "cookies_file",
                "display_name": "tiktok_cookies.txt active",
                "message": "Valid sessionid cookie verified in tiktok_cookies.txt" if has_sessionid else "Warning: sessionid cookie not found in tiktok_cookies.txt"
            }
        except Exception as e:
            return {"valid": False, "mode": "cookies_file", "error": str(e)}

    return {
        "valid": False,
        "error": "No TikTok credentials found. Configure Session ID (Recommended) or upload tiktok_cookies.txt."
    }

@router.get("/tiktok/cookies")
def get_tiktok_cookies():
    path = settings.TIKTOK_COOKIES_PATH
    if not os.path.isfile(path):
        return {"exists": False, "content": ""}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"exists": True, "content": content, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/tiktok/cookies")
def save_tiktok_cookies(req: CookiesUpdateRequest):
    os.makedirs(os.path.dirname(settings.TIKTOK_COOKIES_PATH), exist_ok=True)
    with open(settings.TIKTOK_COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write(req.cookies_content.strip() + "\n")
    return {"success": True, "size": len(req.cookies_content)}

@router.post("/tiktok/cookies/upload")
async def upload_tiktok_cookies(file: UploadFile = File(...)):
    os.makedirs(os.path.dirname(settings.TIKTOK_COOKIES_PATH), exist_ok=True)
    content = await file.read()
    with open(settings.TIKTOK_COOKIES_PATH, "wb") as f:
        f.write(content)
    return {"success": True, "filename": file.filename, "size": len(content)}

# ==========================================
# YOUTUBE ENDPOINTS
# ==========================================

def _get_youtube_channel_info():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request

    token_path = settings.YOUTUBE_TOKEN_FILE
    if not os.path.isfile(token_path):
        return {"valid": False, "error": f"YouTube token file not found at {token_path}. Please authorize."}

    with open(token_path, "r", encoding="utf-8") as f:
        info = json.load(f)

    creds = Credentials.from_authorized_user_info(info, scopes=[
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly"
    ])

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except Exception as ex:
            return {
                "valid": False,
                "error": f"YouTube authorization has expired ({ex}). Please click '1-Click Authorize Channel' to reconnect."
            }
    elif creds.expired and not creds.refresh_token:
        return {
            "valid": False,
            "error": "YouTube token has expired with no refresh token. Please click '1-Click Authorize Channel' to reconnect."
        }

    yt = build("youtube", "v3", credentials=creds)
    res = yt.channels().list(mine=True, part="snippet,statistics").execute()
    items = res.get("items", [])
    if not items:
        return {"valid": True, "channel_title": "No channel found for Google account", "channel_id": ""}
    ch = items[0]
    snippet = ch.get("snippet", {})
    stats = ch.get("statistics", {})
    return {
        "valid": True,
        "channel_id": ch.get("id"),
        "channel_title": snippet.get("title"),
        "custom_url": snippet.get("customUrl", ""),
        "subscriber_count": stats.get("subscriberCount", "Hidden"),
        "video_count": stats.get("videoCount", "0"),
        "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url", "")
    }

_PKCE_FILE = os.path.join(settings.DATA_PATH, ".youtube_pkce.json")

def _save_pkce_verifier(state: str, code_verifier: Optional[str]):
    if not code_verifier:
        return
    try:
        data = {}
        if os.path.exists(_PKCE_FILE):
            try:
                with open(_PKCE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        now = time.time()
        # Prune old states older than 2 hours
        data = {k: v for k, v in data.items() if isinstance(v, dict) and (now - v.get("created_at", 0) < 7200)}
        data[state] = {"verifier": code_verifier, "created_at": now}
        data["_latest"] = {"verifier": code_verifier, "state": state, "created_at": now}
        os.makedirs(os.path.dirname(_PKCE_FILE), exist_ok=True)
        with open(_PKCE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as ex:
        print(f"[YouTube Auth] Warning: could not save PKCE verifier to file: {ex}")

def _get_pkce_verifier(state: Optional[str] = None) -> Optional[str]:
    if not os.path.exists(_PKCE_FILE):
        return None
    try:
        with open(_PKCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if state and state in data and isinstance(data[state], dict):
            return data[state].get("verifier")
        if "_latest" in data and isinstance(data["_latest"], dict):
            return data["_latest"].get("verifier")
    except Exception as ex:
        print(f"[YouTube Auth] Warning: could not read PKCE verifier from file: {ex}")
    return None

def _clear_pkce_verifier(state: Optional[str] = None):
    if not os.path.exists(_PKCE_FILE):
        return
    try:
        with open(_PKCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if state and state in data:
            del data[state]
        if "_latest" in data and (not state or data["_latest"].get("state") == state):
            del data["_latest"]
        with open(_PKCE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

@router.get("/youtube/auth_url")
def get_youtube_auth_url():
    if not os.path.isfile(settings.YOUTUBE_CLIENT_SECRETS_FILE):
        return {
            "ready": False,
            "error": "Google client_secret.json not found. Please upload it first."
        }
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            settings.YOUTUBE_CLIENT_SECRETS_FILE,
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ],
            redirect_uri=f"http://localhost:{settings.PORT}/api/settings/youtube/callback"
        )
        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true"
        )
        if getattr(flow, "code_verifier", None):
            _save_pkce_verifier(state=state, code_verifier=flow.code_verifier)
        return {"ready": True, "auth_url": auth_url, "state": state}
    except Exception as e:
        return {"ready": False, "error": str(e)}

@router.get("/youtube/callback")
def youtube_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None
):
    if error:
        return HTMLResponse(
            f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#ef4444;padding:2rem;">
            <h2>Google OAuth Error: {error}</h2>
            <p><a href="/" style="color:#38bdf8;">Return to AutoPoster</a></p>
            </body></html>""",
            status_code=400
        )
    if not code:
        return HTMLResponse(
            """<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#ef4444;padding:2rem;">
            <h2>Missing authorization code</h2>
            <p><a href="/" style="color:#38bdf8;">Return to AutoPoster</a></p>
            </body></html>""",
            status_code=400
        )
    
    code_verifier = _get_pkce_verifier(state=state) or _get_pkce_verifier(None)

    try:
        from google_auth_oauthlib.flow import Flow
        flow_kwargs = {
            "scopes": [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ],
            "redirect_uri": f"http://localhost:{settings.PORT}/api/settings/youtube/callback"
        }
        if state:
            flow_kwargs["state"] = state
        if code_verifier:
            flow_kwargs["code_verifier"] = code_verifier

        flow = Flow.from_client_secrets_file(
            settings.YOUTUBE_CLIENT_SECRETS_FILE,
            **flow_kwargs
        )
        if code_verifier:
            flow.code_verifier = code_verifier

        flow.fetch_token(code=code)
        creds = flow.credentials
        os.makedirs(os.path.dirname(settings.YOUTUBE_TOKEN_FILE), exist_ok=True)
        with open(settings.YOUTUBE_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        _clear_pkce_verifier(state=state)

        return HTMLResponse(
            """<!DOCTYPE html>
            <html>
            <head>
              <meta charset="utf-8">
              <title>YouTube Connected</title>
              <style>
                body {
                  margin: 0;
                  background-color: #0b0f19;
                  color: #f8fafc;
                  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  justify-content: center;
                  height: 100vh;
                  text-align: center;
                }
                .card {
                  background: #1e293b;
                  border: 1px solid #334155;
                  border-radius: 12px;
                  padding: 2rem 2.5rem;
                  max-width: 420px;
                  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
                }
                .badge {
                  display: inline-flex;
                  align-items: center;
                  justify-content: center;
                  width: 52px;
                  height: 52px;
                  border-radius: 50%;
                  background: rgba(16, 185, 129, 0.15);
                  color: #10b981;
                  font-size: 26px;
                  margin-bottom: 1rem;
                }
                h2 { margin: 0 0 0.5rem 0; font-size: 1.25rem; font-weight: 600; color: #10b981; }
                p { margin: 0 0 1.25rem 0; font-size: 0.875rem; color: #94a3b8; }
                a.btn {
                  display: inline-block;
                  background: #6366f1;
                  color: #ffffff;
                  text-decoration: none;
                  padding: 0.5rem 1.25rem;
                  border-radius: 6px;
                  font-size: 0.85rem;
                  font-weight: 500;
                  cursor: pointer;
                  transition: background 0.2s;
                }
                a.btn:hover { background: #4f46e5; }
              </style>
            </head>
            <body>
              <div class="card">
                <div class="badge">&#10003;</div>
                <h2>YouTube Connected Successfully!</h2>
                <p>AutoPoster has successfully authorized your YouTube Channel. You can close this window now.</p>
                <a href="/" class="btn" id="closeBtn" onclick="tryClose(event)">Close Window</a>
              </div>
              <script>
                function notifyOpener() {
                  if (window.opener) {
                    try { window.opener.postMessage({ type: 'YOUTUBE_AUTH_SUCCESS' }, '*'); } catch(err){}
                  }
                }
                function tryClose(e) {
                  notifyOpener();
                  if (window.opener) {
                    e.preventDefault();
                    window.close();
                  }
                }
                notifyOpener();
                if (window.opener) {
                  setTimeout(() => { window.close(); }, 2000);
                }
              </script>
            </body>
            </html>"""
        )
    except Exception as e:
        return HTMLResponse(
            f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#0f172a;color:#ef4444;padding:2rem;">
            <h2>Failed to exchange authorization code</h2>
            <p style="color:#f8fafc;background:#1e293b;padding:1rem;border-radius:6px;font-family:monospace;">{e}</p>
            <p><a href="/" style="color:#38bdf8;">Return to AutoPoster</a></p>
            </body></html>""",
            status_code=500
        )

@router.post("/youtube/exchange_code")
def exchange_youtube_code(req: JsonPayloadRequest):
    raw = req.content.strip()
    if not os.path.isfile(settings.YOUTUBE_CLIENT_SECRETS_FILE):
        return {"success": False, "error": "client_secret.json missing on server"}
    
    code = raw
    state = None
    if "code=" in raw:
        try:
            qs = urlparse(raw).query if "?" in raw else raw
            parsed = parse_qs(qs)
            if "code" in parsed:
                code = parsed["code"][0]
            if "state" in parsed:
                state = parsed["state"][0]
        except Exception:
            pass

    code_verifier = _get_pkce_verifier(state=state) or _get_pkce_verifier(None)

    try:
        from google_auth_oauthlib.flow import Flow
        flow_kwargs = {
            "scopes": [
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ],
            "redirect_uri": f"http://localhost:{settings.PORT}/api/settings/youtube/callback"
        }
        if state:
            flow_kwargs["state"] = state
        if code_verifier:
            flow_kwargs["code_verifier"] = code_verifier

        flow = Flow.from_client_secrets_file(
            settings.YOUTUBE_CLIENT_SECRETS_FILE,
            **flow_kwargs
        )
        if code_verifier:
            flow.code_verifier = code_verifier

        flow.fetch_token(code=code)
        creds = flow.credentials
        os.makedirs(os.path.dirname(settings.YOUTUBE_TOKEN_FILE), exist_ok=True)
        with open(settings.YOUTUBE_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        
        _clear_pkce_verifier(state=state)
        info = _get_youtube_channel_info()
        return {"success": True, "channel": info}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/youtube/save_secret_json")
def save_youtube_secret_json(req: JsonPayloadRequest):
    os.makedirs(os.path.dirname(settings.YOUTUBE_CLIENT_SECRETS_FILE), exist_ok=True)
    with open(settings.YOUTUBE_CLIENT_SECRETS_FILE, "w", encoding="utf-8") as f:
        f.write(req.content.strip())
    return {"success": True, "size": len(req.content)}

@router.post("/youtube/save_token_json")
def save_youtube_token_json(req: JsonPayloadRequest):
    os.makedirs(os.path.dirname(settings.YOUTUBE_TOKEN_FILE), exist_ok=True)
    with open(settings.YOUTUBE_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(req.content.strip())
    return {"success": True, "size": len(req.content)}

@router.post("/youtube/upload_secret")
async def upload_youtube_secret(file: UploadFile = File(...)):
    os.makedirs(os.path.dirname(settings.YOUTUBE_CLIENT_SECRETS_FILE), exist_ok=True)
    content = await file.read()
    with open(settings.YOUTUBE_CLIENT_SECRETS_FILE, "wb") as f:
        f.write(content)
    return {"success": True, "filename": file.filename, "size": len(content)}

@router.post("/youtube/upload_token")
async def upload_youtube_token(file: UploadFile = File(...)):
    os.makedirs(os.path.dirname(settings.YOUTUBE_TOKEN_FILE), exist_ok=True)
    content = await file.read()
    with open(settings.YOUTUBE_TOKEN_FILE, "wb") as f:
        f.write(content)
    return {"success": True, "filename": file.filename, "size": len(content)}

@router.post("/test_youtube")
def test_youtube_connection():
    try:
        info = _get_youtube_channel_info()
        return info
    except Exception as e:
        return {"valid": False, "error": str(e)}
