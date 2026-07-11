# jioAPI

JioMart automation helpers for saving a browser session, setting an address, adding products to cart, and running the order pipeline from CLI or Telegram bot.

## Setup

Clone the repo:

```powershell
git clone https://github.com/Ashutoshxo/jioAPI.git
cd jioAPI
```

Install Python and Node dependencies:

```powershell
pip install requests pyTelegramBotAPI playwright
python -m playwright install chromium
npm install
```

Create your local config files from the examples:

```powershell
copy telegram_config.example.json telegram_config.json
copy address_input.example.json address_input.json
copy order_input.example.json order_input.json
```

Then edit the copied files with your real bot token, address, and product URL.
For Telegram admin commands, add your Telegram numeric chat ID to
`telegram_config.json` under `admin_chat_ids`.

Telegram bot setup for a new clone:

1. Open Telegram and create a new bot with `@BotFather`.
2. Copy the bot token from BotFather.
3. Paste it into `telegram_config.json`:

```json
{
  "bot_token": "PASTE_YOUR_NEW_BOT_TOKEN_HERE",
  "admin_chat_ids": [123456789],
  "order_charge_rupees": 5,
  "upi_id": "your-upi-id@bank",
  "upi_name": "JioMart Order Bot",
  "require_user_approval": true,
  "support_contact": "@your_support",
  "bot_username": "YourBotUsername"
}
```

Do not commit `telegram_config.json`; it is ignored because it contains the live bot token.

## Save JioMart Cookies

Recommended live Edge export:

```powershell
npm run cookies:live-edge -- --email default
```

For another account, use a different key:

```powershell
npm run cookies:live-edge -- --email account2 --profile "Profile 2"
```

The cookies are saved in `a.json`. This file is ignored by Git because it contains login session data.

## Run Pipeline

Dry run:

```powershell
python .\run_pipeline.py
```

Use a specific saved account:

```powershell
$env:JIOMART_ACCOUNT="account2"
python .\run_pipeline.py
```

Dry run runs the full setup flow: it sets the address, clears/adds cart items,
and verifies price/address, but it stops before the final COD checkout.

## Run Telegram Bot

```powershell
python .\telegram_bot.py
```

Bot commands:

- `/start` starts address and product setup.
- `/deposit` creates a wallet deposit request.
- `/check_payment [deposit_id]` checks a PhonePe deposit status and auto-adds balance when completed.
- `/sync_accounts` imports saved cookie account keys from `a.json` into the local account pool.
- `/accounts` lists account-pool status and daily usage.
- `/use account_key` switches the admin/debug chat to a specific account.

Telegram product flow supports multiple products in one order:

1. Send product URL.
2. Send quantity.
3. Choose `Add More` to add another product, or `Done` to continue.
4. Choose dry run or real order.

Public client orders are saved in the local SQLite database `jiobot.sqlite3`.
The bot stores each client's latest address in the database, creates per-order
runtime input files under `runtime/orders/`, and runs `run_pipeline.py` with the
selected account key. Real orders require the user to type `CONFIRM ORDER` after
pressing the real-order button.

## PhonePe Deposits

The bot supports PhonePe Standard Checkout for dynamic deposit payments. When
`phonepe.enabled` is true in `telegram_config.json`, `/deposit` creates a
PhonePe order for the exact amount, sends the user a checkout QR/link, and
credits wallet balance after PhonePe status returns `COMPLETED`.

Required config:

```json
"phonepe": {
  "enabled": true,
  "environment": "sandbox",
  "client_id": "PHONEPE_CLIENT_ID",
  "client_secret": "PHONEPE_CLIENT_SECRET",
  "client_version": "1",
  "redirect_url": "https://your-domain.example/phonepe-return"
}
```

Use `environment: "production"` only after PhonePe go-live credentials are
ready. Without PhonePe enabled, the bot keeps the old manual UPI proof/admin
approval deposit flow.

Account pool behavior:

- Cookies still live in `a.json`.
- The database tracks account status and daily usage.
- Each active account defaults to 5 real orders per day.
- Dry runs do not consume the daily account count.
- If all accounts are exhausted or expired, the bot refuses the order instead of
  reusing a limited account.

VPS/PostgreSQL deployment is intentionally not included yet. The current DB is
SQLite for local development; the schema is kept simple so it can be migrated to
PostgreSQL later.

## Local Files Not Committed

The repository intentionally ignores live cookies, bot token config, browser profiles, address/order inputs, and debug outputs:

- `a.json`
- `telegram_config.json`
- `address_input.json`
- `order_input.json`
- `user_data/`
- `debug/`
- `runtime/`
- `jiobot.sqlite3`
