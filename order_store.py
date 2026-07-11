import json
import os
import sqlite3
from datetime import date, datetime


DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("JIOBOT_DB_FILE") or os.path.join(DIR, "jiobot.sqlite3")


def utc_now():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_key():
    return date.today().isoformat()


def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER NOT NULL UNIQUE,
                telegram_username TEXT,
                first_name TEXT,
                balance_paise INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jiomart_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_key TEXT NOT NULL UNIQUE,
                display_name TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                max_orders_per_day INTEGER NOT NULL DEFAULT 5,
                orders_today INTEGER NOT NULL DEFAULT 0,
                orders_today_date TEXT NOT NULL DEFAULT '',
                last_used_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT 'default',
                full_name TEXT,
                phone TEXT,
                address_type TEXT,
                pin TEXT,
                city TEXT,
                state TEXT,
                line1 TEXT,
                line2 TEXT,
                landmark TEXT,
                latitude REAL,
                longitude REAL,
                is_default INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                account_id INTEGER,
                address_id INTEGER,
                product_url TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                total_amount REAL,
                jiomart_order_id TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
                FOREIGN KEY (account_id) REFERENCES jiomart_accounts(id),
                FOREIGN KEY (address_id) REFERENCES addresses(id)
            );

            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                amount_paise INTEGER NOT NULL,
                utr TEXT,
                screenshot_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                provider TEXT NOT NULL DEFAULT 'manual',
                merchant_order_id TEXT UNIQUE,
                gateway_order_id TEXT,
                payment_url TEXT,
                gateway_payload TEXT,
                admin_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS product_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT 'Default Product Card',
                is_default INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS product_card_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_card_id INTEGER NOT NULL,
                product_url TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (product_card_id) REFERENCES product_cards(id) ON DELETE CASCADE
            );
            """
        )
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN balance_paise INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        for ddl in (
            "ALTER TABLE deposits ADD COLUMN provider TEXT NOT NULL DEFAULT 'manual'",
            "ALTER TABLE deposits ADD COLUMN merchant_order_id TEXT",
            "ALTER TABLE deposits ADD COLUMN gateway_order_id TEXT",
            "ALTER TABLE deposits ADD COLUMN payment_url TEXT",
            "ALTER TABLE deposits ADD COLUMN gateway_payload TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_merchant_order_id
            ON deposits(merchant_order_id)
            WHERE merchant_order_id IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_cards_client_default
            ON product_cards(client_id, is_default, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_card_items_card_sort
            ON product_card_items(product_card_id, sort_order)
            """
        )


def row_to_dict(row):
    return dict(row) if row is not None else None


def upsert_client(chat_id, username=None, first_name=None, default_status="active"):
    init_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO clients (
                telegram_chat_id, telegram_username, first_name, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_chat_id) DO UPDATE SET
                telegram_username = excluded.telegram_username,
                first_name = excluded.first_name,
                updated_at = excluded.updated_at
            """,
            (chat_id, username, first_name, default_status, now, now),
        )
        row = conn.execute(
            "SELECT * FROM clients WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()
        return row_to_dict(row)


def get_client_by_chat_id(chat_id):
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()
        return row_to_dict(row)


def set_client_status(chat_id, status):
    init_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE clients
            SET status = ?, updated_at = ?
            WHERE telegram_chat_id = ?
            """,
            (status, now, int(chat_id)),
        )


def list_pending_clients(limit=20):
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM clients
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def count_pending_deposits():
    init_db()
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM deposits WHERE status = 'pending'"
            ).fetchone()[0]
        )


def get_client_balance(chat_id):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    return int(client.get("balance_paise") or 0)


