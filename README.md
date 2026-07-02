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

Dry run will set address and cart, but it will not place the COD order.

## Run Telegram Bot

```powershell
python .\telegram_bot.py
```

Bot commands:

- `/start` starts address and product setup.
- `/accounts` lists saved cookie account keys.
- `/use account_key` switches the bot chat to that account.

## Local Files Not Committed

The repository intentionally ignores live cookies, bot token config, browser profiles, address/order inputs, and debug outputs:

- `a.json`
- `telegram_config.json`
- `address_input.json`
- `order_input.json`
- `user_data/`
- `debug/`
