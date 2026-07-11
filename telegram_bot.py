import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse

import requests
import telebot
from telebot import types

import order_store


telebot.logger.setLevel(logging.CRITICAL)
from phonepe_gateway import PhonePeError, PhonePeGateway


DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(DIR, "telegram_config.json")
ADDRESS_FILE = os.path.join(DIR, "address_input.json")
ORDER_FILE = os.path.join(DIR, "order_input.json")
RUNTIME_DIR = os.path.join(DIR, "runtime", "orders")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "bot_token": "YOUR_TELEGRAM_BOT_TOKEN_HERE",
            "admin_chat_ids": []
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)
        print(f"Created {CONFIG_FILE}. Please add your real bot token from BotFather.")
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading bot token: {e}")
        return {}


CONFIG = load_config()
BOT_TOKEN = CONFIG.get("bot_token")
if not BOT_TOKEN:
    print("\n[ERROR] Telegram Bot Token is missing in telegram_config.json.")
    print("Please update telegram_config.json with your actual Bot Token from @BotFather.")
    sys.exit(1)


bot = telebot.TeleBot(BOT_TOKEN)
order_store.init_db()
ADMIN_CHAT_IDS = {int(x) for x in CONFIG.get("admin_chat_ids", []) if str(x).isdigit()}
ORDER_CHARGE_PAISE = int(float(CONFIG.get("order_charge_rupees", 5)) * 100)
UPI_ID = CONFIG.get("upi_id", "")
UPI_NAME = CONFIG.get("upi_name", "JioMart Order Bot")
REQUIRE_USER_APPROVAL = bool(CONFIG.get("require_user_approval", True))
SUPPORT_CONTACT = CONFIG.get("support_contact", "")
BOT_USERNAME = CONFIG.get("bot_username", "")
PHONEPE = PhonePeGateway(CONFIG.get("phonepe", {}))


STATE_IDLE = 0
STATE_WAITING_PIN = 1
STATE_WAITING_LANDMARK = 2
STATE_WAITING_HOUSE_NO = 3
STATE_WAITING_LOCATION = 4
STATE_WAITING_CITY_STATE = 5
STATE_WAITING_PRODUCT_URL = 6
STATE_WAITING_PRODUCT_QTY = 7
STATE_PRODUCT_MORE = 8
STATE_CONFIRMATION = 9
STATE_WAITING_DEPOSIT_AMOUNT = 10
STATE_WAITING_DEPOSIT_PROOF = 11
STATE_WAITING_ACCOUNT_COUNT = 12
STATE_ADDRESS_CHOICE = 13
STATE_PRODUCT_CARD_CHOICE = 14
STATE_WAITING_COUPON_CODE = 15
STATE_WAITING_PHONE = 16

BUTTON_LOCATION = "Share Current Location"
BUTTON_DRY_RUN = "Run Pipeline (Dry Run)"
BUTTON_REAL_ORDER = "Run Pipeline (Real Order)"
BUTTON_CANCEL = "Cancel"
BUTTON_ADD_MORE = "Add More"
BUTTON_DONE = "Done"
BUTTON_USE_SAVED_ADDRESS = "Use Saved Address"
BUTTON_EDIT_ADDRESS = "Edit Address"
BUTTON_CHANGE_PHONE = "Change Phone"
BUTTON_EDIT_PRODUCTS = "Edit Products"
BUTTON_USE_SAVED_PRODUCTS = "Use Saved Products"
BUTTON_NEW_PRODUCTS = "New Products"
BUTTON_SKIP_COUPON = "Skip Coupon"
BUTTON_NEW_ORDER = "🛒 New Order"
BUTTON_MY_ORDERS = "📋 My Orders"
BUTTON_MY_BALANCE = "💰 My Balance"
BUTTON_DEPOSIT = "💳 Deposit"
BUTTON_SETTINGS = "⚙️ Settings"
BUTTON_HELP = "ℹ️ Help"
REAL_CONFIRM_TEXT = "CONFIRM ORDER"
MENU_BUTTONS = {
    BUTTON_NEW_ORDER,
    BUTTON_MY_ORDERS,
    BUTTON_MY_BALANCE,
    BUTTON_DEPOSIT,
    BUTTON_SETTINGS,
    BUTTON_HELP,
}
BOT_COMMANDS = [
    ("start", "Show JioMart bot menu"),
    ("order", "Start a new JioMart order"),
    ("balance", "Check wallet balance"),
    ("deposit", "Add wallet balance"),
    ("check_payment", "Check latest deposit payment"),
    ("orders", "Show recent orders"),
    ("help", "Show help"),
    ("settings", "Show account settings"),
]
ADMIN_COMMANDS = [
    ("accounts", "List JioMart account pool status"),
    ("sync_accounts", "Sync accounts from a.json"),
    ("pending_users", "List users waiting for approval"),
    ("approve_user", "Approve a user"),
    ("reject_user", "Reject a user"),
    ("approve_deposit", "Approve a deposit"),
    ("reject_deposit", "Reject a deposit"),
]

user_states = {}
chat_accounts = {}


def is_admin(chat_id):
    return int(chat_id) in ADMIN_CHAT_IDS


def remember_client(message):
    user = message.from_user
    default_status = "active" if is_admin(message.chat.id) or not REQUIRE_USER_APPROVAL else "pending"
    return order_store.upsert_client(
        message.chat.id,
        username=getattr(user, "username", None),
        first_name=getattr(user, "first_name", None),
        default_status=default_status,
    )


def user_label(client):
    username = client.get("telegram_username")
    first_name = client.get("first_name")
    if username:
        return f"@{username}"
    return first_name or str(client.get("telegram_chat_id"))


def is_client_approved(chat_id):
    if is_admin(chat_id):
        return True
    client = order_store.get_client_by_chat_id(chat_id)
    return bool(client and client.get("status") == "active")


def ensure_approved(message):
    client = remember_client(message)
    if is_client_approved(message.chat.id):
        return True
    if client.get("status") == "rejected":
        bot.reply_to(
            message,
            "Aapka account request rejected hai. Support se contact kijiye.",
            reply_markup=main_menu_markup(),
        )
        return False
    bot.reply_to(
        message,
        "Aapka account abhi approval pending hai. Admin approve karega, phir order/deposit use kar payenge.",
        reply_markup=main_menu_markup(),
    )
    return False


def notify_admin_new_pending_user(client):
    if not REQUIRE_USER_APPROVAL or client.get("status") != "pending":
        return
    text = (
        "New user approval pending\n"
        f"User: {user_label(client)}\n"
        f"Chat ID: {client['telegram_chat_id']}\n\n"
        f"Approve: /approve_user {client['telegram_chat_id']}\n"
        f"Reject: /reject_user {client['telegram_chat_id']}"
    )
    for admin_id in ADMIN_CHAT_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception:
            pass


def bot_invite_link():
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME.lstrip('@')}"
    return "BotFather se bot username copy karke share karein."


def format_rupees(paise):
    return f"₹{int(paise or 0) / 100:.2f}"


def order_charge_per_account(chat_id):
    return 0 if is_admin(chat_id) else ORDER_CHARGE_PAISE


def main_menu_markup():
    return types.ReplyKeyboardRemove()


def configure_bot_menu():
    scopes = [
        types.BotCommandScopeDefault(),
        types.BotCommandScopeAllPrivateChats(),
        types.BotCommandScopeAllGroupChats(),
        types.BotCommandScopeAllChatAdministrators(),
    ]

    for scope in scopes:
        try:
            bot.delete_my_commands(scope=scope)
        except Exception as exc:
            print(f"Could not clear Telegram commands for {type(scope).__name__}: {exc}")

    for admin_id in ADMIN_CHAT_IDS:
        try:
            bot.delete_my_commands(scope=types.BotCommandScopeChat(admin_id))
        except Exception as exc:
            print(f"Could not clear admin Telegram commands for {admin_id}: {exc}")

    try:
        bot.set_chat_menu_button(menu_button=types.MenuButtonDefault())
    except Exception as exc:
        print(f"Could not set Telegram menu button: {exc}")