def add_client_balance(chat_id, amount_paise):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE clients
            SET balance_paise = balance_paise + ?, updated_at = ?
            WHERE id = ?
            """,
            (int(amount_paise), now, client["id"]),
        )


def deduct_client_balance(chat_id, amount_paise):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    amount_paise = int(amount_paise)
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE clients
            SET balance_paise = balance_paise - ?, updated_at = ?
            WHERE id = ? AND balance_paise >= ?
            """,
            (amount_paise, now, client["id"], amount_paise),
        )
        return cur.rowcount == 1


def refund_client_balance(chat_id, amount_paise):
    if amount_paise:
        add_client_balance(chat_id, amount_paise)


def save_address(chat_id, addr, label="default"):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    values = (
        client["id"],
        label,
        addr.get("fullName"),
        addr.get("phone"),
        addr.get("address_type"),
        addr.get("pin"),
        addr.get("city"),
        addr.get("state"),
        addr.get("line1"),
        addr.get("line2"),
        addr.get("landmark"),
        addr.get("latitude"),
        addr.get("longitude"),
        now,
        now,
    )
    with connect() as conn:
        conn.execute(
            "UPDATE addresses SET is_default = 0 WHERE client_id = ?",
            (client["id"],),
        )
        cur = conn.execute(
            """
            INSERT INTO addresses (
                client_id, label, full_name, phone, address_type, pin, city, state,
                line1, line2, landmark, latitude, longitude, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return cur.lastrowid


def list_addresses(chat_id):
    client = get_client_by_chat_id(chat_id)
    if not client:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM addresses
            WHERE client_id = ?
            ORDER BY is_default DESC, updated_at DESC
            """,
            (client["id"],),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def update_address_phone(chat_id, address_id, phone):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE addresses
            SET phone = ?, updated_at = ?
            WHERE id = ? AND client_id = ?
            """,
            (str(phone), now, int(address_id), client["id"]),
        )
        return cur.rowcount == 1


def save_product_card(chat_id, products, label="Default Product Card"):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    clean_products = []
    for index, product in enumerate(products or [], 1):
        product_url = (product.get("product_url") or "").strip()
        if not product_url:
            continue
        clean_products.append(
            {
                "product_url": product_url,
                "quantity": int(product.get("quantity") or 1),
                "sort_order": index,
            }
        )
    if not clean_products:
        return None

    with connect() as conn:
        conn.execute(
            "UPDATE product_cards SET is_default = 0 WHERE client_id = ?",
            (client["id"],),
        )
        cur = conn.execute(
            """
            INSERT INTO product_cards (
                client_id, label, is_default, status, created_at, updated_at
            ) VALUES (?, ?, 1, 'active', ?, ?)
            """,
            (client["id"], label, now, now),
        )
        card_id = cur.lastrowid
        for product in clean_products:
            conn.execute(
                """
                INSERT INTO product_card_items (
                    product_card_id, product_url, quantity, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    product["product_url"],
                    product["quantity"],
                    product["sort_order"],
                    now,
                    now,
                ),
            )
        return card_id


def list_product_cards(chat_id, limit=10):
    client = get_client_by_chat_id(chat_id)
    if not client:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM product_cards
            WHERE client_id = ? AND status = 'active'
            ORDER BY is_default DESC, updated_at DESC
            LIMIT ?
            """,
            (client["id"], int(limit)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def list_product_card_items(product_card_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM product_card_items
            WHERE product_card_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (int(product_card_id),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def latest_product_card(chat_id):
    cards = list_product_cards(chat_id, limit=1)
    if not cards:
        return None
    card = cards[0]
    card["items"] = list_product_card_items(card["id"])
    return card


def sync_accounts_from_keys(account_keys):
    init_db()
    now = utc_now()
    added = 0
    with connect() as conn:
        for key in account_keys:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO jiomart_accounts (
                    account_key, display_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (key, key, now, now),
            )
            added += cur.rowcount
        if account_keys:
            placeholders = ",".join("?" for _ in account_keys)
            conn.execute(
                f"""
                UPDATE jiomart_accounts
                SET status = 'active', updated_at = ?
                WHERE status = 'expired'
                  AND account_key IN ({placeholders})
                """,
                (now, *account_keys),
            )
    return added


def list_accounts():
    init_db()
    reset_daily_counts_if_needed()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jiomart_accounts
            ORDER BY status ASC, orders_today ASC, last_used_at ASC
            """
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def get_account_by_key(account_key):
    init_db()
    reset_daily_counts_if_needed()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM jiomart_accounts WHERE account_key = ?",
            (account_key,),
        ).fetchone()
        return row_to_dict(row)


