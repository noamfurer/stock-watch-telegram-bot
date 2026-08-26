# Stock Watch Telegram Bot

Telegram bot that checks a private watchlist every 15 minutes during the configured Israeli market window and sends:

- Full Hebrew reports at 11:00, 14:00 and 16:30 Israel time.
- Immediate threshold-crossing alerts, with a default threshold of 4%.
- A report layout matching the supplied Telegram example.

The repository can be public. The watchlist, alert threshold, bot token, chat ID and encryption key are stored only as GitHub Actions Secrets. The small runtime state committed by the workflow is encrypted and contains no readable ticker names.

## Data sources

The monitor first requests current quotes from TradingView's scanner endpoints. Missing quotes fall back to Yahoo Finance. Market data can be delayed or unavailable and is informational only.

## GitHub Secrets

Create these repository secrets under **Settings > Secrets and variables > Actions**:

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token received privately from BotFather |
| `TELEGRAM_SETUP_CODE` | A private, one-time random connection code |
| `TELEGRAM_CHAT_ID` | Optional. A numeric chat ID, if already known |
| `WATCHLIST_JSON` | JSON array in the format below |
| `ALERT_THRESHOLD_PERCENT` | `4` |
| `STATE_ENCRYPTION_KEY` | A Fernet key generated with the command below |

Generate the state key locally:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

After adding the bot token, setup code and state key, send this private message to the new bot:

```text
/connect YOUR_SETUP_CODE
```

Run the workflow manually once, or wait for its next scheduled run. The bot discovers the matching chat without printing its ID, stores it inside the encrypted runtime state and sends a confirmation message. `TELEGRAM_SETUP_CODE` can then be deleted.

Watchlist shape:

```json
[
  {
    "name": "Example TASE company",
    "symbol": "DEMO",
    "exchange": "TASE",
    "currency_symbol": "₪",
    "display_symbol": "DEMO"
  },
  {
    "name": "Example Nasdaq company",
    "symbol": "DEMO",
    "exchange": "NASDAQ",
    "currency_symbol": "$",
    "display_symbol": "DEMO"
  }
]
```

Changing `WATCHLIST_JSON` or `ALERT_THRESHOLD_PERCENT` changes the monitored companies or alert threshold without a code commit.

## Schedule behavior

GitHub Actions cron uses UTC, so the workflow starts every 15 minutes across a broad UTC window. The Python process applies `Asia/Jerusalem` and exits unless local time is Monday-Friday between 10:00 and 18:00. This automatically follows Israeli daylight-saving time.

GitHub documents that scheduled workflows can occasionally start late. A report slot remains eligible for 90 minutes and encrypted state prevents duplicates.

## Local tests

```bash
python -m pip install -r requirements.txt pytest
pytest -q
```
