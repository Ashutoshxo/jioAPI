import json
import os
import sys
import io
from playwright.sync_api import sync_playwright
import cookies

# Force UTF-8 encoding for stdout and stderr to handle emojis on Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def main():
    email = cookies.get_active_email()
    print(f"🚀 Starting Address Cleanup for {email}...")

    saved_cookies = cookies.read_cookies(email)
    if not saved_cookies:
        print("❌ Error: No cookies found in a.json.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
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

        # Load cart page to establish fully authorized FDK context
        print("Loading JioMart cart page to establish FDK session...")
        page.goto("https://www.jiomart.com/cart/bag", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        # Execute address fetch and deletion loop inside browser page context
        print("Fetching and deleting all addresses inside browser context...")
        result = page.evaluate("""
            async () => {
                let log = [];
                // 1. Fetch current addresses
                let listRes = await fetch('/api/service/application/cart/v1.0/address?checkout_mode=self', {
                    method: 'GET',
                    headers: { 'x-platform': 'web' }
                });
                
                if (listRes.status !== 200) {
                    return { success: false, status: listRes.status, log: ["Failed to fetch address list"] };
                }
                
                let listData = await listRes.json();
                let addresses = listData.address || [];
                log.push("Found " + addresses.length + " addresses to delete.");
                
                // 2. Loop and delete each address
                let deletedCount = 0;
                for (let addr of addresses) {
                    let addrId = addr.id || addr._id;
                    try {
                        let delRes = await fetch('/api/service/application/cart/v1.0/address/' + addrId, {
                            method: 'DELETE',
                            headers: {
                                'x-platform': 'web',
                                'x-location-detail': JSON.stringify({
                                    country: "INDIA",
                                    country_iso_code: "IN",
                                    city: addr.city || "Mumbai",
                                    pincode: addr.area_code || "400001",
                                    state: addr.state || "Maharashtra"
                                })
                            }
                        });
                        if (delRes.status === 200) {
                            deletedCount++;
                            log.push("Deleted address ID: " + addrId);
                        } else {
                            log.push("Failed to delete address ID " + addrId + " (Status: " + delRes.status + ")");
                        }
                    } catch(e) {
                        log.push("Error deleting " + addrId + ": " + e.message);
                    }
                }
                
                return { success: true, deleted: deletedCount, log: log };
            }
        """)

        print(f"\nExecution success: {result['success']}")
        print("Logs:")
        for line in result.get("log", []):
            print(f"  - {line}")
            
        print(f"\nCleanup complete. Total deleted: {result.get('deleted', 0)}")
        browser.close()

if __name__ == "__main__":
    main()