def reset_daily_counts_if_needed():
    init_db()
    today = today_key()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE jiomart_accounts
            SET orders_today = 0, orders_today_date = ?, updated_at = ?
            WHERE orders_today_date != ?
            """,
            (today, now, today),
        )


def pick_account():
    init_db()
    reset_daily_counts_if_needed()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM jiomart_accounts
            WHERE status = 'active'
              AND orders_today < max_orders_per_day
            ORDER BY orders_today ASC, COALESCE(last_used_at, '') ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        return row_to_dict(row)


def pick_accounts(limit):
    init_db()
    reset_daily_counts_if_needed()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM jiomart_accounts
            WHERE status = 'active'
              AND orders_today < max_orders_per_day
            ORDER BY orders_today ASC, COALESCE(last_used_at, '') ASC, id ASC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def set_account_status(account_key, status):
    init_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE jiomart_accounts
            SET status = ?, updated_at = ?
            WHERE account_key = ?
            """,
            (status, now, account_key),
        )


def create_order(chat_id, address_id, account_id, product_url, quantity, dry_run):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (
                client_id, account_id, address_id, product_url, quantity, dry_run,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                client["id"],
                account_id,
                address_id,
                product_url,
                quantity,
                1 if dry_run else 0,
                now,
                now,
            ),
        )
        return cur.lastrowid


def finish_order(order_id, status, total_amount=None, jiomart_order_id=None, error_message=None):
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE orders
            SET status = ?, total_amount = ?, jiomart_order_id = ?,
                error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, total_amount, jiomart_order_id, error_message, now, order_id),
        )


def list_client_orders(chat_id, limit=10):
    client = get_client_by_chat_id(chat_id)
    if not client:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT o.*, a.account_key
            FROM orders o
            LEFT JOIN jiomart_accounts a ON a.id = o.account_id
            WHERE o.client_id = ?
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (client["id"], int(limit)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def create_deposit(
    chat_id,
    amount_paise,
    utr=None,
    screenshot_file_id=None,
    provider="manual",
    merchant_order_id=None,
):
    client = get_client_by_chat_id(chat_id) or upsert_client(chat_id)
    now = utc_now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO deposits (
                client_id, amount_paise, utr, screenshot_file_id, status, provider,
                merchant_order_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                client["id"],
                int(amount_paise),
                utr,
                screenshot_file_id,
                provider,
                merchant_order_id,
                now,
                now,
            ),
        )
        return cur.lastrowid


def update_deposit_gateway(
    deposit_id,
    gateway_order_id=None,
    payment_url=None,
    payload=None,
    merchant_order_id=None,
):
    now = utc_now()
    payload_text = json.dumps(payload, sort_keys=True) if payload is not None else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE deposits
            SET merchant_order_id = COALESCE(?, merchant_order_id),
                gateway_order_id = COALESCE(?, gateway_order_id),
                payment_url = COALESCE(?, payment_url),
                gateway_payload = COALESCE(?, gateway_payload),
                updated_at = ?
            WHERE id = ?
            """,
            (merchant_order_id, gateway_order_id, payment_url, payload_text, now, int(deposit_id)),
        )


def get_deposit_by_merchant_order_id(merchant_order_id):
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, c.telegram_chat_id, c.telegram_username, c.first_name
            FROM deposits d
            JOIN clients c ON c.id = d.client_id
            WHERE d.merchant_order_id = ?
            """,
            (merchant_order_id,),
        ).fetchone()
        return row_to_dict(row)