def send_main_menu(chat_id):
    client = order_store.get_client_by_chat_id(chat_id)
    status_line = ""
    if client and client.get("status") != "active":
        status_line = f"\nStatus: {client.get('status')}\n"
    bot.send_message(
        chat_id,
        "👋 Welcome to JioMart Order Bot\n\n"
        "Place orders on multiple JioMart accounts automatically.\n\n"
        f"{status_line}"
        "How it works:\n"
        "🛒 /order → Send product link(s) + quantity\n"
        "📍 Enter pincode, landmark, location\n"
        "✅ Confirm per account → order placed!\n\n"
        f"Charge: {format_rupees(ORDER_CHARGE_PAISE)} per account per order run",
        reply_markup=main_menu_markup(),
    )


def start_order_flow(message):
    if not ensure_approved(message):
        return
    chat_id = message.chat.id
    user_states[chat_id] = {"state_val": STATE_IDLE, "products": []}
    address_row = latest_address_row(chat_id)
    if address_row:
        ask_saved_address_choice(chat_id, address_row)
        return
    ask_pincode(chat_id, "New Order\n\nSabse pehle apna 6-digit pincode enter kijiye:")
    return
    user_states[chat_id] = {"state_val": STATE_WAITING_PIN, "products": []}
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    bot.reply_to(
        message,
        "🛒 New Order\n\nSabse pehle apna 6-digit pincode enter kijiye:",
        reply_markup=markup,
    )


def build_upi_uri(amount_rupees):
    if not UPI_ID:
        return ""
    params = {
        "pa": UPI_ID,
        "pn": UPI_NAME,
        "am": f"{amount_rupees:.2f}",
        "cu": "INR",
        "tn": "JioMart Bot Deposit",
    }
    return "upi://pay?" + urllib.parse.urlencode(params)


def build_qr_url(data):
    return (
        "https://api.qrserver.com/v1/create-qr-code/?size=320x320&data="
        + urllib.parse.quote(data, safe="")
    )


def build_phonepe_merchant_order_id(deposit_id):
    return f"JIODEP-{int(deposit_id)}"


def payment_check_markup(deposit_id, payment_url=None):
    markup = types.InlineKeyboardMarkup()
    if payment_url:
        markup.add(types.InlineKeyboardButton("Open PhonePe Payment", url=payment_url))
    markup.add(types.InlineKeyboardButton("Check Payment", callback_data=f"payment_check_{deposit_id}"))
    return markup


def handle_gateway_payment_status(chat_id, deposit_id, notify_pending=True):
    dep = order_store.get_deposit(deposit_id)
    if not dep or dep.get("provider") != "phonepe":
        bot.send_message(chat_id, "PhonePe deposit nahi mila.")
        return
    if dep.get("status") == "approved":
        bot.send_message(
            chat_id,
            f"Payment already approved hai.\nBalance: {format_rupees(order_store.get_client_balance(dep['telegram_chat_id']))}",
            reply_markup=main_menu_markup(),
        )
        return
    if dep.get("status") != "pending":
        bot.send_message(chat_id, f"Deposit #{dep['id']} status: {dep.get('status')}", reply_markup=main_menu_markup())
        return

    try:
        status = PHONEPE.order_status(dep["merchant_order_id"], details=False)
    except PhonePeError as exc:
        bot.send_message(chat_id, f"PhonePe status check fail hua: {exc}")
        return

    state = (status.get("state") or "").upper()
    if state == "COMPLETED":
        paid_amount = int(status.get("amount") or 0)
        if paid_amount != int(dep["amount_paise"]):
            bot.send_message(
                chat_id,
                "PhonePe payment amount mismatch hai. Balance auto add nahi hua. Admin ko check karna hoga.",
            )
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    bot.send_message(
                        admin_id,
                        f"PhonePe amount mismatch for deposit #{dep['id']}\n"
                        f"Expected: {format_rupees(dep['amount_paise'])}\n"
                        f"PhonePe: {format_rupees(paid_amount)}",
                    )
                except Exception:
                    pass
            return
        updated = order_store.complete_gateway_deposit(
            dep["id"],
            "phonepe",
            payload=status,
            admin_note="auto approved by PhonePe status",
        )
        if not updated:
            bot.send_message(chat_id, "Payment complete hai, lekin deposit already process ho chuka hai.")
            return
        bot.send_message(
            dep["telegram_chat_id"],
            f"Payment success. Deposit approved: {format_rupees(dep['amount_paise'])}\n"
            f"New balance: {format_rupees(order_store.get_client_balance(dep['telegram_chat_id']))}",
            reply_markup=main_menu_markup(),
        )
        if chat_id != dep["telegram_chat_id"]:
            bot.send_message(chat_id, f"Deposit #{dep['id']} auto approved.")
        return

    if state in {"FAILED", "CANCELLED", "EXPIRED"}:
        order_store.fail_gateway_deposit(
            dep["id"],
            "phonepe",
            payload=status,
            admin_note=f"PhonePe state {state}",
        )
        bot.send_message(
            dep["telegram_chat_id"],
            f"Payment {state.lower()} ho gaya. Deposit add nahi hua.",
            reply_markup=main_menu_markup(),
        )
        if chat_id != dep["telegram_chat_id"]:
            bot.send_message(chat_id, f"Deposit #{dep['id']} {state.lower()} marked.")
        return

    if notify_pending:
        bot.send_message(
            chat_id,
            f"Payment abhi pending hai. Thodi der baad Check Payment dabaiye.\nDeposit: #{dep['id']}",
            reply_markup=payment_check_markup(dep["id"], dep.get("payment_url")),
        )


def cancel_markup():
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    return markup


def flow_cancelled(message):
    text = (message.text or "").strip()
    if text not in {BUTTON_CANCEL, "Cancel", "cancel", "/cancel"}:
        return False
    set_state(message.chat.id, STATE_IDLE)
    bot.reply_to(message, "Flow cancel kar diya gaya.", reply_markup=main_menu_markup())
    return True


def is_active_flow(chat_id):
    return current_state(chat_id) not in {None, STATE_IDLE}


def reverse_geocode(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        headers = {"User-Agent": "JioMartTelegramBot/1.0"}
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            address = data.get("address", {})
            road = (
                address.get("road")
                or address.get("suburb")
                or address.get("neighbourhood")
                or ""
            )
            city = (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("county")
                or address.get("state_district")
                or ""
            )
            state = address.get("state") or ""
            return road, city, state
    except Exception as e:
        print(f"Error in reverse geocoding: {e}")
    return "", "", ""


def load_existing_address():
    if os.path.exists(ADDRESS_FILE):
        try:
            with open(ADDRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "fullName": "Your Name",
        "phone": "9876543210",
        "address_type": "Home",
    }


def save_address(addr):
    with open(ADDRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(addr, f, indent=2)


def load_existing_order():
    if os.path.exists(ORDER_FILE):
        try:
            with open(ORDER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "dry_run": True,
        "total_min_price": 0.0,
        "total_max_price": 999999.0,
        "products": [],
    }


def load_cookie_account_keys():
    cookie_file = os.path.join(DIR, "a.json")
    if not os.path.exists(cookie_file):
        return []
    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return sorted(data.keys())
        if isinstance(data, list):
            return ["default"]
    except Exception:
        pass
    return []


def selected_account(chat_id):
    return chat_accounts.get(chat_id) or os.environ.get("JIOMART_ACCOUNT") or "default"


def latest_address_row(chat_id):
    addresses = order_store.list_addresses(chat_id)
    return addresses[0] if addresses else None


def format_address_card(address_row):
    return (
        "Saved Address Card\n\n"
        f"Name: {address_row.get('full_name') or 'Not set'}\n"
        f"Phone: {address_row.get('phone') or 'Not set'}\n"
        f"Pincode: {address_row.get('pin') or 'Not set'}\n"
        f"City: {address_row.get('city') or 'Not set'}\n"
        f"State: {address_row.get('state') or 'Not set'}\n"
        f"House: {address_row.get('line1') or 'Not set'}\n"
        f"Landmark/Street: {address_row.get('line2') or 'Not set'}\n"
        f"GPS: {address_row.get('latitude')}, {address_row.get('longitude')}\n\n"
        "Is address ko use karna hai ya edit?"
    )


def ask_saved_address_choice(chat_id, address_row):
    session = user_states.setdefault(chat_id, {})
    products = session.get("products", [])
    user_states[chat_id] = {
        "state_val": STATE_ADDRESS_CHOICE,
        "products": products,
        "address_id": address_row.get("id"),
    }
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_USE_SAVED_ADDRESS))
    markup.add(types.KeyboardButton(BUTTON_CHANGE_PHONE), types.KeyboardButton(BUTTON_EDIT_ADDRESS))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    bot.send_message(chat_id, format_address_card(address_row), reply_markup=markup)


