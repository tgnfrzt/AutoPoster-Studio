import os
import sys
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:
    pass

USERNAME = os.getenv("PYTHONANYWHERE_USERNAME", "").strip()
TOKEN = os.getenv("PYTHONANYWHERE_API_TOKEN", "").strip()
DOMAIN = f"{USERNAME}.pythonanywhere.com" if USERNAME else ""
LOCAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flask_app.py")
REMOTE_PATH = f"/home/{USERNAME}/mysite/flask_app.py" if USERNAME else ""

headers = {
    "Authorization": f"Token {TOKEN}"
}

def deploy():
    if not USERNAME or not TOKEN:
        print("[!] Set PYTHONANYWHERE_USERNAME and PYTHONANYWHERE_API_TOKEN in .env before deploying.")
        return False
    print(f"[*] Verifying credentials for {USERNAME}...")
    cpu_res = requests.get(
        f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/cpu/",
        headers=headers
    )
    if cpu_res.status_code != 200:
        print(f"[!] Authentication failed: {cpu_res.status_code} - {cpu_res.text}")
        return False
    print("[+] Credentials valid!")

    print(f"[*] Uploading {LOCAL_FILE} to {REMOTE_PATH}...")
    upload_url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/files/path{REMOTE_PATH}"
    with open(LOCAL_FILE, "rb") as f:
        res = requests.post(
            upload_url,
            headers=headers,
            files={"content": f}
        )
    if res.status_code not in (200, 201):
        print(f"[!] Upload failed ({res.status_code}): {res.text}")
        return False
    print(f"[+] File uploaded successfully! Status: {res.status_code}")

    print(f"[*] Reloading web app {DOMAIN}...")
    reload_url = f"https://www.pythonanywhere.com/api/v0/user/{USERNAME}/webapps/{DOMAIN}/reload/"
    res_reload = requests.post(reload_url, headers=headers)
    if res_reload.status_code not in (200, 201):
        print(f"[!] Reload failed ({res_reload.status_code}): {res_reload.text}")
        return False
    print(f"[+] Web app {DOMAIN} reloaded successfully!")

    print("[*] Testing live endpoints...")
    test_res = requests.get(f"https://{DOMAIN}/api/orar", timeout=10)
    print(f"[+] GET /api/orar status: {test_res.status_code} -> {test_res.text}")
    return True

if __name__ == "__main__":
    success = deploy()
    sys.exit(0 if success else 1)