def list_client_deposits(chat_id, limit=5):
    client = get_client_by_chat_id(chat_id)
    if not client:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM deposits
            WHERE client_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (client["id"], int(limit)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]


def get_deposit(deposit_id):
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, c.telegram_chat_id, c.telegram_username, c.first_name
            FROM deposits d
            JOIN clients c ON c.id = d.client_id
            WHERE d.id = ?
            """,
            (int(deposit_id),),
        ).fetchone()
        return row_to_dict(row)


def get_latest_pending_deposit():
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT d.*, c.telegram_chat_id, c.telegram_username, c.first_name
            FROM deposits d
            JOIN clients c ON c.id = d.client_id
            WHERE d.status = 'pending'
            ORDER BY d.id DESC
            LIMIT 1
            """
        ).fetchone()
        return row_to_dict(row)


def approve_deposit(deposit_id, admin_note=None):
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending":
        return None
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE deposits
            SET status = 'approved', admin_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (admin_note, now, int(deposit_id)),
        )
        conn.execute(
            """
            UPDATE clients
            SET balance_paise = balance_paise + ?, updated_at = ?
            WHERE id = ?
            """,
            (int(deposit["amount_paise"]), now, deposit["client_id"]),
        )
    return deposit


def complete_gateway_deposit(deposit_id, provider, payload=None, admin_note=None):
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending" or deposit.get("provider") != provider:
        return None
    now = utc_now()
    payload_text = json.dumps(payload, sort_keys=True) if payload is not None else deposit.get("gateway_payload")
    with connect() as conn:
        conn.execute(
            """
            UPDATE deposits
            SET status = 'approved',
                admin_note = ?,
                gateway_payload = COALESCE(?, gateway_payload),
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (admin_note, payload_text, now, int(deposit_id)),
        )
        if conn.total_changes < 1:
            return None
        conn.execute(
            """
            UPDATE clients
            SET balance_paise = balance_paise + ?, updated_at = ?
            WHERE id = ?
            """,
            (int(deposit["amount_paise"]), now, deposit["client_id"]),
        )
    return get_deposit(deposit_id)


def fail_gateway_deposit(deposit_id, provider, payload=None, admin_note=None):
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending" or deposit.get("provider") != provider:
        return None
    now = utc_now()
    payload_text = json.dumps(payload, sort_keys=True) if payload is not None else deposit.get("gateway_payload")
    with connect() as conn:
        conn.execute(
            """
            UPDATE deposits
            SET status = 'rejected',
                admin_note = ?,
                gateway_payload = COALESCE(?, gateway_payload),
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (admin_note, payload_text, now, int(deposit_id)),
        )
    return get_deposit(deposit_id)


def reject_deposit(deposit_id, admin_note=None):
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending":
        return None
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE deposits
            SET status = 'rejected', admin_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (admin_note, now, int(deposit_id)),
        )
    return deposit


def mark_account_used(account_id, increment_order_count):
    if not account_id:
        return
    reset_daily_counts_if_needed()
    now = utc_now()
    today = today_key()
    delta = 1 if increment_order_count else 0
    with connect() as conn:
        conn.execute(
            """
            UPDATE jiomart_accounts
            SET orders_today = orders_today + ?,
                orders_today_date = ?,
                last_used_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (delta, today, now, now, account_id),
        )


def address_to_pipeline_json(address_row):
    return {
        "fullName": address_row.get("full_name") or "Your Name",
        "phone": address_row.get("phone") or "9876543210",
        "address_type": address_row.get("address_type") or "Home",
        "pin": address_row.get("pin"),
        "city": address_row.get("city"),
        "state": address_row.get("state"),
        "line1": address_row.get("line1"),
        "line2": address_row.get("line2"),
        "landmark": address_row.get("landmark") or "",
        "latitude": address_row.get("latitude"),
        "longitude": address_row.get("longitude"),
    }


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
