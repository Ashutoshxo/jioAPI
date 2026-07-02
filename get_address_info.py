import json
import os
import sys
import io
import time
from playwright.sync_api import sync_playwright
import cookies

# Force UTF-8 encoding for stdout and stderr to handle emojis on Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def main():
    email = cookies.get_active_email()
    print(f"Fetching saved addresses for {email} via browser interception...")

    # Read cookies from a.json
    saved_cookies = cookies.read_cookies(email)
    if not saved_cookies:
        print("❌ Error: No cookies found. Please run get_cookies.py first.")
        sys.exit(1)

    address_data = None

    with sync_playwright() as p:
        # Launch headless browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )
        
        # Set saved cookies in the browser context
        playwright_cookies = []
        for c in saved_cookies:
            playwright_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".jiomart.com"),
                "path": c.get("path", "/")
            })
            
        context.add_cookies(playwright_cookies)
        page = context.new_page()

        # Listen to response events to intercept the address API response
        def handle_response(response):
            nonlocal address_data
            url = response.url
            if "/address" in url and "checkout_mode" in url:
                try:
                    content_type = response.headers.get("content-type", "")
                    if "application/json" in content_type:
                        address_data = response.json()
                        print("✨ Successfully intercepted address API response!")
                except Exception:
                    pass

        page.on("response", handle_response)

        # Navigate to the JioMart cart page which triggers the address fetch
        print("Loading JioMart cart/bag page...")
        page.goto("https://www.jiomart.com/cart/bag", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        if address_data:
            target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "address_response.json")
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(address_data, f, indent=2)
            print(f"\n✅ Address details successfully saved to: {target_path}")
            
            # Print address summary
            if "address" in address_data:
                addresses = address_data["address"]
                print(f"\nFound {len(addresses)} address(es):")
                for index, addr in enumerate(addresses):
                    print(f"\nAddress {index + 1}:")
                    print(f"  ID (uid): {addr.get('id') or addr.get('_id')}")
                    print(f"  Name: {addr.get('name')}")
                    print(f"  Phone: {addr.get('phone')}")
                    print(f"  Pincode: {addr.get('area_code')}")
                    print(f"  Address Type: {addr.get('address_type')}")
                    print(f"  Full Address: {addr.get('address')}, {addr.get('landmark')}, {addr.get('city')}, {addr.get('state')}")
            else:
                print("\nRaw response preview:")
                print(json.dumps(address_data, indent=2)[:500])
        else:
            print("❌ Error: Could not intercept the address API response. Check if session has expired.")

        browser.close()

if __name__ == "__main__":
    main()
