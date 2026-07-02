import requests
import cookies
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
import json
import os

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
SECRET = "TpKw7wD9hH"
APP_ID = "685945f46c8c7aee3f3af605"

def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()

def hmac_sha256_hex(key: str, data: str) -> str:
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()

def generate_signature(method, path, headers, body="", secret=SECRET):
    parsed_url = urllib.parse.urlparse(path)
    path_str = parsed_url.path or "/"
    
    query_str = ""
    if parsed_url.query:
        query_params = urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True)
        sorted_params = sorted(query_params, key=lambda x: x[0])
        query_str = "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in sorted_params)

    sanitized_headers = {}
    for k, v in headers.items():
        k_lower = k.lower()
        if k_lower in ["x-fp-signature", "x-fp-signature-version"]:
            continue
        v_str = str(v).strip()
        v_str = " ".join(v_str.split()) # normalize spaces
        sanitized_headers[k_lower] = v_str

    sorted_header_keys = sorted(sanitized_headers.keys())
    canonical_headers = "\n".join(f"{k}:{sanitized_headers[k]}" for k in sorted_header_keys)
    signed_headers = ";".join(sorted_header_keys)

    body_hash = sha256_hex(body or "")

    canonical_request = "\n".join([
        method.upper(),
        path_str,
        query_str,
        canonical_headers + "\n",
        signed_headers,
        body_hash
    ])

    fp_date = headers.get("x-fp-date") or headers.get("X-Fp-Date")
    if not fp_date:
        raise ValueError("x-fp-date is required in headers")

    string_to_sign = f"{fp_date}\n{sha256_hex(canonical_request)}"
    sig = hmac_sha256_hex(secret, string_to_sign)
    return f"v1.1:{sig}"

def refresh_cookies_via_playwright(email):
    print(f"\n🔄 [Auto-Refresh] Cookies expired (401). Launching headless browser to refresh session for {email}...")
    try:
        from playwright.sync_api import sync_playwright
        import get_cookies
        
        user_data_dir = get_cookies.get_user_data_path(email)
        
        with sync_playwright() as p:
            context = get_cookies.launch_context(p, user_data_dir, headless=True)
            get_cookies.apply_browser_patches(context)
            
            page = context.pages[0] if context.pages else context.new_page()
            
            # Navigate to homepage and wait for load
            page.goto("https://www.jiomart.com/", wait_until="domcontentloaded", timeout=60000)
            
            # Wait for 5 seconds to let JavaScript load and refresh session
            page.wait_for_timeout(5000)
            
            # Extract and save cookies
            playwright_cookies = context.cookies()
            has_session = any(c["name"] == "R.session" for c in playwright_cookies)
            if not has_session:
                print("❌ [Auto-Refresh] No active session found in browser profile. Cannot refresh session headlessly.")
                context.close()
                return False

            cookies.save_cookies(email, playwright_cookies)
            print(f"✅ [Auto-Refresh] Successfully refreshed and saved {len(playwright_cookies)} cookies.")
            context.close()
            return True
    except Exception as e:
        print(f"❌ [Auto-Refresh] Failed to refresh cookies via Playwright: {e}")
        return False

