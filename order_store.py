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
            """
        )


def row_to_dict(row):
    return dict(row) if row is not None else None


def upsert_client(chat_id, username=None, first_name=None):
    init_db()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO clients (
                telegram_chat_id, telegram_username, first_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_chat_id) DO UPDATE SET
                telegram_username = excluded.telegram_username,
                first_name = excluded.first_name,
                updated_at = excluded.updated_at
            """,
            (chat_id, username, first_name, now, now),
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
