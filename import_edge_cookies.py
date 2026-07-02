import argparse
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import cookies


CHROME_EPOCH_DELTA_SECONDS = 11644473600
TARGET_DOMAIN_PARTS = (
    "jiomart.com",
    "relianceretail.com",
    "jiomartjcp.com",
)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def dpapi_decrypt(encrypted):
    encrypted_buffer = ctypes.create_string_buffer(encrypted, len(encrypted))
    in_blob = DATA_BLOB(len(encrypted), encrypted_buffer)
    out_blob = DATA_BLOB()

    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def get_edge_user_data_dir():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is not set.")
    return Path(local_app_data) / "Microsoft" / "Edge" / "User Data"


def load_master_key(edge_user_data_dir):
    local_state_path = edge_user_data_dir / "Local State"
    with open(local_state_path, "r", encoding="utf-8") as f:
        local_state = json.load(f)

    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    encrypted_key = base64.b64decode(encrypted_key_b64)
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]
    return dpapi_decrypt(encrypted_key)


def chrome_time_to_unix(expires_utc):
    if not expires_utc:
        return -1
    return int((expires_utc / 1_000_000) - CHROME_EPOCH_DELTA_SECONDS)


def decrypt_cookie_value(encrypted_value, plain_value, master_key):
    if plain_value:
        return plain_value

    if not encrypted_value:
        return ""

    if encrypted_value.startswith((b"v10", b"v11")):
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:-16]
        tag = encrypted_value[-16:]
        return AESGCM(master_key).decrypt(nonce, ciphertext + tag, None).decode("utf-8")

    if encrypted_value.startswith(b"v20"):
        raise RuntimeError(
            "Cookie uses Chrome/Edge app-bound encryption (v20). "
            "Export it from the same browser session instead."
        )

    return dpapi_decrypt(encrypted_value).decode("utf-8")


def copy_cookie_db(profile_dir):
    source = profile_dir / "Network" / "Cookies"
    if not source.exists():
        raise FileNotFoundError(f"Cookies DB not found: {source}")

    temp_dir = Path(tempfile.mkdtemp(prefix="edge-cookies-", dir=os.getcwd()))
    target = temp_dir / "Cookies"
    try:
        shutil.copy2(source, target)
    except PermissionError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Edge Cookies DB is locked; reading it directly in read-only mode.")
        return None, source

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            try:
                shutil.copy2(sidecar, Path(str(target) + suffix))
            except PermissionError:
                pass

    return temp_dir, target


def sqlite_readonly_uri(path):
    return path.resolve().as_uri() + "?mode=ro"


def read_target_cookies(cookie_db, master_key):
    imported = []
    skipped = []

    conn = sqlite3.connect(sqlite_readonly_uri(cookie_db), uri=True)
    try:
        rows = conn.execute(
            """
            SELECT host_key, name, value, encrypted_value, path, expires_utc,
                   is_secure, is_httponly, samesite
            FROM cookies
            WHERE host_key LIKE '%jiomart.com'
               OR host_key LIKE '%relianceretail.com'
               OR host_key LIKE '%jiomartjcp.com'
            """
        ).fetchall()
    finally:
        conn.close()

    for host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite in rows:
        if not any(part in host_key for part in TARGET_DOMAIN_PARTS):
            continue

        try:
            decrypted_value = decrypt_cookie_value(encrypted_value, value, master_key)
        except Exception as e:
            skipped.append((host_key, name, str(e)))
            continue

        if not decrypted_value:
            continue

        imported.append(
            {
                "name": name,
                "value": decrypted_value,
                "domain": host_key,
                "path": path or "/",
                "expires": chrome_time_to_unix(expires_utc),
                "secure": bool(is_secure),
                "httpOnly": bool(is_httponly),
                "sameSite": samesite,
            }
        )

    return imported, skipped


def main():
    parser = argparse.ArgumentParser(description="Import JioMart cookies from normal Microsoft Edge.")
    parser.add_argument("--profile", default="Default", help="Edge profile folder name, e.g. Default/Profile 1")
    parser.add_argument("--email", default=None, help="a.json profile key; defaults to cookies.py active email")
    args = parser.parse_args()

    email = args.email or cookies.get_active_email()
    edge_user_data_dir = get_edge_user_data_dir()
    profile_dir = edge_user_data_dir / args.profile

    master_key = load_master_key(edge_user_data_dir)
    temp_dir, cookie_db = copy_cookie_db(profile_dir)

    try:
        imported, skipped = read_target_cookies(cookie_db, master_key)
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if not imported:
        print(f"No target cookies imported from Edge profile: {profile_dir}")
        if skipped:
            print(f"Skipped encrypted cookies: {len(skipped)}")
            for host, name, reason in skipped[:10]:
                print(f"  {host} | {name} | {reason}")
        sys.exit(1)

    cookies.save_cookies(email, imported)

    names = sorted({cookie["name"] for cookie in imported})
    has_session = any(cookie["name"] == "R.session" for cookie in imported)
    session_cookie = next((cookie for cookie in imported if cookie["name"] == "R.session"), None)

    print(f"Imported {len(imported)} JioMart/Reliance cookies from Edge profile: {args.profile}")
    print(f"Saved into a.json profile: {email}")
    print(f"Cookie names: {', '.join(names)}")
    print(f"R.session present: {'yes' if has_session else 'no'}")
    if session_cookie:
        expires = session_cookie.get("expires")
        print(f"R.session domain: {session_cookie.get('domain')}")
        print(f"R.session path: {session_cookie.get('path')}")
        print(f"R.session expires: {expires if expires and expires > 0 else 'session/server-controlled'}")
    if skipped:
        print(f"Skipped {len(skipped)} cookies that could not be decrypted.")


if __name__ == "__main__":
    main()
