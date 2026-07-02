import json
import os
import sys
import io

# Force UTF-8 encoding for stdout and stderr to handle emojis on Windows console
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import request_helper
import cookies

def main():
    email = cookies.get_active_email()
    print(f"🚀 Fetching JioMart cart via direct API for {email}...")

    # Define the cart URL that returns full item lists and breakup values
    url = "https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/get_cart?b=true&i=true"
    
    try:
        response = request_helper.send_authorized_request("GET", url, email=email)
        print(f"API Response Status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ Error: Received status code {response.status_code} from API.")
            print(response.text[:1000])
            sys.exit(1)
            
        data = response.json()
        print("✨ Successfully fetched cart details!")
        
        # Save to file
        safe_email = email.replace("@", "_").replace(".", "_")
        target_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"cart_response_{safe_email}.json")
        default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cart_response.json")
        
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        with open(default_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            
        print(f"✅ Cart JSON saved to:\n  - {target_path}\n  - {default_path}")
        
        # Print a short summary of items
        items = data.get("items", [])
        print(f"\nFound {len(items)} item(s) in cart:")
        for idx, item in enumerate(items):
            prod = item.get("product", {})
            name = prod.get("name", "Unknown Item")
            qty = item.get("quantity", 0)
            
            # Retrieve price from article details
            price = "N/A"
            article = item.get("article", {})
            if article:
                price_info = article.get("price", {}).get("converted", {})
                if price_info:
                    price = price_info.get("effective", "N/A")
            print(f"  {idx + 1}. {name} | Qty: {qty} | Price: ₹{price}")
            
        # Breakup values preview
        breakup = data.get("breakup_values", {})
        raw_val = breakup.get("raw", {})
        print(f"\nBreakup Summary:")
        print(f"  MRP Total: ₹{raw_val.get('mrp_total')}")
        print(f"  Discount:  ₹{raw_val.get('discount')}")
        print(f"  Subtotal:  ₹{raw_val.get('subtotal')}")
        print(f"  Total Pay: ₹{raw_val.get('total')}")

    except Exception as e:
        print(f"❌ Error fetching cart: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
