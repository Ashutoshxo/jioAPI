import json
import os
import subprocess
import sys

import requests
import telebot
from telebot import types


DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(DIR, "telegram_config.json")
ADDRESS_FILE = os.path.join(DIR, "address_input.json")
ORDER_FILE = os.path.join(DIR, "order_input.json")


def load_bot_token():
    if not os.path.exists(CONFIG_FILE):
        default_config = {"bot_token": "YOUR_TELEGRAM_BOT_TOKEN_HERE"}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)
        print(f"Created {CONFIG_FILE}. Please add your real bot token from BotFather.")
        return None

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        token = config.get("bot_token")
        if token == "YOUR_TELEGRAM_BOT_TOKEN_HERE" or not token:
            return None
        return token
    except Exception as e:
        print(f"Error loading bot token: {e}")
        return None


BOT_TOKEN = load_bot_token()
if not BOT_TOKEN:
    print("\n[ERROR] Telegram Bot Token is missing in telegram_config.json.")
    print("Please update telegram_config.json with your actual Bot Token from @BotFather.")
    sys.exit(1)


bot = telebot.TeleBot(BOT_TOKEN)


STATE_IDLE = 0
STATE_WAITING_PIN = 1
STATE_WAITING_LANDMARK = 2
STATE_WAITING_HOUSE_NO = 3
STATE_WAITING_LOCATION = 4
STATE_WAITING_CITY_STATE = 5
STATE_WAITING_PRODUCT_URL = 6
STATE_WAITING_PRODUCT_QTY = 7
STATE_CONFIRMATION = 8

BUTTON_LOCATION = "Share Current Location"
BUTTON_DRY_RUN = "Run Pipeline (Dry Run)"
BUTTON_REAL_ORDER = "Run Pipeline (Real Order)"
BUTTON_CANCEL = "Cancel"

user_states = {}
chat_accounts = {}


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


def save_order(data):
    with open(ORDER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_product_to_order(product_url, quantity):
    data = load_existing_order()
    data["products"] = [
        {
            "product_url": product_url,
            "quantity": quantity,
        }
    ]
    data.setdefault("dry_run", True)
    data.setdefault("total_min_price", 0.0)
    data.setdefault("total_max_price", 999999.0)
    save_order(data)
    return data


def set_dry_run(value):
    data = load_existing_order()
    data["dry_run"] = value
    save_order(data)


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
        "Ab JioMart product ka URL bhejiye. Is product ko order_input.json me save karunga.",
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
                "Login session expired. Run this in PowerShell, login in the browser, "
                "then run the bot again:\n"
                "cd A:\\jioapi\n"
                "python .\\get_cookies.py\n\n"
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


@bot.message_handler(commands=["start", "location"])
def handle_start(message):
    chat_id = message.chat.id
    user_states[chat_id] = {"state_val": STATE_WAITING_PIN}
    bot.reply_to(
        message,
        "Welcome! Chaliye address setup karte hain.\n\n"
        "Sabse pehle apna 6-digit pincode enter kijiye:",
    )


@bot.message_handler(commands=["accounts"])
def handle_accounts(message):
    keys = load_cookie_account_keys()
    if not keys:
        bot.reply_to(
            message,
            "No accounts found in a.json. First run: npm.cmd run cookies:live-edge -- --email account1",
        )
        return

    current = selected_account(message.chat.id)
    bot.reply_to(
        message,
        "Saved accounts:\n"
        + "\n".join(f"- {key}{' (selected)' if key == current else ''}" for key in keys)
        + "\n\nUse: /use account_key",
    )


@bot.message_handler(commands=["use"])
def handle_use_account(message):
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


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PIN)
def handle_pincode(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    if not text.isdigit() or len(text) != 6:
        bot.reply_to(message, "Invalid pincode. Please 6-digit numeric pincode enter kijiye:")
        return

    set_state(chat_id, STATE_WAITING_LANDMARK, pincode=text)
    bot.reply_to(
        message,
        "Pincode saved.\n\n"
        "Ab nearest landmark enter kijiye, jaise: Near 7 Eleven Store",
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_LANDMARK)
def handle_landmark(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    if not text:
        bot.reply_to(message, "Landmark blank nahi ho sakta. Please landmark enter kijiye:")
        return

    set_state(chat_id, STATE_WAITING_HOUSE_NO, landmark=text)
    bot.reply_to(
        message,
        f"Landmark saved: {text}\n\n"
        "Ab apna flat number / house number / building name enter kijiye:",
    )


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_HOUSE_NO)
def handle_house_no(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    if not text:
        bot.reply_to(message, "House details blank nahi ho sakte. Please house/building enter kijiye:")
        return

    set_state(chat_id, STATE_WAITING_LOCATION, house_no=text)

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(BUTTON_LOCATION, request_location=True))
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
            reply_markup=types.ReplyKeyboardRemove(),
        )
        return

    addr = build_address_from_session(chat_id, city, state)
    send_address_summary(chat_id, addr)
    ask_product_url(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_CITY_STATE)
