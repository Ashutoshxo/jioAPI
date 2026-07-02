import json
import os
import sys

COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "a.json")
JIOMART_SESSION_COOKIE_NAMES = {"R.session"}
JIOMART_LOCATION_COOKIE_NAMES = {"app_location_details", "app_geolocation"}


def _cookie_key(cookie):
    return (
        cookie.get("name"),
        cookie.get("domain", ""),
        cookie.get("path", "/"),
    )


def _is_jiomart_cookie(cookie):
    domain = str(cookie.get("domain", ""))
    return "jiomart.com" in domain


def _normalized_cookie_variants(cookie):
    if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
        return []

    variants = [dict(cookie)]
    name = cookie.get("name")
    if _is_jiomart_cookie(cookie) and (
        name in JIOMART_SESSION_COOKIE_NAMES or name in JIOMART_LOCATION_COOKIE_NAMES
    ):
        normalized = dict(cookie)
        normalized["domain"] = ".jiomart.com"
        normalized["path"] = "/"
        variants.append(normalized)

    deduped = {}
    for variant in variants:
        deduped[_cookie_key(variant)] = variant
    return list(deduped.values())


def normalize_cookies(cookie_list):
    cookie_map = {}
    for cookie in cookie_list or []:
        for variant in _normalized_cookie_variants(cookie):
            cookie_map[_cookie_key(variant)] = variant
    return list(cookie_map.values())

def get_active_email(override_email=None):
    if override_email:
        return override_email

    env_email = os.environ.get("JIOMART_ACCOUNT")
    if env_email:
        return env_email
    
    # Try command line arguments --email
    if "--email" in sys.argv:
        try:
            idx = sys.argv.index("--email")
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
        except ValueError:
            pass
            
    # Try credentials.json
    creds_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    if os.path.exists(creds_file):
        try:
            with open(creds_file, "r", encoding="utf-8") as f:
                creds = json.load(f)
                if "email" in creds and creds["email"] != "your_email@example.com":
                    return creds["email"]
        except Exception:
            pass
            
    return "default"

def load_all_cookies():
    if not os.path.exists(COOKIES_FILE):
        return {}
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            data = f.read().strip()
            if not data:
                return {}
            json_data = json.loads(data)
            if isinstance(json_data, list):
                active_email = get_active_email()
                return {active_email: json_data}
            return json_data
    except Exception as e:
        print(f"Error parsing a.json: {e}", file=sys.stderr)
        return {}

def read_cookies(email=None):
    if not email:
        email = get_active_email()
    all_cookies = load_all_cookies()
    return all_cookies.get(email, [])

def save_cookies(email, cookies):
    if not email:
        email = get_active_email()
    all_cookies = load_all_cookies()
    all_cookies[email] = normalize_cookies(cookies)
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(all_cookies, f, indent=2)
    except Exception as e:
        print(f"Error saving cookies to file: {e}", file=sys.stderr)

def get_cookie_string(email=None):
    if not email:
        email = get_active_email()
    cookies = read_cookies(email)
    return "; ".join([f"{c['name']}={c['value']}" for c in cookies if 'name' in c and 'value' in c])

def apply_cookies_to_session(session, email=None):
    if not email:
        email = get_active_email()
    for c in normalize_cookies(read_cookies(email)):
        if 'name' in c and 'value' in c:
            session.cookies.set(
                c['name'],
                c['value'],
                domain=c.get('domain', '.jiomart.com'),
                path=c.get('path', '/')
            )

def save_cookies_from_session(session, email=None):
    if not email:
        email = get_active_email()
    
    # Get current cookies as list of dicts
    updated_cookies = []
    for cookie in session.cookies:
        updated_cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path
        })
    
    # Merge with existing cookies to prevent losing other keys
    existing = normalize_cookies(read_cookies(email))
    cookie_map = {_cookie_key(c): c for c in existing if 'name' in c}
    
    for c in normalize_cookies(updated_cookies):
        cookie_map[_cookie_key(c)] = c
        
    save_cookies(email, list(cookie_map.values()))