def send_authorized_request(method, url, headers=None, data=None, json_data=None, email=None, retry_count=0):
    if not email:
        email = cookies.get_active_email()
        
    session = requests.Session()
    cookies.apply_cookies_to_session(session, email)
    
    # Load target address pin if available to synchronize session location
    addr_pin = None
    addr_city = None
    addr_state = None
    addr_latitude = None
    addr_longitude = None
    addr_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "address_input.json")
    if os.path.exists(addr_file):
        try:
            with open(addr_file, "r", encoding="utf-8") as f:
                addr_data = json.load(f)
                addr_pin = addr_data.get("pin")
                addr_city = addr_data.get("city")
                addr_state = addr_data.get("state")
                addr_latitude = addr_data.get("latitude")
                addr_longitude = addr_data.get("longitude")
        except Exception:
            pass

    # Extract location details from cookies
    saved_cookies = cookies.read_cookies(email)
    location_val = None
    geo_val = None
    for c in saved_cookies:
        if c.get("name") == "app_location_details":
            location_val = urllib.parse.unquote(c.get("value"))
        elif c.get("name") == "app_geolocation":
            geo_val = urllib.parse.unquote(c.get("value"))

    # Synchronize cookies location with the target address pincode if mismatch found
    cookies_updated = False
    if location_val:
        try:
            loc_json = json.loads(location_val)
            if addr_pin and (
                loc_json.get("pincode") != addr_pin
                or (addr_city and loc_json.get("city") != addr_city.upper())
                or (addr_state and loc_json.get("state") != addr_state.upper())
            ):
                print(f"      [Location Sync] Updating session location to {addr_pin}...")
                loc_json["pincode"] = addr_pin
                if addr_city:
                    loc_json["city"] = addr_city.upper()
                if addr_state:
                    loc_json["state"] = addr_state.upper()
                location_val = json.dumps(loc_json)
                
                # Update in cookies
                for c in saved_cookies:
                    if c.get("name") == "app_location_details":
                        c["value"] = urllib.parse.quote(location_val)
                        cookies_updated = True
        except Exception:
            pass
    else:
        if addr_pin:
            location_val = json.dumps({
                "country": "INDIA",
                "country_iso_code": "IN",
                "city": (addr_city or "Ambernath").upper(),
                "pincode": addr_pin,
                "state": (addr_state or "MAHARASHTRA").upper()
            })
            # Add to cookies
            saved_cookies.append({
                "name": "app_location_details",
                "value": urllib.parse.quote(location_val),
                "domain": ".jiomart.com",
                "path": "/"
            })
            cookies_updated = True
        else:
            location_val = '{"country":"INDIA","country_iso_code":"IN","city":"Ambernath","pincode":"421501","state":"MAHARASHTRA"}'

    # Check / update geolocation. Keep this aligned with address_input.json, not stale browser cookies.
    if addr_latitude and addr_longitude:
        target_geo = json.dumps({
            "latitude": str(addr_latitude),
            "longitude": str(addr_longitude),
            "polygon_ids": []
        })
        if geo_val != target_geo:
            geo_val = target_geo
            geo_found = False
            for c in saved_cookies:
                if c.get("name") == "app_geolocation":
                    c["value"] = urllib.parse.quote(geo_val)
                    geo_found = True
                    cookies_updated = True
            if not geo_found:
                saved_cookies.append({
                    "name": "app_geolocation",
                    "value": urllib.parse.quote(geo_val),
                    "domain": ".jiomart.com",
                    "path": "/"
                })
                cookies_updated = True
    elif addr_pin == "421501":
        target_geo = '{"latitude":"19.1986266122","longitude":"73.1959010102","polygon_ids":["TMZ9_QC_916f3a77"]}'
        if geo_val != target_geo:
            geo_val = target_geo
            geo_found = False
            for c in saved_cookies:
                if c.get("name") == "app_geolocation":
                    c["value"] = urllib.parse.quote(geo_val)
                    geo_found = True
                    cookies_updated = True
            if not geo_found:
                saved_cookies.append({
                    "name": "app_geolocation",
                    "value": urllib.parse.quote(geo_val),
                    "domain": ".jiomart.com",
                    "path": "/"
                })
                cookies_updated = True
    elif not geo_val:
        geo_val = '{"latitude":"19.1986266122","longitude":"73.1959010102","polygon_ids":["TMZ9_QC_916f3a77"]}'

    if cookies_updated:
        cookies.save_cookies(email, saved_cookies)
 
    # Get relative path (including query string)
    parsed_url = urllib.parse.urlparse(url)
    relative_path = parsed_url.path
    if parsed_url.query:
        relative_path += f"?{parsed_url.query}"

    # Create timestamp
    now_utc = datetime.now(timezone.utc)
    x_fp_date = now_utc.strftime("%Y%m%dT%H%M%SZ")

    # Formulate base request headers
    req_headers = {
        "authorization": f"Bearer Njg1OTQ1ZjQ2YzhjN2FlZTNmM2FmNjA1OlRwS3c3d0Q5aA==",
        "x-fp-sdk-version": "1.10.3-60",
        "accept": "application/json, text/plain, */*",
        "x-currency-code": "INR",
        "x-geolocation": geo_val,
        "x-location-detail": location_val,
        "x-fp-date": x_fp_date,
        "user-agent": USER_AGENT,
        "x-platform": "web"
    }

    body_str = ""
    if json_data is not None:
        req_headers["content-type"] = "application/json"
        body_str = json.dumps(json_data, separators=(',', ':'))
    elif data is not None:
        body_str = str(data)

    if headers:
        req_headers.update(headers)

    # Generate signature
    signature = generate_signature(method, relative_path, req_headers, body=body_str)
    req_headers["x-fp-signature"] = signature

    try:
        response = session.request(
            method=method.upper(),
            url=url,
            headers=req_headers,
            data=body_str if json_data is not None else data,
            allow_redirects=True
        )
        cookies.save_cookies_from_session(session, email)
        
        if response.status_code == 401 and retry_count == 0:
            refreshed = refresh_cookies_via_playwright(email)
            if refreshed:
                # Retry the request with the refreshed cookies
                return send_authorized_request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    json_data=json_data,
                    email=email,
                    retry_count=1
                )
        return response
    except Exception as e:
        print(f"HTTP Request failed for {url}: {e}")
        raise e