def ask_pincode(chat_id, prompt=None):
    session = user_states.setdefault(chat_id, {})
    products = session.get("products", [])
    user_states[chat_id] = {"state_val": STATE_WAITING_PIN, "products": products}
    bot.send_message(
        chat_id,
        prompt or "Sabse pehle apna 6-digit pincode enter kijiye:",
        reply_markup=cancel_markup(),
    )


def latest_product_card(chat_id):
    return order_store.latest_product_card(chat_id)


def product_card_products(card):
    products = []
    for item in card.get("items") or []:
        products.append(
            {
                "product_url": item.get("product_url"),
                "quantity": int(item.get("quantity") or 1),
            }
        )
    return products


def format_product_card(card):
    products = product_card_products(card)
    product_lines = format_products_summary(products) if products else "No products saved."
    return (
        "Saved Product Card\n\n"
        f"Name: {card.get('label') or 'Default Product Card'}\n"
        f"{product_lines}\n\n"
        "Is product card ko use karna hai ya edit/new?"
    )


def ask_product_card_or_url(chat_id):
    card = latest_product_card(chat_id)
    if not card:
        ask_product_url(chat_id)
        return

    session = user_states.setdefault(chat_id, {})
    session["state_val"] = STATE_PRODUCT_CARD_CHOICE
    session["product_card_id"] = card.get("id")
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_USE_SAVED_PRODUCTS))
    markup.add(types.KeyboardButton(BUTTON_EDIT_PRODUCTS), types.KeyboardButton(BUTTON_NEW_PRODUCTS))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    bot.send_message(chat_id, format_product_card(card), reply_markup=markup)


def save_order(data):
    with open(ORDER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_product_to_order(product_url, quantity):
    return build_order_json(
        [{"product_url": product_url, "quantity": quantity}],
        dry_run=True,
    )


def build_order_json(products, dry_run, coupon_code=""):
    data = {
        "dry_run": dry_run,
        "total_min_price": 0.0,
        "total_max_price": 999999.0,
        "products": products,
    }
    if coupon_code:
        data["coupon_code"] = coupon_code
    return data


def set_dry_run(value):
    data = load_existing_order()
    data["dry_run"] = value
    save_order(data)


def write_pipeline_files(chat_id, order_id, address_data, order_data):
    run_dir = os.path.join(RUNTIME_DIR, str(chat_id), str(order_id))
    address_path = os.path.join(run_dir, "address_input.json")
    order_path = os.path.join(run_dir, "order_input.json")
    order_store.write_json(address_path, address_data)
    order_store.write_json(order_path, order_data)
    return address_path, order_path


def current_state(chat_id):
    return user_states.get(chat_id, {}).get("state_val")


def set_state(chat_id, state, **extra):
    session = user_states.setdefault(chat_id, {})
    session["state_val"] = state
    session.update(extra)


def build_address_from_session(chat_id, city, state):
    session = user_states[chat_id]
    road = session.get("road", "")
    road_part = f", {road}" if road else ""
    line2 = f"{session['landmark']}{road_part}"

    addr = load_existing_address()
    addr.update(
        {
            "pin": session["pincode"],
            "line1": session["house_no"],
            "line2": line2,
            "city": city,
            "state": state,
            "landmark": session["landmark"],
            "latitude": session["latitude"],
            "longitude": session["longitude"],
        }
    )
    save_address(addr)
    session["address_id"] = order_store.save_address(chat_id, addr)
    session["final_address"] = addr
    return addr


def send_address_summary(chat_id, addr):
    bot.send_message(
        chat_id,
        "Address Summary:\n"
        f"Name: {addr.get('fullName')}\n"
        f"Phone: {addr.get('phone')}\n"
        f"Pincode: {addr.get('pin')}\n"
        f"City: {addr.get('city')}\n"
        f"State: {addr.get('state')}\n"
        f"House: {addr.get('line1')}\n"
        f"Landmark/Street: {addr.get('line2')}\n"
        f"GPS Coords: {addr.get('latitude')}, {addr.get('longitude')}\n\n"
        "Address details save ho gayi hain.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


def ask_product_url(chat_id):
    set_state(chat_id, STATE_WAITING_PRODUCT_URL)
    bot.send_message(
        chat_id,
        "Ab JioMart product ka URL bhejiye.",
        reply_markup=cancel_markup(),
    )


def ask_run_confirmation(chat_id):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_DRY_RUN), types.KeyboardButton(BUTTON_REAL_ORDER))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))

    bot.send_message(
        chat_id,
        "Product save ho gaya. Ab pipeline run karni hai?",
        reply_markup=markup,
    )


def ask_add_more_products(chat_id):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_ADD_MORE), types.KeyboardButton(BUTTON_DONE))
    markup.add(types.KeyboardButton(BUTTON_EDIT_PRODUCTS))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))

    products = user_states.get(chat_id, {}).get("products", [])
    product_lines = format_products_summary(products) if products else "No products saved."
    bot.send_message(
        chat_id,
        "Product Card\n\n"
        f"{product_lines}\n\n"
        "Aur product add/edit karna hai?",
        reply_markup=markup,
    )


def format_products_summary(products):
    lines = []
    for idx, product in enumerate(products, 1):
        lines.append(
            f"{idx}. Qty {product.get('quantity')}: {product.get('product_url')}"
        )
    return "\n".join(lines)


def ask_account_count(chat_id):
    set_state(chat_id, STATE_WAITING_ACCOUNT_COUNT)
    bot.send_message(
        chat_id,
        "Kitne JioMart ID/account se ye order run karna hai?\n"
        "Example: 1\n\n"
        f"Charge real order ke liye {format_rupees(ORDER_CHARGE_PAISE)} per account hai.",
        reply_markup=cancel_markup(),
    )


def ask_coupon_code(chat_id):
    set_state(chat_id, STATE_WAITING_COUPON_CODE)
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_SKIP_COUPON))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    bot.send_message(
        chat_id,
        "Coupon code apply karna hai to bhejiye. Example: RA15\n\n"
        "Coupon nahi lagana hai to Skip Coupon dabaiye.",
        reply_markup=markup,
    )


def clean_pipeline_output(output):
    return output.replace("[OK]", "OK").replace("[ERR]", "ERROR").strip()


def build_pipeline_message(process):
    stdout = clean_pipeline_output(process.stdout or "")
    stderr = clean_pipeline_output(process.stderr or "")
    combined_output = f"{stdout}\n{stderr}"

    if "ORDER PLACED SUCCESSFULLY!" in stdout:
        header = "ORDER PLACED SUCCESSFULLY!\n\n"
    elif "DRY RUN COMPLETE" in stdout:
        header = "DRY RUN COMPLETE - Order not placed.\n\n"
    else:
        header = "Pipeline execution finished.\n\n"

    important_keywords = [
        "Account",
        "Dry Run",
        "Products",
        "Order ID",
        "Deleted",
        "created",
        "Created",
        "Cart ID",
        "Cart Qty",
        "Product :",
        "Price   :",
        "Total",
        "Address  :",
        "Payment",
        "ERROR",
        "Cannot",
        "Failed",
        "No cookies",
        "OUTSIDE",
    ]
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    summary_lines = [
        line for line in lines if any(keyword in line for keyword in important_keywords)
    ]

    if process.returncode != 0:
        tail = "\n".join(lines[-25:])
        details = stderr or tail or "No error details returned by the pipeline."
        login_hint = ""
        if (
            "Cannot fetch addresses: 401" in combined_output
            or "No active session found" in combined_output
            or "Cookies expired" in combined_output
        ):
            login_hint = (
                "Login session expired. Run this in PowerShell, login in normal Edge, "
                "then run the bot again:\n"
                "cd A:\\jioapi\n"
                "npm run cookies:live-edge -- --email default\n\n"
            )
        return (
            header
            + login_hint
            + "\n".join(summary_lines[-25:])
            + f"\n\nError (Exit Code {process.returncode}):\n"
            + details[:1800]
        )[:4000]

    if not summary_lines:
        summary_lines = lines[-20:]
    return (header + "\n".join(summary_lines[-30:]))[:4000]


