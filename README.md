# Stock Watch Telegram Bot

Telegram bot that checks a private watchlist every 15 minutes during the configured Israeli market window and sends:

- Full Hebrew reports at 11:00, 14:00 and 16:30 Israel time.
- Immediate threshold-crossing alerts, with a default threshold of 4%.
- A report layout matching the supplied Telegram example.
- Private updates to every enabled subscriber.

The repository can be public. The watchlist, alert threshold and bot token are stored only as GitHub Actions Secrets. Subscriber Telegram IDs, names, usernames, permissions and notification state are committed only as encrypted runtime data. The public repository contains no readable subscriber or ticker data.

## Subscribers and administration

- Anyone with the bot link can press `Start` and join with status `YES`.
- A new subscriber receives a confirmation and the administrator receives a private notification.
- The administrator can press inline `YES` or `NO` buttons to enable or block that subscriber.
- A blocked subscriber remains encrypted in the registry but receives no reports or alerts.
- Sending `/start` again does not bypass a `NO` status.
- The administrator can send `/users` to receive the full encrypted registry as a private Telegram message with `YES` and `NO` controls.
- Telegram updates are checked every 15 minutes, so joining and permission changes can take up to 15 minutes.

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
| `STATE_ENCRYPTION_KEY` | Optional advanced setting. By default it is derived securely from the bot token |

After adding the bot token, setup code and state key, send this private message to the new bot:

```text
/connect YOUR_SETUP_CODE
```

Run the workflow manually once, or wait for its next scheduled run. The bot discovers the matching chat without printing its ID, stores it inside the encrypted runtime state and sends a confirmation message. `TELEGRAM_SETUP_CODE` can then be deleted.

If the Telegram bot token is rotated later, delete `runtime_state.enc` before the next run or set a permanent `STATE_ENCRYPTION_KEY` first.

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

GitHub Actions schedules primary runs at minutes 07, 22, 37 and 52 of every hour. Backup runs are scheduled five minutes later at minutes 12, 27, 42 and 57. A backup run skips market work when a successful check was recorded in the previous 10 minutes. Manual workflow runs always bypass this backup guard.

Telegram subscriptions and permission buttons are processed throughout the week. The Python process requests market data and sends market notifications only when local time is Monday-Friday between 10:00 and 18:00 in `Asia/Jerusalem`. This automatically follows Israeli daylight-saving time.

GitHub documents that scheduled workflows can occasionally start late. A report slot remains eligible for 90 minutes, encrypted state prevents duplicates, and the backup schedule retries missed primary triggers.

## Local tests

```bash
python -m pip install -r requirements.txt pytest
pytest -q
```