def handle_city_state(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    parts = [part.strip() for part in text.split(",", 1)]

    if len(parts) != 2 or not parts[0] or not parts[1]:
        bot.reply_to(message, "Please city aur state comma ke saath bhejiye. Example: Kalyan, Maharashtra")
        return

    city, state = parts
    addr = build_address_from_session(chat_id, city, state)
    send_address_summary(chat_id, addr)
    ask_product_url(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PRODUCT_URL)
def handle_product_url(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    if not text.startswith(("http://", "https://")) or "jiomart.com" not in text:
        bot.reply_to(message, "Please valid JioMart product URL bhejiye.")
        return

    set_state(chat_id, STATE_WAITING_PRODUCT_QTY, product_url=text)
    bot.reply_to(message, "Product URL saved. Quantity kitni chahiye? Example: 1")


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_WAITING_PRODUCT_QTY)
def handle_product_quantity(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    try:
        quantity = int(text)
    except ValueError:
        bot.reply_to(message, "Quantity number me bhejiye. Example: 1")
        return

    if quantity < 1 or quantity > 20:
        bot.reply_to(message, "Quantity 1 se 20 ke beech honi chahiye.")
        return

    product_url = user_states[chat_id]["product_url"]
    order = save_product_to_order(product_url, quantity)
    set_state(chat_id, STATE_CONFIRMATION, order=order)
    bot.reply_to(
        message,
        f"Product saved in order_input.json:\n{product_url}\nQuantity: {quantity}",
    )
    ask_run_confirmation(chat_id)


@bot.message_handler(func=lambda msg: current_state(msg.chat.id) == STATE_CONFIRMATION)
def handle_pipeline_trigger(message):
    chat_id = message.chat.id
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

    if text not in dry_values and text not in real_values:
        bot.reply_to(message, "Please button se Dry Run ya Real Order select kijiye.")
        return

    is_dry = text in dry_values
    set_dry_run(is_dry)
    account_key = selected_account(chat_id)

    bot.send_message(
        chat_id,
        f"Pipeline launch ho rahi hai. Mode: {'Dry Run' if is_dry else 'REAL ORDER'}.\n\n"
        f"Account: {account_key}\n\n"
        "Processing start ho gayi hai. Isme 30-40 seconds lag sakte hain.",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    bot.send_chat_action(chat_id, "typing")

    try:
        process = subprocess.run(
            [sys.executable, os.path.join(DIR, "run_pipeline.py"), "--email", account_key],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        bot.send_message(chat_id, build_pipeline_message(process))
    except Exception as e:
        bot.send_message(chat_id, f"Pipeline running process crashed: {e}")

    set_state(chat_id, STATE_IDLE)


print("JioMart Telegram Bot is polling...")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
