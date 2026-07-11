import json
import os
import sys
import io
import re

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import request_helper
import cookies

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_input.json")

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_url(product_url):
    """
    Detect URL format and return (slug, product_uid, seller_id).

    Format A: /p/category/name/490001992
      -> seller_id = 490001992  (last numeric path segment)

    Format B: /product/pears-pure-gentle-soap-...-7527773
      -> slug = full slug after /product/
      -> product_uid = 7527773  (last number in slug)
    """
    url   = product_url.rstrip('/').split('?')[0]
    path  = url.split('jiomart.com', 1)[-1]

    # Format A: /p/...
    if re.match(r'^/p/', path):
        m = re.search(r'/([0-9]+)$', path)
        if m:
            return None, None, m.group(1)   # (slug, uid, seller_id)

    # Format B: /product/...
    if '/product/' in path:
        slug = path.split('/product/', 1)[-1]
        m = re.search(r'-([0-9]+)$', slug)
        uid = m.group(1) if m else None
        return slug, uid, None

    # Fallback
    m = re.search(r'/([0-9]+)$', path)
    if m:
        return None, None, m.group(1)
    return None, None, None


def get_product_details(slug=None, product_uid=None, seller_id=None, email=None):
    """
    Fetch product uid, seller_identifier and size from catalog.
    Returns (product_uid, seller_identifier, size).
    """
    if slug:
        url = f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/{slug}/"
        res = request_helper.send_authorized_request("GET", url, email=email)
        if res.status_code == 200:
            d = res.json()
            uid    = d.get("uid") or product_uid
            s_id   = d.get("item_code") or seller_id
            sizes  = d.get("sizes") or [{"value": "OS"}]
            size   = sizes[0].get("value", "OS") if sizes else "OS"
            return uid, s_id, size

    if product_uid:
        url = f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/?item_id={product_uid}"
        res = request_helper.send_authorized_request("GET", url, email=email)
        if res.status_code == 200:
            items = res.json().get("items", [])
            if items:
                p    = items[0]
                uid  = p.get("uid") or product_uid
                s_id = p.get("item_code") or seller_id
                sizes = p.get("sizes") or [{"value": "OS"}]
                size  = sizes[0].get("value", "OS") if sizes else "OS"
                return uid, s_id, size

    if seller_id:
        url = f"https://www.jiomart.com/api/service/application/catalog/v1.0/products/?item_code={seller_id}"
        res = request_helper.send_authorized_request("GET", url, email=email)
        if res.status_code == 200:
            items = res.json().get("items", [])
            if items:
                p    = items[0]
                uid  = p.get("uid") or product_uid
                s_id = p.get("item_code") or seller_id
                sizes = p.get("sizes") or [{"value": "OS"}]
                size  = sizes[0].get("value", "OS") if sizes else "OS"
                return uid, s_id, size

    return product_uid, seller_id, "OS"


def get_cart(email):
    """Returns (cart_id, items, raw_data)."""
    res = request_helper.send_authorized_request(
        "GET", "https://www.jiomart.com/ext/jmshipmentfee/cart/v1.0/get_cart?b=true&i=true",
        email=email)
    if res.status_code != 200:
        return None, [], {}
    d = res.json()
    return d.get("cart_id") or d.get("id"), d.get("items", []), d


def add_to_cart(product_uid, seller_id, size, quantity, email):
    """Tries multiple add-to-cart payloads. Returns new cart_id or None."""
    url = "https://www.jiomart.com/api/service/application/cart/v1.0/detail"
    attempts = []
    cart_item_meta = {
        "vertical_code": "GROCERIES",
        "compute_delivery_fee": True,
    }
    if product_uid and seller_id:
        attempts.append({
            "item_id": int(product_uid),
            "quantity": quantity,
            "size": size,
            "seller_identifier": str(seller_id),
            "meta": cart_item_meta,
        })
    if product_uid:
        attempts.append({
            "item_id": int(product_uid),
            "quantity": quantity,
            "size": size,
            "meta": cart_item_meta,
        })
    if seller_id:
        attempts.append({
            "seller_identifier": str(seller_id),
            "quantity": quantity,
            "meta": cart_item_meta,
        })

    for item in attempts:
        res = request_helper.send_authorized_request("POST", url,
                json_data={"items": [item], "meta": cart_item_meta}, email=email)
        if res.status_code == 200:
            d    = res.json()
            if not d.get("success"):
                print(f"    Add to cart API returned success=False: {d.get('message', 'Unknown error')}")
                continue
            cart = d.get("cart", {})
            cid  = cart.get("cart_id") or cart.get("id") or cart.get("uid")
            if cid:
                return cid
        print(f"    Attempt failed ({res.status_code}): {res.text[:120]}")
    return None


