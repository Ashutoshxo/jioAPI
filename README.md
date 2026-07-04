# jioAPI

JioMart automation helpers for saving a browser session, setting an address, adding products to cart, and running the order pipeline from CLI or Telegram bot.

## Setup

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
- `/sync_accounts` imports saved cookie account keys from `a.json` into the local account pool.
- `/accounts` lists account-pool status and daily usage.
- `/use account_key` switches the admin/debug chat to a specific account.

Public client orders are saved in the local SQLite database `jiobot.sqlite3`.
The bot stores each client's latest address in the database, creates per-order
runtime input files under `runtime/orders/`, and runs `run_pipeline.py` with the
selected account key. Real orders require the user to type `CONFIRM ORDER` after
pressing the real-order button.

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
