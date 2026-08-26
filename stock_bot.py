from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken


ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
STATE_PATH = Path(os.getenv("STATE_PATH", "runtime_state.enc"))
REPORT_TIMES = (clock_time(11, 0), clock_time(14, 0), clock_time(16, 30))
REPORT_GRACE = timedelta(minutes=90)

HEBREW_WEEKDAYS = (
    "יום שני",
    "יום שלישי",
    "יום רביעי",
    "יום חמישי",
    "יום שישי",
    "יום שבת",
    "יום ראשון",
)

HEBREW_MONTHS = (
    "",
    "בינואר",
    "בפברואר",
    "במרץ",
    "באפריל",
    "במאי",
    "ביוני",
    "ביולי",
    "באוגוסט",
    "בספטמבר",
    "באוקטובר",
    "בנובמבר",
    "בדצמבר",
)


@dataclass(frozen=True)
class Stock:
    name: str
    symbol: str
    exchange: str
    currency_symbol: str
    display_symbol: str

    @property
    def provider_symbol(self) -> str:
        return f"{self.exchange}:{self.symbol}"

    @property
    def yahoo_symbol(self) -> str:
        return f"{self.symbol}.TA" if self.exchange == "TASE" else self.symbol

    @property
    def private_id(self) -> str:
        return hashlib.sha256(self.provider_symbol.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class Quote:
    stock: Stock
    price: float
    change: float
    change_percent: float


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required secret: {name}")
    return value


def optional_env(name: str) -> str:
    return os.getenv(name, "").strip()


def state_encryption_key(token: str) -> str:
    explicit_key = optional_env("STATE_ENCRYPTION_KEY")
    if explicit_key:
        return explicit_key
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


def load_watchlist(raw: str) -> list[Stock]:
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("WATCHLIST_JSON is not valid JSON") from exc

    if not isinstance(items, list) or not items:
        raise RuntimeError("WATCHLIST_JSON must be a non-empty JSON array")

    stocks: list[Stock] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Each watchlist item must be an object")
        try:
            name = str(item["name"]).strip()
            symbol = str(item["symbol"]).strip().upper()
            exchange = str(item.get("exchange", "TASE")).strip().upper()
        except KeyError as exc:
            raise RuntimeError("Each watchlist item needs name and symbol") from exc
        if not name or not symbol or exchange not in {"TASE", "NASDAQ", "NYSE"}:
            raise RuntimeError("Invalid watchlist item")
        provider_symbol = f"{exchange}:{symbol}"
        if provider_symbol in seen:
            raise RuntimeError("WATCHLIST_JSON contains a duplicate symbol")
        seen.add(provider_symbol)
        stocks.append(
            Stock(
                name=name,
                symbol=symbol,
                exchange=exchange,
                currency_symbol=str(item.get("currency_symbol", "$" if exchange != "TASE" else "₪")),
                display_symbol=str(item.get("display_symbol", symbol)),
            )
        )
    return stocks


def is_monitoring_window(now: datetime) -> bool:
    local = now.astimezone(ISRAEL_TZ)
    return local.weekday() <= 4 and clock_time(10, 0) <= local.time().replace(tzinfo=None) <= clock_time(18, 0)


def tradingview_quotes(stocks: list[Stock], session: requests.Session) -> dict[str, Quote]:
    results: dict[str, Quote] = {}
    groups = {
        "israel": [stock for stock in stocks if stock.exchange == "TASE"],
        "america": [stock for stock in stocks if stock.exchange != "TASE"],
    }
    for market, group in groups.items():
        if not group:
            continue
        payload = {
            "symbols": {"tickers": [stock.provider_symbol for stock in group], "query": {"types": []}},
            "columns": ["close", "change_abs", "change"],
        }
        response = session.post(
            f"https://scanner.tradingview.com/{market}/scan",
            json=payload,
            headers={"User-Agent": "Mozilla/5.0 stock-monitor/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        stocks_by_provider = {stock.provider_symbol: stock for stock in group}
        for row in rows:
            stock = stocks_by_provider.get(row.get("s"))
            data = row.get("d") or []
            if stock is None or len(data) < 3 or any(value is None for value in data[:3]):
                continue
            results[stock.private_id] = Quote(stock, float(data[0]), float(data[1]), float(data[2]))
    return results


def yahoo_quote(stock: Stock, session: requests.Session) -> Quote | None:
    response = session.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{stock.yahoo_symbol}",
        params={"interval": "1m", "range": "1d"},
        headers={"User-Agent": "Mozilla/5.0 stock-monitor/1.0"},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose", meta.get("previousClose"))
    if price is None or previous in (None, 0):
        return None
    change = float(price) - float(previous)
    change_percent = change / float(previous) * 100
    return Quote(stock, float(price), change, change_percent)


def fetch_quotes(stocks: list[Stock]) -> list[Quote]:
    session = requests.Session()
    found: dict[str, Quote] = {}
    try:
        found.update(tradingview_quotes(stocks, session))
    except (requests.RequestException, ValueError, KeyError):
        pass

    missing = [stock for stock in stocks if stock.private_id not in found]
    for stock in missing:
        try:
            quote = yahoo_quote(stock, session)
            if quote:
                found[stock.private_id] = quote
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(0.15)
    return [found[stock.private_id] for stock in stocks if stock.private_id in found]


def signed(number: float, digits: int = 2) -> str:
    return f"{number:+.{digits}f}"


def direction_emoji(change: float) -> str:
    return "🟢" if change >= 0 else "🔴"


def format_header(now: datetime) -> str:
    local = now.astimezone(ISRAEL_TZ)
    weekday = HEBREW_WEEKDAYS[local.weekday()]
    month = HEBREW_MONTHS[local.month]
    return f"📈 <b>עדכון מניות הלקוחות</b> ({weekday}, {local.day} {month} {local.year}, {local:%H:%M})"


def format_report(quotes: Iterable[Quote], now: datetime) -> str:
    lines = [format_header(now), ""]
    for index, quote in enumerate(quotes, start=1):
        stock = quote.stock
        lines.append(
            f"{index}. <b>{html.escape(stock.name)}</b> "
            f"({html.escape(stock.display_symbol)}): {quote.price:.2f} {html.escape(stock.currency_symbol)} | "
            f"{direction_emoji(quote.change)} {signed(quote.change)} ({signed(quote.change_percent)}%)"
        )
    return "\n".join(lines)


def format_alert(quote: Quote, threshold: float, now: datetime) -> str:
    stock = quote.stock
    direction = "עולה" if quote.change_percent >= 0 else "יורדת"
    local = now.astimezone(ISRAEL_TZ)
    return "\n".join(
        [
            "🚨 <b>התראת מניה</b>",
            "",
            f"מניית <b>{html.escape(stock.name)}</b> ({html.escape(stock.display_symbol)}) {direction} ביותר מ-{threshold:.2f}%.",
            f"מחיר עדכני: {quote.price:.2f} {html.escape(stock.currency_symbol)}",
            f"שינוי יומי: {direction_emoji(quote.change)} {signed(quote.change)} ({signed(quote.change_percent)}%)",
            f"עודכן בשעה {local:%H:%M}",
        ]
    )


def send_telegram(token: str, chat_id: str, text: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()


def discover_chat_id(token: str, setup_code: str) -> str | None:
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"timeout": 0, "allowed_updates": json.dumps(["message"])},
        timeout=20,
    )
    response.raise_for_status()
    for update in reversed(response.json().get("result", [])):
        message = update.get("message") or {}
        text = str(message.get("text", "")).strip()
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", maxsplit=1)[0] if parts else ""
        supplied_code = parts[1].strip() if len(parts) == 2 else ""
        if command == "/connect" and hmac.compare_digest(supplied_code, setup_code):
            chat_id = (message.get("chat") or {}).get("id")
            return str(chat_id) if chat_id is not None else None
    return None


def empty_state(local_date: str) -> dict[str, Any]:
    return {"local_date": local_date, "reports_sent": [], "alerts": {}}


def load_state(key: str, local_date: str) -> dict[str, Any]:
    if not STATE_PATH.exists():
        return empty_state(local_date)
    try:
        decrypted = Fernet(key.encode("ascii")).decrypt(STATE_PATH.read_bytes())
        state = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Encrypted runtime state is invalid") from exc
    if state.get("local_date") != local_date:
        fresh = empty_state(local_date)
        if state.get("chat_id"):
            fresh["chat_id"] = state["chat_id"]
        return fresh
    return state


def save_state(key: str, state: dict[str, Any]) -> None:
    encrypted = Fernet(key.encode("ascii")).encrypt(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    STATE_PATH.write_bytes(encrypted)


def due_report_slots(now: datetime, sent: set[str]) -> list[str]:
    local = now.astimezone(ISRAEL_TZ)
    due: list[str] = []
    for report_time in REPORT_TIMES:
        slot = report_time.strftime("%H:%M")
        target = datetime.combine(local.date(), report_time, tzinfo=ISRAEL_TZ)
        if slot not in sent and timedelta(0) <= local - target <= REPORT_GRACE:
            due.append(slot)
    return due


def alert_direction(percent: float, threshold: float) -> str | None:
    if percent >= threshold:
        return "up"
    if percent <= -threshold:
        return "down"
    return None


def process_alerts(
    quotes: Iterable[Quote],
    threshold: float,
    state: dict[str, Any],
    now: datetime,
    sender,
) -> bool:
    changed = False
    alerts: dict[str, str] = state.setdefault("alerts", {})
    rearm_level = max(0.0, threshold - 0.25)
    for quote in quotes:
        private_id = quote.stock.private_id
        direction = alert_direction(quote.change_percent, threshold)
        previous_direction = alerts.get(private_id)
        if direction and direction != previous_direction:
            sender(format_alert(quote, threshold, now))
            alerts[private_id] = direction
            changed = True
        elif direction is None and previous_direction and abs(quote.change_percent) < rearm_level:
            del alerts[private_id]
            changed = True
    return changed


def main(now: datetime | None = None) -> int:
    now = now or datetime.now(tz=ISRAEL_TZ)
    token = required_env("TELEGRAM_BOT_TOKEN")
    encryption_key = state_encryption_key(token)
    local_date = now.astimezone(ISRAEL_TZ).date().isoformat()
    state = load_state(encryption_key, local_date)

    chat_id = optional_env("TELEGRAM_CHAT_ID") or str(state.get("chat_id", "")).strip()
    newly_connected = False
    if not chat_id:
        setup_code = required_env("TELEGRAM_SETUP_CODE")
        chat_id = discover_chat_id(token, setup_code) or ""
        if not chat_id:
            print("Waiting for the private Telegram connection command.")
            return 0
        state["chat_id"] = chat_id
        save_state(encryption_key, state)
        newly_connected = True
        send_telegram(
            token,
            chat_id,
            "✅ <b>הבוט חובר בהצלחה</b>\n\nהמעקב יפעל בימים שני עד שישי, בין 10:00 ל-18:00 לפי שעון ישראל.",
        )

    if not is_monitoring_window(now):
        print("Telegram connected." if newly_connected else "Outside configured monitoring window.")
        return 0

    watchlist = load_watchlist(required_env("WATCHLIST_JSON"))
    try:
        threshold = float(required_env("ALERT_THRESHOLD_PERCENT"))
    except ValueError as exc:
        raise RuntimeError("ALERT_THRESHOLD_PERCENT must be numeric") from exc
    if threshold <= 0:
        raise RuntimeError("ALERT_THRESHOLD_PERCENT must be positive")

    quotes = fetch_quotes(watchlist)
    if not quotes:
        raise RuntimeError("No market data was returned")

    state_changed = False
    sender = lambda message: send_telegram(token, chat_id, message)

    sent_slots = set(state.get("reports_sent", []))
    for slot in due_report_slots(now, sent_slots):
        sender(format_report(quotes, now))
        sent_slots.add(slot)
        state["reports_sent"] = sorted(sent_slots)
        state_changed = True

    state_changed = process_alerts(quotes, threshold, state, now, sender) or state_changed
    if state_changed or not STATE_PATH.exists():
        save_state(encryption_key, state)

    print(f"Monitoring run completed. Quotes received: {len(quotes)}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Monitoring run failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
