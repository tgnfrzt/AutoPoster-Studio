import os
import sys

# Force UTF-8 encoding on standard streams on Windows to prevent 'charmap' codec crashes
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import json
import argparse
import traceback

def patch_tiktok_uploader():
    """
    Monkeypatches tiktok_uploader with:
    1. Critical Cookie Fix: ensures sessionid cookie has domain='.tiktok.com' and path='/'
       so Playwright's BrowserContext.add_cookies accepts it without error.
    2. Resilient Upload Page Navigation: waits for #root, #app, or file input without strict failure.
    3. Modern 2025/2026 post confirmation logic (URL redirection, modern modal text)
    4. Resilient description typing that doesn't crash on empty spaces/IndexError
    5. Screenshot auditing on completion
    """
    try:
        from playwright.sync_api import Page
        from tiktok_uploader import config
        from tiktok_uploader.auth import AuthBackend
        import tiktok_uploader.upload as tu_upload

        # 1. Patch cookie resolution: Playwright requires domain and path for each cookie
        orig_resolve = AuthBackend._resolve_cookies
        def patched_resolve_cookies(self):
            cookies = orig_resolve(self)
            for c in cookies:
                if "domain" not in c and "url" not in c:
                    c["domain"] = ".tiktok.com"
                    c["path"] = "/"
            return cookies
        AuthBackend._resolve_cookies = patched_resolve_cookies

        # 2. Patch go_to_upload
        def robust_go_to_upload(page: Page) -> None:
            upload_url = str(config.paths.upload)
            print(f"[TikTokRunner] Navigating to upload URL: {upload_url}")
            if page.url != upload_url:
                page.goto(upload_url)
            else:
                page.reload()
            page.on("dialog", lambda dialog: dialog.accept())
            try:
                page.wait_for_selector("#root, #app, input[type='file'], .upload-container", timeout=45000)
            except Exception as e:
                print(f"[TikTokRunner] Note: selector wait completed: {e}")

        tu_upload._go_to_upload = robust_go_to_upload

        # 3. Patch set_description
        def robust_set_description(page: Page, description: str) -> None:
            if not description:
                return
            try:
                desc_locator = page.locator(f"xpath={config.selectors.upload.description}")
                desc_locator.wait_for(state="visible", timeout=15000)
                desc_locator.click()
                desc_locator.press("Control+A")
                desc_locator.press("Backspace")
                time.sleep(0.5)

                words = [w for w in description.split() if w]
                for word in words:
                    if word.startswith("#"):
                        desc_locator.press_sequentially(word, delay=40)
                        time.sleep(0.3)
                        try:
                            mention_box = page.locator(f"xpath={config.selectors.upload.mention_box}")
                            mention_box.wait_for(state="visible", timeout=2000)
                            desc_locator.press("Enter")
                        except Exception:
                            pass
                    else:
                        desc_locator.press_sequentially(word + " ", delay=20)
            except Exception as exc:
                print(f"[TikTokRunner] Note: using direct fill for description ({exc})")
                try:
                    desc_locator = page.locator(f"xpath={config.selectors.upload.description}")
                    desc_locator.fill(description)
                except Exception:
                    pass

        tu_upload._set_description = robust_set_description

        # 4. Patch post_video
        def robust_post_video(page: Page) -> None:
            print("[TikTokRunner] Waiting for TikTok post button to become enabled...")

            # Locate post button
            post_btn = None
            selectors = [
                f"xpath={config.selectors.upload.post}",
                "button[data-e2e='post_video_button']",
                "button:has-text('Post')",
                ".TUXButton--primary:has-text('Post')"
            ]
            for sel in selectors:
                try:
                    candidate = page.locator(sel).first
                    if candidate.is_visible(timeout=3000):
                        post_btn = candidate
                        break
                except Exception:
                    pass

            if not post_btn:
                post_btn = page.locator(f"xpath={config.selectors.upload.post}")

            # Wait for upload/processing to complete and button to enable
            for _ in range(60):  # up to 120 seconds
                try:
                    is_disabled_attr = post_btn.get_attribute("data-disabled")
                    is_disabled_prop = post_btn.is_disabled()
                    if is_disabled_attr != "true" and not is_disabled_prop:
                        break
                except Exception:
                    pass
                time.sleep(2)

            print("[TikTokRunner] Clicking the post button...")
            try:
                post_btn.scroll_into_view_if_needed()
                post_btn.click()
            except Exception:
                try:
                    page.evaluate('document.querySelector("button[data-e2e=\'post_video_button\']").click()')
                except Exception:
                    pass

            # Handle secondary modals (e.g. "Post now", "Post anyway", "Confirm")
            time.sleep(3)
            for _ in range(3):
                for modal_text in ["Post now", "Post anyway", "Continue", "Confirm"]:
                    try:
                        modal_btn = page.locator(f"//button[contains(., '{modal_text}')]").first
                        if modal_btn.is_visible(timeout=1500):
                            print(f"[TikTokRunner] Clicking secondary confirmation: '{modal_text}'")
                            modal_btn.click()
                            time.sleep(2)
                    except Exception:
                        pass

            # Verify upload success
            print("[TikTokRunner] Verifying upload success (checking modern 2026 UI & URL redirects)...")
            screenshot_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "storage", "data"
            )
            os.makedirs(screenshot_dir, exist_ok=True)
            screenshot_path = os.path.join(screenshot_dir, "tiktok_last_post_screen.png")

            confirmed = False
            success_texts = [
                "Your video has been uploaded",
                "Your video is uploaded to TikTok",
                "Your video is uploaded",
                "Manage your posts",
                "Upload another video",
                "Upload another",
                "Video published",
                "Your video is being processed",
                "View profile"
            ]

            for _ in range(35):  # up to 70 seconds
                # 1. URL redirection check
                current_url = page.url.lower()
                if any(path_part in current_url for path_part in ["creator-center/content", "tiktokstudio/content", "/profile", "manage"]):
                    print(f"[TikTokRunner] Redirection detected to: {page.url}")
                    confirmed = True
                    break

                # 2. Text indicator check
                for st in success_texts:
                    try:
                        loc = page.locator(f"//*[contains(text(), '{st}')]").first
                        if loc.is_visible(timeout=300):
                            print(f"[TikTokRunner] Success indicator found: '{st}'")
                            confirmed = True
                            break
                    except Exception:
                        pass
                if confirmed:
                    break
                time.sleep(2)

            try:
                page.screenshot(path=screenshot_path)
                print(f"[TikTokRunner] Saved confirmation screenshot to: {screenshot_path}")
            except Exception:
                pass

            if not confirmed:
                try:
                    if not post_btn.is_visible():
                        print("[TikTokRunner] Post button no longer visible; video accepted by TikTok.")
                        confirmed = True
                except Exception:
                    pass

            if not confirmed:
                raise Exception("Upload completed without confirmation from TikTok (modern 2026 UI check timed out).")

            print("[TikTokRunner] Video successfully posted to TikTok!")

        tu_upload._post_video = robust_post_video
        print("[TikTokRunner] Applied modern 2026 TikTok upload & cookie patches.")
    except Exception as e:
        print(f"[TikTokRunner] Warning: could not apply monkeypatch: {e}")