def load_last_pipeline_result():
    result_path = os.path.join(DIR, "last_order.json")
    if not os.path.exists(result_path):
        return {}
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@bot.message_handler(commands=["start"])
def handle_start(message):
    client = remember_client(message)
    chat_id = message.chat.id
    set_state(chat_id, STATE_IDLE)
    notify_admin_new_pending_user(client)
    send_main_menu(chat_id)


@bot.message_handler(commands=["order", "location"])
def handle_order_command(message):
    start_order_flow(message)


@bot.message_handler(commands=["balance"])
def handle_balance_command(message):
    if not ensure_approved(message):
        return
    balance = order_store.get_client_balance(message.chat.id)
    deposits = order_store.list_client_deposits(message.chat.id, limit=3)
    lines = [f"💰 My Balance\n\nAvailable: {format_rupees(balance)}"]
    if deposits:
        lines.append("\nRecent deposits:")
        for dep in deposits:
            lines.append(
                f"#{dep['id']} {format_rupees(dep['amount_paise'])} - {dep['status']}"
            )
    bot.reply_to(message, "\n".join(lines), reply_markup=main_menu_markup())


@bot.message_handler(commands=["deposit"])
def handle_deposit_command(message):
    if not ensure_approved(message):
        return
    set_state(message.chat.id, STATE_WAITING_DEPOSIT_AMOUNT)
    bot.reply_to(
        message,
        "💳 Deposit\n\nAmount rupees me bhejiye. Example: 100",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(commands=["check_payment"])
def handle_check_payment_command(message):
    if not ensure_approved(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        latest = order_store.list_client_deposits(message.chat.id, limit=1)
        if not latest:
            bot.reply_to(message, "Usage: /check_payment deposit_id")
            return
        deposit_id = latest[0]["id"]
    else:
        deposit_id = int(parts[1].strip())
    dep = order_store.get_deposit(deposit_id)
    if not dep:
        bot.reply_to(message, "Deposit nahi mila.")
        return
    if dep["telegram_chat_id"] != message.chat.id and not is_admin(message.chat.id):
        bot.reply_to(message, "Ye deposit aapka nahi hai.")
        return
    handle_gateway_payment_status(message.chat.id, deposit_id, notify_pending=True)


@bot.message_handler(commands=["orders"])
def handle_orders_command(message):
    if not ensure_approved(message):
        return
    rows = order_store.list_client_orders(message.chat.id, limit=10)
    if not rows:
        bot.reply_to(message, "📋 My Orders\n\nAbhi koi order history nahi hai.", reply_markup=main_menu_markup())
        return
    lines = ["📋 My Orders"]
    for row in rows:
        mode = "Dry" if row.get("dry_run") else "Real"
        lines.append(
            f"#{row['id']} {mode} - {row['status']} - Qty {row['quantity']} - {row.get('account_key') or '-'}"
        )
    bot.reply_to(message, "\n".join(lines), reply_markup=main_menu_markup())


@bot.message_handler(commands=["help"])
def handle_help_command(message):
    client = remember_client(message)
    support_line = f"\nSupport: {SUPPORT_CONTACT}\n" if SUPPORT_CONTACT else ""
    bot.reply_to(
        message,
        "ℹ️ Help\n\n"
        "/order - new order start\n"
        "/balance - wallet balance\n"
        "/deposit - add balance request\n"
        "/orders - last orders\n"
        f"Invite link: {bot_invite_link()}\n"
        f"Your status: {client.get('status')}\n"
        f"{support_line}"
        "Real order ke liye wallet balance required hai. Deposit proof admin approve karega.",
        reply_markup=main_menu_markup(),
    )


@bot.message_handler(commands=["settings"])
def handle_settings_command(message):
    client = remember_client(message)
    balance = order_store.get_client_balance(message.chat.id)
    address_saved = "yes" if latest_address_row(message.chat.id) else "no"
    orders = order_store.list_client_orders(message.chat.id, limit=5)
    admin_lines = ""
    if is_admin(message.chat.id):
        accounts = order_store.list_accounts()
        active_accounts = len([acc for acc in accounts if acc.get("status") == "active"])
        pending_users = len(order_store.list_pending_clients(limit=100))
        pending_deposits = order_store.count_pending_deposits()
        admin_lines = (
            "\nAdmin\n"
            f"Active JioMart accounts: {active_accounts}\n"
            f"Pending users: {pending_users}\n"
            f"Pending deposits: {pending_deposits}\n"
            "Commands: /pending_users, /accounts, /sync_accounts\n"
        )
    bot.reply_to(
        message,
        "⚙️ Settings\n\n"
        f"Telegram chat ID: {message.chat.id}\n"
        f"Username: @{client.get('telegram_username') or '-'}\n"
        f"Status: {client.get('status')}\n"
        f"Balance: {format_rupees(balance)}\n"
        f"Charge: {format_rupees(ORDER_CHARGE_PAISE)} per account per order run\n"
        f"Default address saved: {address_saved}\n"
        f"Recent orders shown: {len(orders)}\n"
        f"Invite link: {bot_invite_link()}"
        + (f"\nSupport: {SUPPORT_CONTACT}" if SUPPORT_CONTACT else "")
        + admin_lines,
        reply_markup=main_menu_markup(),
    )


@bot.message_handler(commands=["accounts"])
def handle_accounts(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return

    keys = load_cookie_account_keys()
    if keys:
        order_store.sync_accounts_from_keys(keys)
    accounts = order_store.list_accounts()
    if not keys:
        bot.reply_to(
            message,
            "No accounts found in a.json. First run: npm.cmd run cookies:live-edge -- --email account1",
        )
        return

    current = selected_account(message.chat.id)
    bot.reply_to(
        message,
        "Account pool:\n"
        + "\n".join(
            f"- {acc['account_key']}: {acc['status']}, "
            f"{acc['orders_today']}/{acc['max_orders_per_day']}"
            f"{' (selected)' if acc['account_key'] == current else ''}"
            for acc in accounts
        )
        + "\n\nUse: /use account_key for debug override.",
    )


@bot.message_handler(commands=["sync_accounts"])
def handle_sync_accounts(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return

    keys = load_cookie_account_keys()
    if not keys:
        bot.reply_to(message, "a.json me koi account key nahi mili.")
        return

    added = order_store.sync_accounts_from_keys(keys)
    bot.reply_to(message, f"Synced {len(keys)} account(s). New added: {added}.")


@bot.message_handler(commands=["use"])
def handle_use_account(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin/debug command hai. Normal orders account pool se auto assign honge.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        bot.reply_to(message, "Use format: /use account_key")
        return

    account = parts[1].strip()
    keys = load_cookie_account_keys()
    if account not in keys:
        bot.reply_to(
            message,
            f"Account '{account}' not found. Run /accounts to see saved accounts.",
        )
        return

    chat_accounts[message.chat.id] = account
    bot.reply_to(message, f"Selected account: {account}")


@bot.message_handler(commands=["pending_users"])
def handle_pending_users(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return
    users = order_store.list_pending_clients(limit=25)
    if not users:
        bot.reply_to(message, "Koi pending user nahi hai.")
        return
    lines = ["Pending users:"]
    for client in users:
        lines.append(
            f"- {user_label(client)} | {client['telegram_chat_id']} | /approve_user {client['telegram_chat_id']}"
        )
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["approve_user"])
def handle_approve_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "Use format: /approve_user chat_id")
        return
    chat_id = int(parts[1].strip())
    client = order_store.get_client_by_chat_id(chat_id)
    if not client:
        bot.reply_to(message, "Client nahi mila.")
        return
    order_store.set_client_status(chat_id, "active")
    bot.reply_to(message, f"Approved user {chat_id}.")
    try:
        bot.send_message(
            chat_id,
            "✅ Account approved. Ab aap order/deposit use kar sakte hain.",
            reply_markup=main_menu_markup(),
        )
    except Exception:
        pass


@bot.message_handler(commands=["reject_user"])
def handle_reject_user(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "Use format: /reject_user chat_id")
        return
    chat_id = int(parts[1].strip())
    client = order_store.get_client_by_chat_id(chat_id)
    if not client:
        bot.reply_to(message, "Client nahi mila.")
        return
    order_store.set_client_status(chat_id, "rejected")
    bot.reply_to(message, f"Rejected user {chat_id}.")
    try:
        bot.send_message(chat_id, "❌ Account request rejected.")
    except Exception:
        pass


@bot.message_handler(commands=["approve_deposit"])
def handle_approve_deposit(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().isdigit():
        deposit_id = int(parts[1].strip())
    else:
        latest = order_store.get_latest_pending_deposit()
        if not latest:
            bot.reply_to(message, "Koi pending deposit nahi mila.")
            return
        deposit_id = latest["id"]
    dep = order_store.approve_deposit(deposit_id, admin_note=f"approved by {message.chat.id}")
    if not dep:
        bot.reply_to(message, "Deposit pending nahi mila ya already processed hai.")
        return
    bot.reply_to(message, f"Approved deposit #{dep['id']} for {format_rupees(dep['amount_paise'])}.")
    try:
        bot.send_message(
            dep["telegram_chat_id"],
            f"✅ Deposit approved: {format_rupees(dep['amount_paise'])}\n"
            f"New balance: {format_rupees(order_store.get_client_balance(dep['telegram_chat_id']))}",
            reply_markup=main_menu_markup(),
        )
    except Exception:
        pass


@bot.message_handler(commands=["reject_deposit"])
def handle_reject_deposit(message):
    if not is_admin(message.chat.id):
        bot.reply_to(message, "Ye admin command hai.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip().isdigit():
        deposit_id = int(parts[1].strip())
    else:
        latest = order_store.get_latest_pending_deposit()
        if not latest:
            bot.reply_to(message, "Koi pending deposit nahi mila.")
            return
        deposit_id = latest["id"]
    dep = order_store.reject_deposit(deposit_id, admin_note=f"rejected by {message.chat.id}")
    if not dep:
        bot.reply_to(message, "Deposit pending nahi mila ya already processed hai.")
        return
    bot.reply_to(message, f"Rejected deposit #{dep['id']}.")
    try:
        bot.send_message(
            dep["telegram_chat_id"],
            f"❌ Deposit rejected: {format_rupees(dep['amount_paise'])}",
            reply_markup=main_menu_markup(),
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("deposit_approve_") or call.data.startswith("deposit_reject_"))
def handle_deposit_action_button(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "Admin only")
        return

    parts = call.data.split("_")
    action = parts[1]
    deposit_id = int(parts[2])

    if action == "approve":
        dep = order_store.approve_deposit(deposit_id, admin_note=f"approved by {call.message.chat.id}")
        label = "Approved"
    else:
        dep = order_store.reject_deposit(deposit_id, admin_note=f"rejected by {call.message.chat.id}")
        label = "Rejected"

    if not dep:
        bot.answer_callback_query(call.id, "Already processed or not found")
        return

    try:
        if action == "approve":
            bot.send_message(
                dep["telegram_chat_id"],
                f"Deposit approved: {format_rupees(dep['amount_paise'])}\n"
                f"New balance: {format_rupees(order_store.get_client_balance(dep['telegram_chat_id']))}",
                reply_markup=main_menu_markup(),
            )
        else:
            bot.send_message(
                dep["telegram_chat_id"],
                f"Deposit rejected: {format_rupees(dep['amount_paise'])}",
                reply_markup=main_menu_markup(),
            )
    except Exception:
        pass

    bot.answer_callback_query(call.id, f"{label} deposit #{dep['id']}")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, f"{label} deposit #{dep['id']} for {format_rupees(dep['amount_paise'])}.")


@bot.message_handler(
    func=lambda msg: is_active_flow(msg.chat.id)
    and (msg.text or "").strip() in {BUTTON_CANCEL, "Cancel", "cancel", "/cancel"}
)
def handle_active_flow_cancel(message):
    flow_cancelled(message)


@bot.message_handler(
    func=lambda msg: is_active_flow(msg.chat.id)
    and (msg.text or "").strip() in MENU_BUTTONS
)
def handle_active_flow_menu_button(message):
    bot.reply_to(
        message,
        "Abhi ek flow chal raha hai. Pehle Cancel dabaiye, phir menu option choose kijiye.",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_DEPOSIT_AMOUNT)
def handle_deposit_amount(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return
    try:
        amount_rupees = float(text)
    except ValueError:
        bot.reply_to(message, "Amount number me bhejiye. Example: 100")
        return
    if amount_rupees < 1:
        bot.reply_to(message, "Minimum deposit ₹1 hai.")
        return

    amount_paise = int(round(amount_rupees * 100))
    if PHONEPE.enabled:
        if not PHONEPE.is_configured():
            bot.reply_to(
                message,
                "PhonePe enabled hai, lekin config missing hai. Admin client_id/client_secret/redirect_url set kare.",
                reply_markup=main_menu_markup(),
            )
            set_state(chat_id, STATE_IDLE)
            return
        deposit_id = order_store.create_deposit(chat_id, amount_paise, provider="phonepe")
        merchant_order_id = build_phonepe_merchant_order_id(deposit_id)
        order_store.update_deposit_gateway(
            deposit_id,
            merchant_order_id=merchant_order_id,
            payload={"merchantOrderId": merchant_order_id},
        )
        try:
            response = PHONEPE.create_payment(
                merchant_order_id,
                amount_paise,
                message=f"Deposit {format_rupees(amount_paise)}",
            )
        except PhonePeError as exc:
            order_store.fail_gateway_deposit(
                deposit_id,
                "phonepe",
                payload={"error": str(exc), "merchantOrderId": merchant_order_id},
                admin_note="PhonePe create payment failed",
            )
            bot.reply_to(
                message,
                f"PhonePe payment create nahi ho paya: {exc}\nAdmin se contact kijiye.",
                reply_markup=main_menu_markup(),
            )
            set_state(chat_id, STATE_IDLE)
            return

        payment_url = response.get("redirectUrl")
        order_store.update_deposit_gateway(
            deposit_id,
            gateway_order_id=response.get("orderId"),
            payment_url=payment_url,
            payload={**response, "merchantOrderId": merchant_order_id},
        )
        set_state(chat_id, STATE_IDLE)
        if payment_url:
            try:
                bot.send_photo(
                    chat_id,
                    build_qr_url(payment_url),
                    caption=(
                        f"PhonePe payment ready: {format_rupees(amount_paise)}\n"
                        f"Deposit ID: #{deposit_id}\n\n"
                        "QR scan kijiye ya button se payment open kijiye. Payment ke baad Check Payment dabaiye."
                    ),
                    reply_markup=payment_check_markup(deposit_id, payment_url),
                )
            except Exception:
                bot.send_message(
                    chat_id,
                    f"PhonePe payment ready: {format_rupees(amount_paise)}\n"
                    f"Payment link: {payment_url}\n\n"
                    "Payment ke baad Check Payment dabaiye.",
                    reply_markup=payment_check_markup(deposit_id, payment_url),
                )
        else:
            bot.send_message(
                chat_id,
                "PhonePe order bana, lekin payment link response me nahi mila. Admin se contact kijiye.",
                reply_markup=main_menu_markup(),
            )
        return

    set_state(chat_id, STATE_WAITING_DEPOSIT_PROOF, deposit_amount_paise=amount_paise)
    upi_uri = build_upi_uri(amount_rupees)

    if upi_uri:
        try:
            bot.send_photo(
                chat_id,
                build_qr_url(upi_uri),
                caption=(
                    f"Scan QR and pay {format_rupees(amount_paise)}.\n\n"
                    "Payment ke baad UTR/reference number ya screenshot bhejiye."
                ),
                reply_markup=cancel_markup(),
            )
        except Exception:
            bot.send_message(
                chat_id,
                f"Pay {format_rupees(amount_paise)} using this UPI link:\n{upi_uri}\n\n"
                "Payment ke baad UTR/reference number ya screenshot bhejiye.",
                reply_markup=cancel_markup(),
            )
    else:
        bot.send_message(
            chat_id,
            f"Deposit amount: {format_rupees(amount_paise)}\n\n"
            "UPI QR config missing hai. Admin ko UPI details configure karni hongi.\n"
            "Payment proof/UTR bhej sakte hain, request pending rahegi.",
            reply_markup=cancel_markup(),
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("payment_check_"))
def handle_payment_check_button(call):
    deposit_id = int(call.data.split("_")[-1])
    bot.answer_callback_query(call.id, "Checking payment...")
    handle_gateway_payment_status(call.message.chat.id, deposit_id, notify_pending=True)


@bot.message_handler(
    content_types=["text", "photo", "document"],
    func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_DEPOSIT_PROOF,
)
def handle_deposit_proof(message):
    chat_id = message.chat.id
    if message.content_type == "text" and flow_cancelled(message):
        return
    session = user_states.get(chat_id, {})
    amount_paise = session.get("deposit_amount_paise")
    if not amount_paise:
        set_state(chat_id, STATE_IDLE)
        bot.reply_to(message, "Deposit session expire ho gaya. /deposit se dobara try kijiye.")
        return

    utr = None
    screenshot_file_id = None
    if message.content_type == "text":
        utr = (message.text or "").strip()
        if not utr:
            bot.reply_to(message, "UTR/reference blank nahi ho sakta.")
            return
    elif message.content_type == "photo":
        screenshot_file_id = message.photo[-1].file_id
        utr = message.caption or None
    elif message.content_type == "document":
        screenshot_file_id = message.document.file_id
        utr = message.caption or None

    deposit_id = order_store.create_deposit(
        chat_id,
        amount_paise,
        utr=utr,
        screenshot_file_id=screenshot_file_id,
    )
    set_state(chat_id, STATE_IDLE)
    bot.reply_to(
        message,
        f"Deposit request #{deposit_id} pending hai.\n"
        "Admin approve karega, phir balance add ho jayega.",
        reply_markup=main_menu_markup(),
    )

    admin_text = (
        f"New deposit request #{deposit_id}\n"
        f"User: {chat_id}\n"
        f"Amount: {format_rupees(amount_paise)}\n"
        f"UTR: {utr or '-'}\n\n"
        f"Approve: /approve_deposit {deposit_id}\n"
        f"Reject: /reject_deposit {deposit_id}"
    )
    admin_markup = types.InlineKeyboardMarkup()
    admin_markup.add(
        types.InlineKeyboardButton("Approve", callback_data=f"deposit_approve_{deposit_id}"),
        types.InlineKeyboardButton("Reject", callback_data=f"deposit_reject_{deposit_id}"),
    )
    for admin_id in ADMIN_CHAT_IDS:
        try:
            if screenshot_file_id and message.content_type == "photo":
                bot.send_photo(admin_id, screenshot_file_id, caption=admin_text, reply_markup=admin_markup)
            elif screenshot_file_id and message.content_type == "document":
                bot.send_document(admin_id, screenshot_file_id, caption=admin_text, reply_markup=admin_markup)
            else:
                bot.send_message(admin_id, admin_text, reply_markup=admin_markup)
        except Exception:
            pass


@bot.message_handler(
    func=lambda msg: (current_state(msg.chat.id) in {None, STATE_IDLE})
    and bool((msg.text or "").strip())
)
def handle_main_menu_buttons(message):
    remember_client(message)
    text = (message.text or "").strip()
    if text == BUTTON_NEW_ORDER:
        start_order_flow(message)
    elif text == BUTTON_MY_ORDERS:
        handle_orders_command(message)
    elif text == BUTTON_MY_BALANCE:
        handle_balance_command(message)
    elif text == BUTTON_DEPOSIT:
        handle_deposit_command(message)
    elif text == BUTTON_SETTINGS:
        handle_settings_command(message)
    elif text == BUTTON_HELP:
        handle_help_command(message)
    else:
        send_main_menu(message.chat.id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_ADDRESS_CHOICE)
def handle_address_choice(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if text == BUTTON_USE_SAVED_ADDRESS:
        address_row = latest_address_row(chat_id)
        if not address_row:
            ask_pincode(chat_id, "Saved address missing hai. Apna 6-digit pincode enter kijiye:")
            return
        session = user_states.setdefault(chat_id, {})
        session["address_id"] = address_row.get("id")
        bot.reply_to(
            message,
            "Saved address selected.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        ask_product_card_or_url(chat_id)
        return

    if text == BUTTON_EDIT_ADDRESS:
        ask_pincode(chat_id, "Address edit karte hain. Apna 6-digit pincode enter kijiye:")
        return

    if text == BUTTON_CHANGE_PHONE:
        address_row = latest_address_row(chat_id)
        if not address_row:
            ask_pincode(chat_id, "Saved address missing hai. Apna 6-digit pincode enter kijiye:")
            return
        set_state(chat_id, STATE_WAITING_PHONE, address_id=address_row.get("id"))
        bot.reply_to(
            message,
            "New delivery phone number bhejiye. Example: 9876543210",
            reply_markup=cancel_markup(),
        )
        return

    bot.reply_to(
        message,
        "Please Use Saved Address, Change Phone ya Edit Address button select kijiye.",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PHONE)
def handle_phone_change(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().replace(" ", "")
    if flow_cancelled(message):
        return

    if text.startswith("+91"):
        text = text[3:]
    elif text.startswith("91") and len(text) == 12:
        text = text[2:]

    if not text.isdigit() or len(text) != 10:
        bot.reply_to(
            message,
            "Invalid phone number. 10-digit mobile number bhejiye. Example: 9876543210",
            reply_markup=cancel_markup(),
        )
        return

    session = user_states.setdefault(chat_id, {})
    address_id = session.get("address_id")
    if not address_id:
        address_row = latest_address_row(chat_id)
        address_id = address_row.get("id") if address_row else None
    if not address_id or not order_store.update_address_phone(chat_id, address_id, text):
        bot.reply_to(message, "Phone update nahi ho paya. Address edit karke dobara try kijiye.")
        ask_pincode(chat_id, "Apna 6-digit pincode enter kijiye:")
        return

    existing = load_existing_address()
    existing["phone"] = text
    save_address(existing)

    address_row = latest_address_row(chat_id)
    bot.reply_to(
        message,
        f"Phone updated: {text}",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    ask_saved_address_choice(chat_id, address_row)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PIN)
def handle_pincode(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if not text.isdigit() or len(text) != 6:
        bot.reply_to(
            message,
            "Invalid pincode. Please 6-digit numeric pincode enter kijiye:",
            reply_markup=cancel_markup(),
        )
        return

    set_state(chat_id, STATE_WAITING_LANDMARK, pincode=text)
    bot.reply_to(
        message,
        "Pincode saved.\n\n"
        "Ab nearest landmark enter kijiye, jaise: Near 7 Eleven Store",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_LANDMARK)
def handle_landmark(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if not text:
        bot.reply_to(
            message,
            "Landmark blank nahi ho sakta. Please landmark enter kijiye:",
            reply_markup=cancel_markup(),
        )
        return

    set_state(chat_id, STATE_WAITING_HOUSE_NO, landmark=text)
    bot.reply_to(
        message,
        f"Landmark saved: {text}\n\n"
        "Ab apna flat number / house number / building name enter kijiye:",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_HOUSE_NO)
def handle_house_no(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if not text:
        bot.reply_to(
            message,
            "House details blank nahi ho sakte. Please house/building enter kijiye:",
            reply_markup=cancel_markup(),
        )
        return

    set_state(chat_id, STATE_WAITING_LOCATION, house_no=text)

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_LOCATION, request_location=True))
    markup.add(types.KeyboardButton(BUTTON_CANCEL))
    bot.send_message(
        chat_id,
        "House details saved.\n\n"
        "Ab bottom button se current location share kijiye, taaki exact latitude/longitude save ho sake.",
        reply_markup=markup,
    )


@bot.message_handler(
    content_types=["location"],
    func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_LOCATION,
)
def handle_location(message):
    chat_id = message.chat.id
    lat = message.location.latitude
    lon = message.location.longitude

    bot.send_chat_action(chat_id, "find_location")
    road, city, state = reverse_geocode(lat, lon)

    session = user_states[chat_id]
    session["latitude"] = lat
    session["longitude"] = lon
    session["road"] = road

    if not city or not state:
        set_state(chat_id, STATE_WAITING_CITY_STATE)
        bot.send_message(
            chat_id,
            "Location save ho gayi, lekin GPS se city/state clear nahi mila.\n"
            "Please city aur state is format me bhejiye: Kalyan, Maharashtra",
            reply_markup=cancel_markup(),
        )
        return

    addr = build_address_from_session(chat_id, city, state)
    send_address_summary(chat_id, addr)
    ask_product_card_or_url(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_CITY_STATE)
def handle_city_state(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return
    parts = [part.strip() for part in text.split(",", 1)]

    if len(parts) != 2 or not parts[0] or not parts[1]:
        bot.reply_to(
            message,
            "Please city aur state comma ke saath bhejiye. Example: Kalyan, Maharashtra",
            reply_markup=cancel_markup(),
        )
        return

    city, state = parts
    addr = build_address_from_session(chat_id, city, state)
    send_address_summary(chat_id, addr)
    ask_product_card_or_url(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PRODUCT_URL)
def handle_product_url(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if not text.startswith(("http://", "https://")) or "jiomart.com" not in text:
        bot.reply_to(message, "Please valid JioMart product URL bhejiye.", reply_markup=cancel_markup())
        return

    set_state(chat_id, STATE_WAITING_PRODUCT_QTY, product_url=text)
    bot.reply_to(message, "Product URL saved. Quantity kitni chahiye? Example: 1", reply_markup=cancel_markup())


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_PRODUCT_CARD_CHOICE)
def handle_product_card_choice(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    if text == BUTTON_USE_SAVED_PRODUCTS:
        card = latest_product_card(chat_id)
        if not card:
            bot.reply_to(message, "Saved product card missing hai. Product URL bhejiye.")
            ask_product_url(chat_id)
            return
        products = product_card_products(card)
        if not products:
            bot.reply_to(message, "Saved product card empty hai. Product URL bhejiye.")
            ask_product_url(chat_id)
            return
        order = build_order_json(products, dry_run=True)
        set_state(
            chat_id,
            STATE_WAITING_ACCOUNT_COUNT,
            order=order,
            products=products,
            product_card_id=card.get("id"),
        )
        bot.reply_to(
            message,
            "Saved products selected:\n" + format_products_summary(products),
            reply_markup=cancel_markup(),
        )
        ask_coupon_code(chat_id)
        return

    if text in {BUTTON_EDIT_PRODUCTS, BUTTON_NEW_PRODUCTS}:
        session = user_states.setdefault(chat_id, {})
        session["products"] = []
        session["order"] = build_order_json([], dry_run=True)
        set_state(chat_id, STATE_WAITING_PRODUCT_URL, products=[])
        prompt = (
            "Products edit karte hain. Ab JioMart product ka URL bhejiye."
            if text == BUTTON_EDIT_PRODUCTS
            else "New product card banate hain. Ab JioMart product ka URL bhejiye."
        )
        bot.reply_to(message, prompt, reply_markup=cancel_markup())
        return

    bot.reply_to(
        message,
        "Please Use Saved Products, Edit Products ya New Products button select kijiye.",
        reply_markup=cancel_markup(),
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PRODUCT_QTY)
def handle_product_quantity(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    try:
        quantity = int(text)
    except ValueError:
        bot.reply_to(message, "Quantity number me bhejiye. Example: 1", reply_markup=cancel_markup())
        return

    if quantity < 1 or quantity > 20:
        bot.reply_to(message, "Quantity 1 se 20 ke beech honi chahiye.", reply_markup=cancel_markup())
        return

    session = user_states.setdefault(chat_id, {})
    product_url = session["product_url"]
    products = session.setdefault("products", [])
    products.append({"product_url": product_url, "quantity": quantity})
    order = build_order_json(products, dry_run=True)
    set_state(chat_id, STATE_PRODUCT_MORE, order=order, products=products)
    bot.reply_to(
        message,
        f"Product saved:\n{product_url}\nQuantity: {quantity}",
    )
    ask_add_more_products(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_PRODUCT_MORE)
def handle_product_more(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    session = user_states.get(chat_id, {})

    if text in {BUTTON_CANCEL, "Cancel", "cancel"}:
        set_state(chat_id, STATE_IDLE)
        bot.reply_to(
            message,
            "Order cancel kar diya gaya hai. /start run karke fir se start kar sakte hain.",
            reply_markup=main_menu_markup(),
        )
        return

    if text == BUTTON_ADD_MORE:
        set_state(chat_id, STATE_WAITING_PRODUCT_URL)
        bot.reply_to(
            message,
            "Next JioMart product ka URL bhejiye.",
            reply_markup=cancel_markup(),
        )
        return

    if text == BUTTON_EDIT_PRODUCTS:
        session["products"] = []
        session["order"] = build_order_json([], dry_run=True)
        set_state(chat_id, STATE_WAITING_PRODUCT_URL, products=[])
        bot.reply_to(
            message,
            "Products clear kar diye. Ab JioMart product ka URL bhejiye.",
            reply_markup=cancel_markup(),
        )
        return

    if text == BUTTON_DONE:
        products = session.get("products", [])
        if not products:
            bot.reply_to(message, "Koi product saved nahi hai. Product URL bhejiye.")
            set_state(chat_id, STATE_WAITING_PRODUCT_URL)
            return
        product_card_id = order_store.save_product_card(chat_id, products)
        order = build_order_json(products, dry_run=True)
        set_state(
            chat_id,
            STATE_WAITING_ACCOUNT_COUNT,
            order=order,
            products=products,
            product_card_id=product_card_id,
        )
        bot.reply_to(
            message,
            "Products ready and saved as Product Card:\n" + format_products_summary(products),
            reply_markup=cancel_markup(),
        )
        ask_coupon_code(chat_id)
        return

    bot.reply_to(message, "Please Add More, Edit Products ya Done button select kijiye.")


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_COUPON_CODE)
def handle_coupon_code(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    session = user_states.get(chat_id, {})
    products = session.get("products", [])
    order = session.get("order") or build_order_json(products, dry_run=True)

    if text and text not in {BUTTON_SKIP_COUPON, "skip", "Skip", "no", "No"}:
        coupon_code = text.upper().replace(" ", "")
        order["coupon_code"] = coupon_code
        session["coupon_code"] = coupon_code
        bot.reply_to(message, f"Coupon saved: {coupon_code}", reply_markup=types.ReplyKeyboardRemove())
    else:
        order.pop("coupon_code", None)
        session["coupon_code"] = ""
        bot.reply_to(message, "Coupon skip kar diya.", reply_markup=types.ReplyKeyboardRemove())

    set_state(chat_id, STATE_WAITING_ACCOUNT_COUNT, order=order, products=products)
    ask_account_count(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_ACCOUNT_COUNT)
def handle_account_count(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if flow_cancelled(message):
        return

    try:
        account_count = int(text)
    except ValueError:
        bot.reply_to(message, "Account count number me bhejiye. Example: 1", reply_markup=cancel_markup())
        return

    if account_count < 1 or account_count > 20:
        bot.reply_to(message, "Account count 1 se 20 ke beech hona chahiye.", reply_markup=cancel_markup())
        return

    session = user_states.get(chat_id, {})
    products = session.get("products", [])
    order = session.get("order") or build_order_json(products, dry_run=True)
    set_state(chat_id, STATE_CONFIRMATION, order=order, products=products, account_count=account_count)
    balance = order_store.get_client_balance(chat_id)
    per_account_charge = order_charge_per_account(chat_id)
    bot.reply_to(
        message,
        f"Account count saved: {account_count}\n"
        f"Real order charge: {format_rupees(account_count * per_account_charge)}\n"
        f"Current balance: {format_rupees(balance)}",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    ask_run_confirmation(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_CONFIRMATION)
def handle_pipeline_trigger(message):
    chat_id = message.chat.id
    remember_client(message)
    text = (message.text or "").strip()

    cancel_values = {BUTTON_CANCEL, "Cancel", "cancel"}
    dry_values = {BUTTON_DRY_RUN, "Run Pipeline (Dry Run)"}
    real_values = {BUTTON_REAL_ORDER, "Run Pipeline (Real Order)"}

    if text in cancel_values:
        set_state(chat_id, STATE_IDLE)
        bot.reply_to(
            message,
            "Order cancel kar diya gaya hai. /start run karke fir se start kar sakte hain.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    session = user_states.get(chat_id, {})
    if text in real_values:
        order_data = session.get("order") or {}
        products = order_data.get("products") or []
        account_count = int(session.get("account_count") or 1)
        balance = order_store.get_client_balance(chat_id)
        total_charge = account_count * order_charge_per_account(chat_id)
        if balance < total_charge:
            bot.reply_to(
                message,
                "Balance low hai. Real order start nahi ho sakta.\n\n"
                f"Required: {format_rupees(total_charge)}\n"
                f"Available: {format_rupees(balance)}\n\n"
                "Deposit ke liye /deposit use kijiye.",
                reply_markup=main_menu_markup(),
            )
            set_state(chat_id, STATE_IDLE)
            return
        set_state(chat_id, STATE_CONFIRMATION, real_confirm_required=True)
        bot.reply_to(
            message,
            "REAL ORDER confirm karne ke liye exactly ye type kijiye:\n"
            f"{REAL_CONFIRM_TEXT}\n\n"
            f"Accounts/IDs: {account_count}\n"
            f"Charge: {format_rupees(total_charge)}\n"
            f"Current balance: {format_rupees(balance)}\n\n"
            "Products:\n"
            + (format_products_summary(products) if products else "?"),
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    if text.upper() == REAL_CONFIRM_TEXT and session.get("real_confirm_required"):
        is_dry = False
    elif text in dry_values:
        is_dry = True
    else:
        bot.reply_to(message, "Please button se Dry Run ya Real Order select kijiye.")
        return

    order_data = session.get("order") or {}
    products = order_data.get("products") or []
    if not products:
        bot.reply_to(message, "Product details missing hain. /start se fir se try kijiye.")
        set_state(chat_id, STATE_IDLE)
        return

    address_row = latest_address_row(chat_id)
    if not address_row:
        bot.reply_to(message, "Address missing hai. /start se address setup kijiye.")
        set_state(chat_id, STATE_IDLE)
        return

    order_store.sync_accounts_from_keys(load_cookie_account_keys())

    override_key = chat_accounts.get(chat_id)
    account_count = int(session.get("account_count") or 1)
    if override_key:
        account = order_store.get_account_by_key(override_key)
        accounts = [account] if account and account.get("status") == "active" else []
        if account_count > 1:
            bot.reply_to(
                message,
                "Debug override /use active hai, isliye sirf 1 account run ho sakta hai. /use clear karne ke liye bot restart ya normal account pool use kijiye.",
                reply_markup=main_menu_markup(),
            )
            set_state(chat_id, STATE_IDLE)
            return
    else:
        accounts = order_store.pick_accounts(account_count)

    if not accounts:
        bot.reply_to(
            message,
            "Abhi koi active JioMart account available nahi hai. Ya to cookies sync karo, ya daily limit complete ho gayi hai.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        set_state(chat_id, STATE_IDLE)
        return
    if len(accounts) < account_count:
        bot.reply_to(
            message,
            f"Sirf {len(accounts)} active account available hai, lekin aapne {account_count} manga.\n"
            "Accounts/cookies sync karo ya lower count ke saath try karo.",
            reply_markup=main_menu_markup(),
        )
        set_state(chat_id, STATE_IDLE)
        return

    per_account_charge = order_charge_per_account(chat_id)
    charge_paise = 0 if is_dry else account_count * per_account_charge
    if charge_paise:
        balance = order_store.get_client_balance(chat_id)
        if balance < charge_paise:
            bot.reply_to(
                message,
                "Balance low hai.\n\n"
                f"Required: {format_rupees(charge_paise)}\n"
                f"Available: {format_rupees(balance)}\n\n"
                "Deposit ke liye /deposit use kijiye.",
                reply_markup=main_menu_markup(),
            )
            set_state(chat_id, STATE_IDLE)
            return
        if not order_store.deduct_client_balance(chat_id, charge_paise):
            bot.reply_to(message, "Balance deduct nahi ho paya. Please dobara try kijiye.")
            set_state(chat_id, STATE_IDLE)
            return

    product_url = products[0]["product_url"]
    quantity = sum(int(product.get("quantity", 1)) for product in products)
    pipeline_order = dict(order_data)
    pipeline_order["dry_run"] = is_dry
    pipeline_order["products"] = products
    pipeline_address = order_store.address_to_pipeline_json(address_row)

    launch_message = (
        f"Pipeline launch ho rahi hai. Mode: {'Dry Run' if is_dry else 'REAL ORDER'}.\n\n"
        f"Accounts/IDs: {len(accounts)}\n"
        f"Account keys: {', '.join(acc['account_key'] for acc in accounts)}\n\n"
        + (f"Charged: {format_rupees(charge_paise)}\n\n" if charge_paise else "")
        + "Processing start ho gayi hai. Har account me 30-40 seconds lag sakte hain."
    )
    bot.send_message(
        chat_id,
        launch_message,
        reply_markup=types.ReplyKeyboardRemove(),
    )
    bot.send_chat_action(chat_id, "typing")

    successful_runs = 0
    failed_runs = 0
    refunded_paise = 0
    summaries = []
    try:
        for index, account in enumerate(accounts, 1):
            account_key = account["account_key"]
            db_order_id = order_store.create_order(
                chat_id,
                address_row["id"],
                account["id"],
                product_url,
                quantity,
                is_dry,
            )
            address_path, order_path = write_pipeline_files(
                chat_id,
                db_order_id,
                pipeline_address,
                pipeline_order,
            )
            bot.send_message(chat_id, f"[{index}/{len(accounts)}] Running account: {account_key}")
            process = subprocess.run(
                [
                    sys.executable,
                    os.path.join(DIR, "run_pipeline.py"),
                    "--email",
                    account_key,
                    "--address-file",
                    address_path,
                    "--order-file",
                    order_path,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            if process.returncode == 0:
                successful_runs += 1
                pipeline_result = load_last_pipeline_result() if not is_dry else {}
                order_store.finish_order(
                    db_order_id,
                    "dry_run_done" if is_dry else "placed",
                    total_amount=pipeline_result.get("amount"),
                    jiomart_order_id=pipeline_result.get("order_id"),
                )
                order_store.mark_account_used(account["id"], increment_order_count=not is_dry)
            else:
                failed_runs += 1
                if not is_dry and per_account_charge:
                    order_store.refund_client_balance(chat_id, per_account_charge)
                    refunded_paise += per_account_charge
                order_store.finish_order(db_order_id, "failed", error_message=(process.stderr or process.stdout or "")[:1000])
                if "401" in (process.stderr or process.stdout or ""):
                    order_store.set_account_status(account_key, "expired")
            summaries.append(f"Account {account_key}:\n{build_pipeline_message(process)}")
    except Exception as e:
        remaining_refund = 0 if is_dry else (len(accounts) - successful_runs - failed_runs) * per_account_charge
        if remaining_refund:
            order_store.refund_client_balance(chat_id, remaining_refund)
            refunded_paise += remaining_refund
        bot.send_message(chat_id, f"Pipeline running process crashed: {e}")

    summary = (
        "Order run complete.\n\n"
        f"Requested accounts: {len(accounts)}\n"
        f"Successful: {successful_runs}\n"
        f"Failed: {failed_runs}\n"
        f"Charged net: {format_rupees(max(charge_paise - refunded_paise, 0))}\n"
        f"Refunded: {format_rupees(refunded_paise)}"
    )
    bot.send_message(chat_id, summary)
    for chunk in summaries[-5:]:
        bot.send_message(chat_id, chunk[:3500])

    set_state(chat_id, STATE_IDLE)
    send_main_menu(chat_id)


def run_polling_forever():
    configure_bot_menu()
    print("JioMart Telegram Bot is polling...")
    while True:
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=20,
                long_polling_timeout=20,
                logger_level=logging.CRITICAL,
            )
        except KeyboardInterrupt:
            print("Bot stopped by user.")
            break
        except Exception as e:
            print(f"Polling crashed/retrying in 5 seconds: {e}")
            try:
                bot.stop_polling()
            except Exception:
                pass
            time.sleep(5)


if __name__ == "__main__":
    run_polling_forever()