def get_addresses(email):
    """Returns list of saved addresses."""
    res = request_helper.send_authorized_request(
        "GET", "https://www.jiomart.com/api/service/application/cart/v1.0/address?checkout_mode=self",
        email=email)
    if res.status_code == 200:
        return res.json().get("address", [])
    return []


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    config    = load_config()
    dry_run   = config.get("dry_run", True)

    products = config.get("products", [])
    if products and not config.get("product_url"):
        first_prod = products[0]
        prod_url = first_prod.get("product_url", "")
        target_qty = first_prod.get("quantity", 1)
        
        min_price = first_prod.get("min_price")
        if min_price is None:
            min_price = config.get("min_price")
        if min_price is None:
            min_price = config.get("total_min_price")
        if min_price is None:
            min_price = 0.0
            
        max_price = first_prod.get("max_price")
        if max_price is None:
            max_price = config.get("max_price")
        if max_price is None:
            max_price = config.get("total_max_price")
        if max_price is None:
            max_price = 999999.0
    else:
        prod_url  = config.get("product_url", "")
        target_qty = config.get("quantity", 1)
        
        min_price = config.get("min_price")
        if min_price is None:
            min_price = config.get("total_min_price")
        if min_price is None:
            min_price = 0.0
            
        max_price = config.get("max_price")
        if max_price is None:
            max_price = config.get("total_max_price")
        if max_price is None:
            max_price = 999999.0

    email = cookies.get_active_email()
    if not cookies.read_cookies(email):
        print("Error: No cookies found. Run get_cookies.py first.")
        sys.exit(1)

    print("=" * 60)
    print("JioMart Order Pipeline")
    print(f"Account  : {email}")
    print(f"Dry Run  : {dry_run}")
    print(f"Price    : Rs.{min_price} – Rs.{max_price}")
    print(f"URL      : {prod_url}")
    print("=" * 60)

    # ── Step 1: Parse URL ──────────────────────────────────────
    print("\n[Step 1] Parsing product URL...")
    slug, uid_from_url, seller_from_url = parse_url(prod_url)
    if not slug and not uid_from_url and not seller_from_url:
        print("Error: Cannot parse product ID from URL.")
        sys.exit(1)
    print(f"  Slug     : {slug or '—'}")
    print(f"  UID      : {uid_from_url or '—'}")
    print(f"  Seller ID: {seller_from_url or '—'}")

    # ── Step 2: Get product details from catalog ───────────────
    print("\n[Step 2] Fetching product details from catalog...")
    product_uid, seller_id, size = get_product_details(
        slug=slug, product_uid=uid_from_url, seller_id=seller_from_url, email=email)
    print(f"  Product UID      : {product_uid}")
    print(f"  Seller Identifier: {seller_id}")
    print(f"  Size             : {size}")

    if not product_uid and not seller_id:
        print("Error: Could not resolve product details from catalog.")
        sys.exit(1)

    # ── Step 3: Check / Add to cart ───────────────────────────
    print("\n[Step 3] Checking cart...")
    cart_id, items, _ = get_cart(email)

    # Check if this product is already in cart
    in_cart = any(
        str(it.get("article", {}).get("seller_identifier", "")) == str(seller_id or "") or
        str(it.get("product", {}).get("uid", "")) == str(product_uid or "")
        for it in items
    )

    if in_cart:
        print(f"  Product already in cart (cart_id: {cart_id})")
    else:
        print(f"  Adding product to cart (qty={target_qty})...")
        new_id = add_to_cart(product_uid, seller_id, size, target_qty, email)
        if not new_id:
            print("Error: All add-to-cart attempts failed.")
            sys.exit(1)
        cart_id = new_id
        # Refresh
        cart_id, items, _ = get_cart(email)

    if not items:
        print("Error: Cart is empty.")
        sys.exit(1)
    print(f"  Cart ID: {cart_id}  ({len(items)} item(s))")

    # ── Step 4: Verify price ──────────────────────────────────
    print("\n[Step 4] Verifying price...")
    # Find our item
    target = next(
        (it for it in items
         if str(it.get("article", {}).get("seller_identifier", "")) == str(seller_id or "") or
            str(it.get("product", {}).get("uid", "")) == str(product_uid or "")),
        items[0])

    prod_name = target.get("product", {}).get("name", "Unknown")
    price     = target.get("article", {}).get("price", {}).get("converted", {}).get("effective", 0.0)
    qty       = target.get("quantity", 1)
    total     = round(price * qty, 2)

    print(f"  Product : {prod_name}")
    print(f"  Price   : Rs.{price} x {qty} = Rs.{total}")

    # Item price verification if configured
    has_item_constraints = (
        (products and ("min_price" in products[0] or "max_price" in products[0]))
        or "min_price" in config
        or "max_price" in config
        or "total_min_price" in config
        or "total_max_price" in config
    )
    if has_item_constraints:
        if price < min_price or price > max_price:
            print(f"\nError: Rs.{price} is outside allowed range Rs.{min_price}–Rs.{max_price}. ABORT.")
            sys.exit(1)
        print("  Price check PASSED!")

    # Verify total cart price if configured
    total_min_price = config.get("total_min_price")
    total_max_price = config.get("total_max_price")
    if total_min_price is not None or total_max_price is not None:
        t_min = total_min_price if total_min_price is not None else 0.0
        t_max = total_max_price if total_max_price is not None else 999999.0
        if total < t_min or total > t_max:
            print(f"\nError: Total cart amount Rs.{total} is outside allowed range Rs.{t_min}–Rs.{t_max}. ABORT.")
            sys.exit(1)
        print(f"  Total cart price Rs.{total} is within Rs.{t_min}–Rs.{t_max}. Check PASSED!")

    # ── Step 5: Get delivery address ──────────────────────────
    print("\n[Step 5] Fetching delivery address...")
    addresses = get_addresses(email)
    if not addresses:
        print("Error: No saved addresses. Run cleanup_and_add_address.py first.")
        sys.exit(1)

    addr  = next((a for a in addresses if a.get("is_default_address")), addresses[0])
    a_id  = addr.get("id") or addr.get("_id")
    a_name = addr.get("name", "?")
    a_city = addr.get("city", "?")
    a_pin  = addr.get("area_code", "?")
    print(f"  {a_name} | {a_city} – {a_pin}")
    print(f"  ID: {a_id}")

    # ── Step 6: DRY RUN STOP ─────────────────────────────────
    if dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN MODE — Order NOT placed.")
        print("Would place:")
        print(f"  Product : {prod_name}")
        print(f"  Amount  : Rs.{total}")
        print(f"  Address : {a_name}, {a_city} – {a_pin}")
        print(f"  Payment : Cash on Delivery (COD)")
        print(f"  Cart ID : {cart_id}")
        print("\n  Set 'dry_run': false in order_input.json to place the real order.")
        print("=" * 60)
        sys.exit(0)

    # ── Step 7: PLACE ORDER ───────────────────────────────────
    print("\n[Step 6] Placing COD order...")
    res = request_helper.send_authorized_request(
        "POST",
        "https://www.jiomart.com/api/service/application/cart/v1.0/checkout",
        json_data={
            "billing_address_id":  a_id,
            "delivery_address_id": a_id,
            "cart_id":             cart_id,
            "payment_mode":        "COD",
            "payment_identifier":  "cod"
        },
        email=email)

    print(f"  Status: {res.status_code}")

    if res.status_code != 200:
        print("Error: Checkout API failed.")
        print(res.text[:600])
        sys.exit(1)

    data = res.json()
    if not data.get("success"):
        print("Error: success=false in checkout response.")
        print(json.dumps(data, indent=2)[:800])
        sys.exit(1)

    order_id = (data.get("order_id")
                or data.get("data", {}).get("order_id")
                or data.get("cart", {}).get("order_id"))

    print("\n" + "=" * 60)
    print("ORDER PLACED SUCCESSFULLY!")
    if order_id:
        print(f"  Order ID : {order_id}")
    print(f"  Product  : {prod_name}")
    print(f"  Amount   : Rs.{total}")
    print(f"  Address  : {a_name}, {a_city} – {a_pin}")
    print(f"  Payment  : COD")
    print("=" * 60)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_order.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"order_id": order_id, "product": prod_name, "price": price,
                   "qty": qty, "amount": total,
                   "address": f"{a_name}, {a_city}-{a_pin}",
                   "payment": "COD", "full_response": data}, f, indent=2)
    print(f"  Saved  : {out}")


if __name__ == "__main__":
    main()