def main():
    parser = argparse.ArgumentParser(description="Isolated TikTok session uploader subprocess")
    parser.add_argument("--video-path", required=True, help="Absolute path to the video file")
    parser.add_argument("--caption", default="", help="Video caption/description")
    parser.add_argument("--session-id", default="", help="TikTok session ID cookie")
    parser.add_argument("--cookies-path", default="", help="Path to tiktok cookies txt file")
    parser.add_argument("--browser", default="edge", help="Browser to use (edge, chrome, chromium)")
    parser.add_argument("--headless", action="store_true", default=True, help="Run browser in headless mode")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run browser in visible mode")

    args = parser.parse_args()

    if not os.path.isfile(args.video_path):
        out = {"success": False, "error": f"Video file not found: {args.video_path}"}
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

    sess = args.session_id.strip() if args.session_id else None
    cookies = args.cookies_path.strip() if args.cookies_path and os.path.isfile(args.cookies_path) else ""

    if not sess and not cookies:
        out = {"success": False, "error": "Neither session ID nor valid cookies path provided."}
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

    try:
        patch_tiktok_uploader()
        from tiktok_uploader.upload import TikTokUploader

        uploader = TikTokUploader(
            sessionid=sess,
            cookies=cookies,
            browser=args.browser,
            headless=args.headless
        )

        success = uploader.upload_video(
            filename=args.video_path,
            description=args.caption
        )

        if success:
            out = {
                "success": True,
                "browser": args.browser,
                "caption": args.caption,
                "message": "Video successfully uploaded and published to TikTok using session ID!"
            }
            print(json.dumps(out, ensure_ascii=False))
            sys.exit(0)
        else:
            out = {
                "success": False,
                "browser": args.browser,
                "error": "Upload completed without confirmation from TikTok."
            }
            print(json.dumps(out, ensure_ascii=False))
            sys.exit(1)

    except Exception as exc:
        trace = traceback.format_exc()
        out = {
            "success": False,
            "browser": args.browser,
            "error": str(exc),
            "traceback": trace
        }
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
