import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import cookies


DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BROWSER_CHANNEL = os.environ.get("JIOMART_BROWSER_CHANNEL", "msedge")
BROWSER_ARGS = [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
]


def get_user_data_path(email):
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", email)
    return os.path.join(DIR, "user_data", safe_name)


def launch_context(playwright, user_data_dir, headless=False):
    launch_options = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "no_viewport": True,
        "args": BROWSER_ARGS,
        "ignore_default_args": ["--enable-automation", "--no-sandbox"],
    }

    if DEFAULT_BROWSER_CHANNEL:
        try:
            print(f"Launching browser with channel: {DEFAULT_BROWSER_CHANNEL}")
            return playwright.chromium.launch_persistent_context(
                channel=DEFAULT_BROWSER_CHANNEL,
                **launch_options,
            )
        except Exception as e:
            print(f"Could not launch {DEFAULT_BROWSER_CHANNEL}; falling back to bundled Chromium.")
            print(f"Launch error: {e}")

    print("Launching bundled Playwright Chromium.")
    return playwright.chromium.launch_persistent_context(**launch_options)


def apply_browser_patches(context):
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {
          get: () => undefined
        });
        Object.defineProperty(navigator, 'plugins', {
          get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
          get: () => ['en-US', 'en']
        });
        window.chrome = window.chrome || { runtime: {} };
        """
    )


def save_context_cookies(context, email):
    playwright_cookies = context.cookies()
    cookies.save_cookies(email, playwright_cookies)
    has_session = any(c.get("name") == "R.session" for c in playwright_cookies)
    print(f"\nSaved {len(playwright_cookies)} cookies to a.json for {email}.")
    print(f"R.session present: {'yes' if has_session else 'no'}")
    return playwright_cookies


def close_location_popup(page):
    print("Checking for location popup...")
    try:
        page.wait_for_timeout(3000)
        locators = [
            page.locator("button:has-text('Select Location Manually')"),
            page.locator("div:has-text('Select Location Manually')").last,
            page.locator("button.close"),
            page.locator("[aria-label='Close']"),
            page.locator(".modal-close"),
            page.locator(".jmart-modal-close"),
            page.locator("div.modal-header button"),
        ]
        for loc in locators:
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    print("Location popup closed successfully.")
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"No location popup or failed to close it automatically: {e}")


def is_logged_in(context, page):
    print("Checking if user is already logged in...")
    page.wait_for_timeout(3000)

    playwright_cookies = context.cookies()
    has_session_cookie = any(c.get("name") == "R.session" for c in playwright_cookies)

    try:
        sign_in_link = page.locator("a:has-text('Sign In'), #sign-in, .sign-in").first
        sign_in_visible = sign_in_link.count() > 0 and sign_in_link.is_visible()
    except Exception:
        sign_in_visible = False

    return has_session_cookie and not sign_in_visible


def open_login_if_needed(page):
    print("Opening login drawer if needed...")
    sign_in_locators = [
        page.locator("a:has-text('Sign In')"),
        page.locator("button:has-text('Sign In')"),
        page.locator("#sign-in"),
        page.locator(".sign-in"),
        page.locator("a[href*='login']"),
        page.locator(".login-icon-content"),
        page.locator("a.logged-user-name"),
    ]

    for loc in sign_in_locators:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                print("Sign In drawer/modal opened.")
                return
        except Exception:
            continue

    print("Could not click Sign In automatically. Please click it manually in the browser.")


def main():
    email = cookies.get_active_email()
    print(f"Starting JioMart login process for {email}...")

    user_data_dir = get_user_data_path(email)
    print(f"Using persistent profile directory: {user_data_dir}")

    with sync_playwright() as p:
        context = launch_context(p, user_data_dir, headless=False)
        apply_browser_patches(context)
        page = context.pages[0] if context.pages else context.new_page()

        print("Opening JioMart homepage...")
        try:
            page.goto("https://www.jiomart.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"Initial page load did not finish cleanly: {e}")
            print("If the page is visible in the browser, continue manually and then press ENTER here.")

        page.wait_for_timeout(5000)
        close_location_popup(page)

        if is_logged_in(context, page):
            print("\nDetected active login session in browser profile.")
        else:
            print("\nUser is not logged in. Manual login is needed.")
            open_login_if_needed(page)

            print("\n" + "=" * 60)
            print("INSTRUCTIONS:")
            print("1. If the page is stuck, press Ctrl+R in the opened browser once.")
            print("2. Click Sign In if the login drawer is not open.")
            print("3. Enter mobile number and verify OTP in this browser.")
            print("4. After successful login, come back here and press ENTER.")
            print("=" * 60 + "\n")

            input("Press Enter here AFTER you have successfully logged in in the browser...")

        save_context_cookies(context, email)
        context.close()


if __name__ == "__main__":
    main()
