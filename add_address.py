import json
import os
import sys
import io
from playwright.sync_api import sync_playwright
import cookies

# Force UTF-8 encoding for stdout and stderr to handle emojis on Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

INPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "address_input.json")

def load_address_input():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Error: {INPUT_FILE} not found.")
        sys.exit(1)
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error parsing address_input.json: {e}")
        sys.exit(1)

def main():
    addr_input = load_address_input()
    print("🚀 Starting JioMart UI-based Address Automation...")
    print(f"Adding: {addr_input.get('fullName')} | Pin: {addr_input.get('pin')}")

    email = cookies.get_active_email()
    saved_cookies = cookies.read_cookies(email)
    if not saved_cookies:
        print("❌ Error: No cookies found in a.json. Please run get_cookies.py first.")
        sys.exit(1)

    with sync_playwright() as p:
        # Launch headed or headless browser
        # Running headed is recommended if you want to see the address being filled!
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        )

        # Set cookies
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

        # Step 1: Open Address Listing page directly
        print("Loading JioMart address book page...")
        page.goto("https://www.jiomart.com/customer/addresses", wait_until="load")
        page.wait_for_timeout(3000)

        # Handle location popup if it appears
        try:
            page.locator("button:has-text('Select Location Manually')").click(timeout=3000)
        except Exception:
            pass

        # Step 2: Click 'Add New Address'
        print("Clicking 'Add New Address' button...")
        add_btn_selectors = [
            "text=Add New Address",
            "button:has-text('Add')",
            ".add-address-btn",
            "a:has-text('Add Address')"
        ]
        clicked_add = False
        for sel in add_btn_selectors:
            try:
                elem = page.locator(sel).first
                if elem.is_visible():
                    elem.click()
                    clicked_add = True
                    print("Opened Address input modal/form.")
                    break
            except Exception:
                continue

        if not clicked_add:
            # If not on the customer panel, go to checkout address page
            print("Could not find Add Address on profile. Navigating directly to checkout address process...")
            page.goto("https://www.jiomart.com/checkout/address", wait_until="load")
            page.wait_for_timeout(3000)
            try:
                page.locator("text=Add New Address, .add-address").first.click(timeout=5000)
                clicked_add = True
            except Exception as e:
                print("Could not initiate address addition page:", e)
                browser.close()
                sys.exit(1)

        page.wait_for_timeout(3000)

        # Step 3: Enter Pincode first if prompted
        pincode_input = page.locator("input[placeholder*='Pincode'], input[placeholder*='Pin code'], input[name*='pincode']").first
        if pincode_input.is_visible():
            print("Entering pincode to fetch locality...")
            pincode_input.fill(addr_input.get("pin"))
            page.wait_for_timeout(2000)
            # Click proceed or submit pin
            proceed_btn = page.locator("button:has-text('Proceed'), button:has-text('Continue'), button:has-text('Submit')").first
            if proceed_btn.is_visible():
                proceed_btn.click()
                page.wait_for_timeout(3000)

        # Step 4: Fill the Address Form (matching the Confirm Location screenshot)
        print("Filling out the Address Form fields...")
        try:
            # House no / Flat
            house_input = page.locator("input[placeholder*='House no'], input[placeholder*='Flat']").first
            house_input.fill(addr_input.get("line1").split(",")[0]) # Extract flat number e.g. A-14
            
            # Building / Apartment
            building_input = page.locator("input[placeholder*='Building'], input[placeholder*='Apartment']").first
            # Fill the rest of the street info
            building_input.fill(addr_input.get("line1"))
            
            # Nearby Landmark
            landmark_input = page.locator("input[placeholder*='Landmark']").first
            landmark_input.fill(addr_input.get("landmark", ""))
            
            # Recipient Details (Name and Phone)
            name_input = page.locator("input[placeholder*='Name'], input[name*='name']").first
            if not name_input.is_visible():
                # Fallback: Locate name input by position or label
                # Looking at the screenshot, name is the 4th text field
                name_input = page.locator("input").nth(3)
            name_input.fill(addr_input.get("fullName"))
            
            phone_input = page.locator("input[type='tel'], input[placeholder*='Phone'], input[placeholder*='Mobile']").first
            phone_input.fill(addr_input.get("phone"))

            # Save As (Home / Work / Other)
            addr_type = addr_input.get("address_type", "Home").title()
            type_btn = page.locator(f"button:has-text('{addr_type}'), div:has-text('{addr_type}'), span:has-text('{addr_type}')").first
            if type_btn.is_visible():
                type_btn.click()
                print(f"Address Type selected: {addr_type}")

            # Save Address
            print("Saving Address...")
            save_btn = page.locator("button:has-text('Save Address'), button:has-text('Save'), .save-address-btn").first
            save_btn.click()
            page.wait_for_timeout(4000)
            
            print("✅ Address successfully added via UI automation!")
        except Exception as e:
            print(f"❌ Error filling/submitting address form: {e}")
            page.screenshot(path="address_error.png")
            print("Saved error screenshot to: address_error.png")

        browser.close()

if __name__ == "__main__":
    main()
