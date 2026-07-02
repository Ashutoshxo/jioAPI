import json
import os
import sys
import io
import urllib.parse
import request_helper
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
    print("🚀 Starting JioMart API-based Address Cleanup & Re-Creation...")
    print(f"Target New Address Name: {addr_input.get('fullName')} | Pin: {addr_input.get('pin')}")

    email = cookies.get_active_email()

    # Step 1: Fetch all existing addresses
    print("\nStep 1: Fetching current saved addresses...")
    list_url = "https://www.jiomart.com/api/service/application/cart/v1.0/address?checkout_mode=self"
    
    try:
        response = request_helper.send_authorized_request("GET", list_url, email=email)
        if response.status_code != 200:
            print(f"❌ Failed to fetch address list. Status code: {response.status_code}")
            print(response.text[:1000])
            sys.exit(1)
            
        data = response.json()
        addresses = data.get("address", [])
        print(f"Found {len(addresses)} saved address(es).")
    except Exception as e:
        print(f"❌ Error fetching addresses: {e}")
        sys.exit(1)

    # Step 2: Delete each address
    deleted_count = 0
    if addresses:
        print(f"\nStep 2: Deleting all {len(addresses)} existing addresses...")
        for addr in addresses:
            addr_id = addr.get("id") or addr.get("_id")
            name = addr.get("name", "Unknown")
            print(f"Deleting address card '{name}' (ID: {addr_id})...")
            
            del_url = f"https://www.jiomart.com/api/service/application/cart/v1.0/address/{addr_id}"
            try:
                del_res = request_helper.send_authorized_request("DELETE", del_url, email=email)
                if del_res.status_code == 200:
                    deleted_count += 1
                    print("  Successfully deleted.")
                else:
                    print(f"  ⚠️ Failed to delete. Status code: {del_res.status_code}")
            except Exception as e:
                print(f"  ❌ Error deleting: {e}")
        print(f"Address cleanup complete. Total deleted: {deleted_count}/{len(addresses)} address(es).")
    else:
        print("\nStep 2: No existing addresses to delete.")

    # Step 3: Add new address
    print("\nStep 3: Creating new address from address_input.json...")
    post_url = "https://www.jiomart.com/api/service/application/cart/v1.0/address"
    
    # Extract geolocation details
    if addr_input.get("latitude") and addr_input.get("longitude"):
        geo_val = {
            "latitude": float(addr_input["latitude"]),
            "longitude": float(addr_input["longitude"])
        }
    else:
        saved_cookies = cookies.read_cookies(email)
        geo_val = None
        for c in saved_cookies:
            if c.get("name") == "app_geolocation":
                geo_val = json.loads(urllib.parse.unquote(c.get("value")))
                break
                
        if not geo_val:
            geo_val = {"latitude": 19.1986266122, "longitude": 73.1959010102}

    line1 = str(addr_input["line1"]).strip()
    line2 = str(addr_input["line2"]).strip()
    landmark = str(addr_input.get("landmark", "")).strip()
    full_address = f"{line1}, {line2}"
    payload = {
        "address": full_address,
        "address_type": addr_input["address_type"].lower(),
        "area": line2,
        "area_code": addr_input["pin"],
        "city": addr_input["city"],
        "country": "India",
        "country_code": "91",
        "country_phone_code": "91",
        "country_iso_code": "IN",
        "email": None,
        "is_default_address": True,
        "landmark": landmark,
        "name": addr_input["fullName"].strip(),
        "phone": addr_input["phone"],
        "state": addr_input["state"].upper(),
        "geo_location": {
            "longitude": geo_val.get("longitude", 73.1959010102),
            "latitude": geo_val.get("latitude", 19.1986266122)
        },
        "_custom_json": {
            "flat_or_house_no": line1,
            "floor_no": "",
            "tower_no": "",
            "input_mode": "MAP_POLY",
            "address_line": line2
        }
    }

    try:
        post_res = request_helper.send_authorized_request("POST", post_url, json_data=payload, email=email)
        print(f"Add Address Response Status: {post_res.status_code}")
        
        if post_res.status_code in [200, 201]:
            result_data = post_res.json()
            print("🎉 Address successfully created via API!")
            
            # Save response
            res_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "add_address_response.json")
            with open(res_path, "w", encoding="utf-8") as f:
                json.dump(result_data, f, indent=2)
            print(f"Saved response to: {res_path}")
            
            # Print new address details
            is_success = result_data.get("is_default_address") or result_data.get("success")
            new_id = result_data.get("id") or result_data.get("_id") or result_data.get("address_id")
            print(f"  New Address ID: {new_id}")
        else:
            print(f"❌ Failed to create address. Status code: {post_res.status_code}")
            print(post_res.text)
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error during address creation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
