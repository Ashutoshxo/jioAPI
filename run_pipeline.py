"""
JioMart Complete Order Pipeline
================================
Flow:
  1. Delete all old addresses
  2. Add new address (from address_input.json)
  3. Clear cart (delete all items)
  4. Add product(s) to cart (from order_input.json)
  5. Verify price of each product within min/max bounds
  6. Place COD order  (skipped if dry_run=True)

Config files:
  - address_input.json  → delivery address details
  - order_input.json    → products list (URL, quantity, price bounds), dry_run flag
"""

import json
import os
import sys
import io
import re
import urllib.parse

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import request_helper
import cookies

DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(DIR, "debug")

def sep(title=""):
    line = "=" * 60
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(line)
    else:
        print(line)

def ok(msg):  print(f"  [OK]  {msg}")
def err(msg): print(f"  [ERR] {msg}"); sys.exit(1)
def info(msg):print(f"        {msg}")

def save_debug_json(name, data):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(os.path.join(DEBUG_DIR, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        info(f"Could not save debug file {name}: {e}")

def cart_item_product_uid(item):
    return (
        item.get("item", {}).get("id")
        or item.get("item", {}).get("uid")
        or item.get("item_id")
        or item.get("product", {}).get("uid")
    )

def cart_item_seller_id(item):
    return (
        item.get("article", {}).get("seller_identifier")
        or item.get("seller_identifier")
        or item.get("product", {}).get("item_code")
    )

def cart_item_name(item):
    return item.get("product", {}).get("name", "?")

def cart_item_price(item):
    return item.get("article", {}).get("price", {}).get("converted", {}).get("effective", 0)

def cart_item_quantity(item):
    return item.get("quantity") or item.get("item", {}).get("quantity") or 0

def find_cart_item(items, product_uid=None, seller_id=None):
    for item in items:
        item_uid = cart_item_product_uid(item)
        item_seller = cart_item_seller_id(item)
        if product_uid and item_uid and str(item_uid) == str(product_uid):
            return item
        if seller_id and item_seller and str(item_seller) == str(seller_id):
            return item
    return None

# ─────────────────────────────────────────────
# Load configs
# ─────────────────────────────────────────────
def load_json(path):
    if not os.path.exists(path):
        err(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────────────────────────
# PHASE 1 — Address
# ─────────────────────────────────────────────
def delete_all_addresses(email):
    res = request_helper.send_authorized_request(
        "GET",
        "https://www.jiomart.com/api/service/application/cart/v1.0/address?checkout_mode=self",
        email=email)
    if res.status_code != 200:
        err(f"Cannot fetch addresses: {res.status_code}")
    addresses = res.json().get("address", [])
    info(f"Found {len(addresses)} existing address(es).")
    for addr in addresses:
        addr_id = addr.get("id") or addr.get("_id")
        name    = addr.get("name", "?")
        d = request_helper.send_authorized_request(
            "DELETE",
            f"https://www.jiomart.com/api/service/application/cart/v1.0/address/{addr_id}",
            email=email)
        if d.status_code == 200:
            info(f"Deleted: {name} ({addr_id})")
        else:
            info(f"Could not delete {name}: {d.status_code}")

def add_address(addr_input, email):
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
                try:
                    geo_val = json.loads(urllib.parse.unquote(c.get("value", "")))
                except Exception:
                    pass
                break
        if not geo_val:
            geo_val = {"latitude": 19.1986266122, "longitude": 73.1959010102}

    line1 = str(addr_input["line1"]).strip()
    line2 = str(addr_input["line2"]).strip()
    landmark = str(addr_input.get("landmark", "")).strip()

    payload = {
        "address":          f"{line1}, {line2}",
        "address_type":     addr_input["address_type"].lower(),
        "area":             line2,
        "area_code":        addr_input["pin"],
        "city":             addr_input["city"],
        "country":          "India",
        "country_code":     "91",
        "country_phone_code": "91",
        "country_iso_code": "IN",
        "email":            None,
        "is_default_address": True,
        "landmark":         landmark,
        "name":             addr_input["fullName"].strip(),
        "phone":            addr_input["phone"],
        "state":            addr_input["state"].upper(),
        "geo_location": {
            "longitude": geo_val.get("longitude", 73.1959010102),
            "latitude":  geo_val.get("latitude",  19.1986266122)
        },
        "_custom_json": {
            "flat_or_house_no": line1,
            "floor_no": "",
            "tower_no": "",
            "input_mode": "MAP_POLY",
            "address_line": line2
        }
    }
    res = request_helper.send_authorized_request(
        "POST",
        "https://www.jiomart.com/api/service/application/cart/v1.0/address",
        json_data=payload, email=email)
    if res.status_code not in [200, 201]:
        err(f"Address add failed: {res.status_code} — {res.text[:200]}")
    data   = res.json()
    new_id = data.get("id") or data.get("_id") or data.get("address_id")
    ok(f"New address created: {addr_input['fullName'].strip()} | Pin {addr_input['pin']} | ID: {new_id}")
    return new_id

# ─────────────────────────────────────────────
# PHASE 2 — Cart
# ─────────────────────────────────────────────
def clear_cart(email):
    res = request_helper.send_authorized_request(
        "GET",
        "https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/get_cart?b=true&i=true",
        email=email)
    if res.status_code != 200:
        err(f"Cannot fetch cart: {res.status_code}")
    data = res.json()
    cart_id = data.get("cart_id") or data.get("id")
    items = data.get("items", [])
    info(f"Found {len(items)} item(s) in cart.")
    
    if not cart_id:
        return
        
    for it in items:
        name = it.get("product", {}).get("name", "?")
        item_id = it.get("product", {}).get("uid")
        size = it.get("article", {}).get("size") or "OS"
        identifier = it.get("identifiers", {}).get("identifier")
        full_article_id = f"{item_id}_{size}"
        
        payload = {
            "item": {
                "article_id": full_article_id,
                "item_id": int(item_id) if item_id else None,
                "identifiers": {
                    "identifier": identifier
                },
                "item_size": size,
                "quantity": 0,
                "parent_item_identifiers": {
                    "identifier": None,
                    "parent_item_size": None,
                    "parent_item_id": None
                },
                "meta": {
                    "vertical_code": "GROCERIES",
                    "compute_delivery_fee": True
                },
                "item_index": 0
            },
            "operation": "update_item"
        }
        
        url = f"https://www.jiomart.com/ext/jmshipmentfee/cart/v2.0/update_cart?id={cart_id}"
        rm = request_helper.send_authorized_request("PUT", url, json_data=payload, email=email)
        
        if rm.status_code == 200:
            info(f"Removed: {name}")
        else:
            info(f"Could not remove {name}: {rm.status_code} — {rm.text[:100]}")


def parse_url(product_url):
    """Returns (slug, uid, seller_id) from a JioMart product URL."""
    url  = product_url.rstrip('/').split('?')[0]
    path = url.split('jiomart.com', 1)[-1]
    if re.match(r'^/p/', path):
        m = re.search(r'/([0-9]+)$', path)
        if m:
            return None, None, m.group(1)
    if '/product/' in path:
        slug = path.split('/product/', 1)[-1]
        m    = re.search(r'-([0-9]+)$', slug)
        uid  = m.group(1) if m else None
        return slug, uid, None
    m = re.search(r'/([0-9]+)$', path)
    if m:
        return None, None, m.group(1)
    return None, None, None

def get_product_details(slug=None, uid=None, seller_id=None, email=None):
    """Returns (product_uid, seller_identifier, size) from catalog."""
    def get_size_value(sizes_list):
        if not sizes_list:
            return "OS"
        sz = sizes_list[0]
        if isinstance(sz, dict):
            return sz.get("value", "OS")
        elif isinstance(sz, str):
            return sz
        return "OS"

    if slug:
        r = request_helper.send_authorized_request(
            "GET",
            f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/{slug}/",
            email=email)
        if r.status_code == 200:
            d     = r.json()
            p_uid = d.get("uid") or uid
            s_id  = d.get("item_code") or seller_id
            sizes = d.get("sizes") or [{"value": "OS"}]
            size  = get_size_value(sizes)
            return p_uid, s_id, size
    if uid:
        r = request_helper.send_authorized_request(
            "GET",
            f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/?item_id={uid}",
            email=email)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                p    = items[0]
                sizes = p.get("sizes") or [{"value": "OS"}]
                return p.get("uid") or uid, p.get("item_code") or seller_id, get_size_value(sizes)
    if seller_id:
        # Try search query 'q' first as it resolves legacy codes correctly
        r = request_helper.send_authorized_request(
            "GET",
            f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/?q={seller_id}",
            email=email)
        if r.status_code == 200:
            items = r.json().get("items", [])
            matched_item = None
            for p in items:
                if str(p.get("item_code")) == str(seller_id) or str(p.get("uid")) == str(seller_id):
                    matched_item = p
                    break
            if not matched_item and len(items) == 1:
                matched_item = items[0]
            if matched_item:
                sizes = matched_item.get("sizes") or [{"value": "OS"}]
                return matched_item.get("uid") or uid, matched_item.get("item_code") or seller_id, get_size_value(sizes)

        # Fallback to item_code
        r = request_helper.send_authorized_request(
            "GET",
            f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/?item_code={seller_id}",
            email=email)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if items:
                p    = items[0]
                sizes = p.get("sizes") or [{"value": "OS"}]
                return p.get("uid") or uid, p.get("item_code") or seller_id, get_size_value(sizes)
    return uid, seller_id, "OS"

def add_item_to_cart(product_uid, seller_id, size, quantity, email):
    """Add item to cart. Returns (cart_id, product_name, price)."""
    url   = "https://www.jiomart.com/api/service/application/cart/v1.0/detail"
    tries = []
    if product_uid and seller_id:
        tries.append({"item_id": int(product_uid), "quantity": quantity, "size": size, "seller_identifier": str(seller_id)})
    if product_uid:
        tries.append({"item_id": int(product_uid), "quantity": quantity, "size": size})
    if seller_id:
        tries.append({"seller_identifier": str(seller_id), "quantity": quantity})

    for attempt_idx, item in enumerate(tries, 1):
        res = request_helper.send_authorized_request("POST", url, json_data={"items": [item]}, email=email)
        debug_payload = {
            "attempt": attempt_idx,
            "request_item": item,
            "status_code": res.status_code,
            "response_text": res.text[:4000],
        }
        if res.status_code == 200:
            d    = res.json()
            debug_payload["response_json"] = d
            save_debug_json(f"cart_add_attempt_{attempt_idx}.json", debug_payload)
            if not d.get("success"):
                info(f"Add attempt returned success=False: {d.get('message', 'Unknown error')}")
                continue
            cart = d.get("cart", {})
            cid  = cart.get("cart_id") or cart.get("id") or cart.get("uid")
            if cid:
                c_items = cart.get("items", [])
                matched_item = None
                for it in c_items:
                    it_product_uid = it.get("item", {}).get("id") or it.get("item_id")
                    it_seller_id = it.get("article", {}).get("seller_identifier") or it.get("seller_identifier")
                    if (product_uid and str(it_product_uid) == str(product_uid)) or (seller_id and str(it_seller_id) == str(seller_id)):
                        matched_item = it
                        break
                
                if not matched_item and c_items:
                    matched_item = c_items[0]

                name  = matched_item.get("product", {}).get("name", "?") if matched_item else "?"
                price = matched_item.get("article", {}).get("price", {}).get("converted", {}).get("effective", 0) if matched_item else 0
                return cid, name, price
        else:
            save_debug_json(f"cart_add_attempt_{attempt_idx}.json", debug_payload)
        info(f"Add attempt failed ({res.status_code}): {res.text[:120]}")
    return None, None, None

def get_cart(email):
    res = request_helper.send_authorized_request(
        "GET", "https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/get_cart?b=true&i=true", email=email)
    if res.status_code != 200:
        save_debug_json("cart_after_add_failed.json", {
            "status_code": res.status_code,
            "response_text": res.text[:4000],
        })
        return None, [], {}
    d = res.json()
    save_debug_json("cart_after_add.json", d)
    return d.get("cart_id") or d.get("id"), d.get("items", []), d

# ─────────────────────────────────────────────
# PHASE 3 — Place Order
# ─────────────────────────────────────────────
def place_cod_order(cart_id, addr_id, email):
    res = request_helper.send_authorized_request(
        "POST",
        "https://www.jiomart.com/api/service/application/cart/v1.0/checkout",
        json_data={
            "billing_address_id":  addr_id,
            "delivery_address_id": addr_id,
            "cart_id":             cart_id,
            "payment_mode":        "COD",
            "payment_identifier":  "cod"
        },
        email=email)
    return res

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    addr_input  = load_json(os.path.join(DIR, "address_input.json"))
    order_input = load_json(os.path.join(DIR, "order_input.json"))

    dry_run = order_input.get("dry_run", True)
    
    # Support multiple products format or single product fallback
    products = order_input.get("products", [])
    if not products:
        products = [{
            "product_url": order_input.get("product_url", ""),
            "quantity": order_input.get("quantity", 1),
            "min_price": order_input.get("min_price", 0.0),
            "max_price": order_input.get("max_price", 999999.0)
        }]

    email = cookies.get_active_email()
    if not cookies.read_cookies(email):
        err("No cookies found. Run get_cookies.py first.")

    sep("JioMart Complete Order Pipeline")
    info(f"Account  : {email}")
    info(f"Dry Run  : {dry_run}")
    info(f"Products to add: {len(products)}")

    # ══════════════════════════════════════════
    # PHASE 1 — Address Setup
    # ══════════════════════════════════════════
    sep("PHASE 1 — Address Setup")

    print("\n[1a] Deleting all old addresses...")
    delete_all_addresses(email)

    print("\n[1b] Adding new address...")
    new_addr_id = add_address(addr_input, email)

    # ══════════════════════════════════════════
    # PHASE 2 — Cart Setup & Product Fetching
    # ══════════════════════════════════════════
    sep("PHASE 2 — Cart Setup")

    print("\n[2a] Clearing existing cart items...")
    clear_cart(email)

    added_products = []
    total_amount = 0.0
    global_cart_id = None

    for idx, prod_info in enumerate(products, 1):
        prod_url = prod_info.get("product_url", "")
        quantity = prod_info.get("quantity", 1)

        print(f"\n--- Item {idx}: {prod_url} (qty={quantity}) ---")
        print("\n[2b] Parsing product URL...")
        slug, uid_from_url, seller_from_url = parse_url(prod_url)
        if not slug and not uid_from_url and not seller_from_url:
            err(f"Cannot parse product ID from URL: {prod_url}")
        info(f"Slug={slug or '—'}, UID={uid_from_url or '—'}, Seller={seller_from_url or '—'}")

        print("\n[2c] Fetching product details from catalog...")
        product_uid, seller_id, size = get_product_details(
            slug=slug, uid=uid_from_url, seller_id=seller_from_url, email=email)
        ok(f"Product UID: {product_uid} | Seller ID: {seller_id} | Size: {size}")

        print(f"\n[2d] Adding product to cart (qty={quantity})...")
        cart_id, prod_name, price = add_item_to_cart(product_uid, seller_id, size, quantity, email)

        print("\n[2e] Verifying product is visible in cart...")
        verified_cart_id, cart_items, _ = get_cart(email)
        if verified_cart_id:
            cart_id = verified_cart_id
        matched = find_cart_item(cart_items, product_uid=product_uid, seller_id=seller_id)
        if not matched:
            cart_names = ", ".join(cart_item_name(it) for it in cart_items) or "empty cart"
            err(
                "Product add API did not leave the expected item in cart. "
                f"Cart now has {len(cart_items)} item(s): {cart_names}. "
                "Check debug/cart_add_attempt_*.json and debug/cart_after_add.json."
            )

        verified_qty = cart_item_quantity(matched)
        prod_name = cart_item_name(matched)
        price = cart_item_price(matched)
        if int(verified_qty or 0) < int(quantity):
            err(
                f"Cart has '{prod_name}' but quantity is {verified_qty}, expected {quantity}. "
                "Check stock/serviceability or debug/cart_after_add.json."
            )

        global_cart_id = cart_id
        ok(f"Cart ID : {cart_id}")
        ok(f"Product : {prod_name}")
        ok(f"Cart Qty: {verified_qty}")
        ok(f"Price   : Rs.{price} x {quantity} = Rs.{round(price * quantity, 2)}")

        # Price verification for this specific item if configured
        min_price = prod_info.get("min_price")
        if min_price is None:
            min_price = order_input.get("min_price")
        if min_price is None:
            min_price = order_input.get("total_min_price")
        if min_price is None:
            min_price = 0.0

        max_price = prod_info.get("max_price")
        if max_price is None:
            max_price = order_input.get("max_price")
        if max_price is None:
            max_price = order_input.get("total_max_price")
        if max_price is None:
            max_price = 999999.0

        has_min = (prod_info.get("min_price") is not None 
                   or order_input.get("min_price") is not None 
                   or order_input.get("total_min_price") is not None)
        has_max = (prod_info.get("max_price") is not None 
                   or order_input.get("max_price") is not None 
                   or order_input.get("total_max_price") is not None)
                   
        if has_min or has_max:
            if price < min_price or price > max_price:
                err(f"Price Rs.{price} is OUTSIDE allowed range Rs.{min_price}–Rs.{max_price} for '{prod_name}'. Aborting.")
            ok(f"Price Rs.{price} is within Rs.{min_price}–Rs.{max_price}. Check PASSED!")

        added_products.append({
            "name": prod_name,
            "price": price,
            "quantity": quantity,
            "total": round(price * quantity, 2)
        })
        total_amount += price * quantity

    # Verify total cart price if configured
    total_min_price = order_input.get("total_min_price")
    total_max_price = order_input.get("total_max_price")
    if total_min_price is not None or total_max_price is not None:
        t_min = total_min_price if total_min_price is not None else 0.0
        t_max = total_max_price if total_max_price is not None else 999999.0
        if total_amount < t_min or total_amount > t_max:
            err(f"Total cart amount Rs.{round(total_amount, 2)} is OUTSIDE allowed range Rs.{t_min}–Rs.{t_max}. Aborting.")
        ok(f"Total cart amount Rs.{round(total_amount, 2)} is within Rs.{t_min}–Rs.{t_max}. Check PASSED!")

    # ══════════════════════════════════════════
    # PHASE 3 — Fetch and Confirm Delivery Address
    # ══════════════════════════════════════════
    sep("PHASE 3 — Address Verification")
    
    addrs  = request_helper.send_authorized_request(
        "GET",
        "https://www.jiomart.com/api/service/application/cart/v1.0/address?checkout_mode=self",
        email=email).json().get("address", [])
    addr   = next((a for a in addrs if a.get("is_default_address")), addrs[0] if addrs else {})
    addr_id = addr.get("id") or addr.get("_id") or new_addr_id
    
    total_amount = round(total_amount, 2)

    # ══════════════════════════════════════════
    # DRY RUN STOP
    # ══════════════════════════════════════════
    if dry_run:
        sep("DRY RUN COMPLETE — Order NOT placed")
        info("What WOULD happen if dry_run=false:")
        for idx, it in enumerate(added_products, 1):
            info(f"  Item {idx} : {it['name']} x {it['quantity']} @ Rs.{it['price']} (Subtotal: Rs.{it['total']})")
        info(f"  Total    : Rs.{total_amount}")
        info(f"  Address  : {addr.get('name','?')}, {addr.get('city','?')} – {addr.get('area_code','?')}")
        info(f"  Payment  : Cash on Delivery (COD)")
        info(f"  Cart ID  : {global_cart_id}")
        print()
        info(">> Set 'dry_run': false in order_input.json to place the real order.")
        sep()
        sys.exit(0)

    # ══════════════════════════════════════════
    # PHASE 4 — Place COD Order
    # ══════════════════════════════════════════
    sep("PHASE 4 — Place COD Order")

    print("\n[4] Placing COD order...")
    res = place_cod_order(global_cart_id, addr_id, email)
    info(f"Checkout status: {res.status_code}")

    if res.status_code != 200:
        err(f"Checkout API failed: {res.text[:400]}")

    data = res.json()
    if not data.get("success"):
        err(f"success=false in response:\n{json.dumps(data, indent=2)[:600]}")

    order_id = (data.get("order_id")
                or data.get("data", {}).get("order_id")
                or data.get("cart", {}).get("order_id"))

    sep("ORDER PLACED SUCCESSFULLY!")
    if order_id:
        info(f"Order ID : {order_id}")
    for idx, it in enumerate(added_products, 1):
        info(f"  Item {idx} : {it['name']} x {it['quantity']} @ Rs.{it['price']} (Subtotal: Rs.{it['total']})")
    info(f"  Amount   : Rs.{total_amount}")
    info(f"  Address  : {addr.get('name','?')}, {addr.get('city','?')} – {addr.get('area_code','?')}")
    info(f"  Payment  : COD")
    sep()

    # Save result
    out = os.path.join(DIR, "last_order.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"order_id": order_id, "products": added_products,
                   "amount": total_amount,
                   "address": f"{addr.get('name','?')}, {addr.get('city','?')}-{addr.get('area_code','?')}",
                   "payment": "COD", "full_response": data}, f, indent=2)
    info(f"Saved to: {out}")

if __name__ == "__main__":
    main()
