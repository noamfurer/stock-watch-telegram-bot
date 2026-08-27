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
BACKUP_SKIP_WINDOW = timedelta(minutes=10)

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


def env_flag(name: str) -> bool:
    return optional_env(name).lower() in {"1", "true", "yes", "on"}


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
            f"🛎️ <b>{html.escape(stock.name)} - התראת מניה</b>",
            "",
            f"מניית <b>{html.escape(stock.name)}</b> ({html.escape(stock.display_symbol)}) {direction} ביותר מ-{threshold:.2f}%.",
            f"מחיר עדכני: {quote.price:.2f} {html.escape(stock.currency_symbol)}",
            f"שינוי יומי: {direction_emoji(quote.change)} {signed(quote.change)} ({signed(quote.change_percent)}%)",
            f"עודכן בשעה {local:%H:%M}",
        ]
    )


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def answer_callback(token: str, callback_id: str, text: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text},
        timeout=20,
    )
    response.raise_for_status()


def get_telegram_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "timeout": 0,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return list(response.json().get("result", []))


def discover_chat_id(token: str, setup_code: str) -> str | None:
    for update in reversed(get_telegram_updates(token, None)):
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
    return {
        "local_date": local_date,
        "reports_sent": [],
        "alerts": {},
        "subscribers": {},
        "update_offset": None,
        "snapshot_requests": [],
    }


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
        for key in ("chat_id", "admin_chat_id", "subscribers", "update_offset", "snapshot_requests"):
            if state.get(key) is not None:
                fresh[key] = state[key]
        return fresh
    return state


def save_state(key: str, state: dict[str, Any]) -> None:
    encrypted = Fernet(key.encode("ascii")).encrypt(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    STATE_PATH.write_bytes(encrypted)


def subscriber_from_user(user: dict[str, Any], chat_id: str, now: datetime) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "username": str(user.get("username") or ""),
        "first_name": str(user.get("first_name") or ""),
        "last_name": str(user.get("last_name") or ""),
        "enabled": True,
        "joined_at": now.astimezone(ISRAEL_TZ).isoformat(timespec="seconds"),
    }


def ensure_multiuser_state(state: dict[str, Any], now: datetime) -> bool:
    changed = False
    legacy_chat_id = str(state.pop("chat_id", "") or "").strip()
    admin_chat_id = str(state.get("admin_chat_id", "") or legacy_chat_id).strip()
    if admin_chat_id and state.get("admin_chat_id") != admin_chat_id:
        state["admin_chat_id"] = admin_chat_id
        changed = True

    subscribers = state.setdefault("subscribers", {})
    if admin_chat_id and admin_chat_id not in subscribers:
        subscribers[admin_chat_id] = {
            "chat_id": admin_chat_id,
            "username": "",
            "first_name": "מנהל המערכת",
            "last_name": "",
            "enabled": True,
            "joined_at": now.astimezone(ISRAEL_TZ).isoformat(timespec="seconds"),
        }
        changed = True
    if "update_offset" not in state:
        state["update_offset"] = None
        changed = True
    if "snapshot_requests" not in state:
        state["snapshot_requests"] = []
        changed = True
    return changed


def subscriber_label(subscriber: dict[str, Any]) -> str:
    full_name = " ".join(
        part for part in (str(subscriber.get("first_name", "")).strip(), str(subscriber.get("last_name", "")).strip()) if part
    )
    username = str(subscriber.get("username", "")).strip()
    if username:
        return f"{full_name or 'ללא שם'} (@{username})"
    return full_name or "משתמש ללא שם"


def permission_keyboard(chat_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ YES", "callback_data": f"subscriber:yes:{chat_id}"},
                {"text": "🚫 NO", "callback_data": f"subscriber:no:{chat_id}"},
            ]
        ]
    }


def register_subscriber(
    state: dict[str, Any],
    message: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any] | None, bool, bool]:
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    if chat.get("type") != "private" or chat.get("id") is None:
        return None, False, False
    chat_id = str(chat["id"])
    subscribers = state.setdefault("subscribers", {})
    existing = subscribers.get(chat_id)
    is_new = existing is None
    if is_new:
        subscribers[chat_id] = subscriber_from_user(user, chat_id, now)
        return subscribers[chat_id], True, True

    changed = False
    for key in ("username", "first_name", "last_name"):
        new_value = str(user.get(key) or "")
        if existing.get(key) != new_value:
            existing[key] = new_value
            changed = True
    return existing, False, changed


def set_subscriber_enabled(state: dict[str, Any], chat_id: str, enabled: bool) -> bool:
    subscriber = state.setdefault("subscribers", {}).get(chat_id)
    if subscriber is None or bool(subscriber.get("enabled", True)) == enabled:
        return False
    subscriber["enabled"] = enabled
    return True


def active_chat_ids(state: dict[str, Any]) -> list[str]:
    return [
        str(chat_id)
        for chat_id, subscriber in state.get("subscribers", {}).items()
        if bool(subscriber.get("enabled", True))
    ]


def queue_snapshot_request(state: dict[str, Any], chat_id: str) -> bool:
    requests_queue = state.setdefault("snapshot_requests", [])
    if chat_id in requests_queue:
        return False
    requests_queue.append(chat_id)
    return True


def normalize_snapshot_requests(state: dict[str, Any]) -> bool:
    active = set(active_chat_ids(state))
    original = [str(chat_id) for chat_id in state.setdefault("snapshot_requests", [])]
    normalized = list(dict.fromkeys(chat_id for chat_id in original if chat_id in active))
    if normalized == original:
        return False
    state["snapshot_requests"] = normalized
    return True


def fulfill_snapshot_requests(token: str, state: dict[str, Any], text: str) -> bool:
    requested = [str(chat_id) for chat_id in state.get("snapshot_requests", [])]
    if not requested:
        return False

    remaining: list[str] = []
    changed = False
    for chat_id in requested:
        subscriber = state.get("subscribers", {}).get(chat_id)
        if subscriber is None or not bool(subscriber.get("enabled", True)):
            changed = True
            continue
        try:
            send_telegram(token, chat_id, text)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {400, 403}:
                changed = set_subscriber_enabled(state, chat_id, False) or changed
            else:
                remaining.append(chat_id)
        except requests.RequestException:
            remaining.append(chat_id)
        else:
            changed = True

    if remaining != requested:
        state["snapshot_requests"] = remaining
        changed = True
    return changed


def users_message(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    subscribers = state.get("subscribers", {})
    lines = ["👥 <b>רשימת מנויים</b>", ""]
    keyboard: list[list[dict[str, str]]] = []
    ordered = sorted(subscribers.items(), key=lambda item: str(item[1].get("joined_at", "")))
    for index, (chat_id, subscriber) in enumerate(ordered, start=1):
        enabled = bool(subscriber.get("enabled", True))
        status = "✅ YES" if enabled else "🚫 NO"
        lines.append(
            f"{index}. {html.escape(subscriber_label(subscriber))} | <b>{status}</b> | <code>{html.escape(str(chat_id))}</code>"
        )
        keyboard.append(
            [
                {"text": f"{index} ✅ YES", "callback_data": f"subscriber:yes:{chat_id}"},
                {"text": f"{index} 🚫 NO", "callback_data": f"subscriber:no:{chat_id}"},
            ]
        )
    if not ordered:
        lines.append("אין עדיין מנויים.")
    return "\n".join(lines), {"inline_keyboard": keyboard}


def process_telegram_updates(token: str, state: dict[str, Any], now: datetime) -> bool:
    offset_value = state.get("update_offset")
    offset = int(offset_value) if offset_value is not None else None
    updates = get_telegram_updates(token, offset)
    if not updates:
        return False

    changed = False
    admin_chat_id = str(state.get("admin_chat_id", ""))
    for update in updates:
        update_id = int(update.get("update_id", 0))
        state["update_offset"] = max(int(state.get("update_offset") or 0), update_id + 1)
        changed = True

        message = update.get("message") or {}
        text = str(message.get("text", "")).strip()
        command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0] if text else ""

        if command == "/start":
            subscriber, is_new, profile_changed = register_subscriber(state, message, now)
            changed = profile_changed or changed
            if subscriber is None:
                continue
            chat_id = str(subscriber["chat_id"])
            enabled = bool(subscriber.get("enabled", True))
            if is_new:
                send_telegram(
                    token,
                    chat_id,
                    "✅ <b>הצטרפת לעדכוני המניות</b>\n\nתקבל כאן את הדוחות וההתראות. לקבלת תמונת מצב מלאה לפי בקשה, אפשר לכתוב <b>עכשיו</b>. מנהל המערכת רשאי לאשר או לחסום את קבלת ההודעות.",
                )
                if admin_chat_id and chat_id != admin_chat_id:
                    send_telegram(
                        token,
                        admin_chat_id,
                        "👤 <b>מצטרף חדש לבוט</b>\n\n"
                        f"{html.escape(subscriber_label(subscriber))}\n"
                        f"מזהה: <code>{html.escape(chat_id)}</code>\n"
                        "סטטוס התחלתי: <b>YES</b>",
                        permission_keyboard(chat_id),
                    )
            else:
                status = "פעילה" if enabled else "חסומה"
                send_telegram(token, chat_id, f"ℹ️ ההרשמה שלך כבר קיימת. קבלת ההודעות כרגע <b>{status}</b>.")

        elif command == "/users" and str((message.get("chat") or {}).get("id", "")) == admin_chat_id:
            users_text, keyboard = users_message(state)
            send_telegram(token, admin_chat_id, users_text, keyboard)

        elif text == "עכשיו" or command == "/now":
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if chat.get("type") != "private" or not chat_id:
                continue
            subscriber = state.get("subscribers", {}).get(chat_id)
            if subscriber is None:
                send_telegram(token, chat_id, "כדי לקבל תמונת מצב, יש ללחוץ תחילה על <b>Start</b>.")
            elif not bool(subscriber.get("enabled", True)):
                send_telegram(token, chat_id, "קבלת עדכוני המניות שלך חסומה כרגע.")
            else:
                changed = queue_snapshot_request(state, chat_id) or changed

        callback = update.get("callback_query") or {}
        if callback:
            callback_id = str(callback.get("id", ""))
            actor_id = str((callback.get("from") or {}).get("id", ""))
            data = str(callback.get("data", ""))
            parts = data.split(":", maxsplit=2)
            if actor_id != admin_chat_id or len(parts) != 3 or parts[0] != "subscriber":
                if callback_id:
                    answer_callback(token, callback_id, "אין הרשאה לפעולה")
                continue
            enabled = parts[1] == "yes"
            target_chat_id = parts[2]
            if parts[1] not in {"yes", "no"} or target_chat_id not in state.get("subscribers", {}):
                answer_callback(token, callback_id, "המשתמש לא נמצא")
                continue
            changed = set_subscriber_enabled(state, target_chat_id, enabled) or changed
            answer_callback(token, callback_id, "ההרשאה עודכנה")
            subscriber = state["subscribers"][target_chat_id]
            status = "YES" if enabled else "NO"
            send_telegram(
                token,
                admin_chat_id,
                f"עודכן: {html.escape(subscriber_label(subscriber))} מסומן כעת <b>{status}</b>.",
            )
            if target_chat_id != admin_chat_id:
                user_status = "הופעלה" if enabled else "הושהתה"
                try:
                    send_telegram(token, target_chat_id, f"ℹ️ קבלת עדכוני המניות {user_status} על ידי מנהל המערכת.")
                except requests.RequestException:
                    pass
    return changed


def broadcast_telegram(token: str, state: dict[str, Any], text: str) -> bool:
    changed = False
    for chat_id in active_chat_ids(state):
        try:
            send_telegram(token, chat_id, text)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in {400, 403}:
                changed = set_subscriber_enabled(state, chat_id, False) or changed
        except requests.RequestException:
            continue
    return changed


def due_report_slots(now: datetime, sent: set[str]) -> list[str]:
    local = now.astimezone(ISRAEL_TZ)
    due: list[str] = []
    for report_time in REPORT_TIMES:
        slot = report_time.strftime("%H:%M")
        target = datetime.combine(local.date(), report_time, tzinfo=ISRAEL_TZ)
        if slot not in sent and timedelta(0) <= local - target <= REPORT_GRACE:
            due.append(slot)
    return due


def has_recent_successful_check(
    state: dict[str, Any],
    now: datetime,
    window: timedelta = BACKUP_SKIP_WINDOW,
) -> bool:
    raw = str(state.get("last_successful_market_check", "")).strip()
    if not raw:
        return False
    try:
        last_check = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if last_check.tzinfo is None:
        last_check = last_check.replace(tzinfo=ISRAEL_TZ)
    elapsed = now.astimezone(ISRAEL_TZ) - last_check.astimezone(ISRAEL_TZ)
    return timedelta(0) <= elapsed < window


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

    admin_chat_id = (
        optional_env("TELEGRAM_CHAT_ID")
        or str(state.get("admin_chat_id", "")).strip()
        or str(state.get("chat_id", "")).strip()
    )
    newly_connected = False
    if not admin_chat_id:
        setup_code = required_env("TELEGRAM_SETUP_CODE")
        admin_chat_id = discover_chat_id(token, setup_code) or ""
        if not admin_chat_id:
            print("Waiting for the private Telegram connection command.")
            return 0
        state["admin_chat_id"] = admin_chat_id
        newly_connected = True
        send_telegram(
            token,
            admin_chat_id,
            "✅ <b>הבוט חובר בהצלחה</b>\n\nהמעקב יפעל בימים שני עד שישי, בין 10:00 ל-18:00 לפי שעון ישראל.",
        )

    state_changed = ensure_multiuser_state(state, now)
    state_changed = process_telegram_updates(token, state, now) or state_changed
    state_changed = normalize_snapshot_requests(state) or state_changed
    if state_changed or not STATE_PATH.exists():
        save_state(encryption_key, state)

    snapshot_requested = bool(state.get("snapshot_requests"))
    monitoring_window = is_monitoring_window(now)
    if not monitoring_window and not snapshot_requested:
        print("Telegram connected." if newly_connected else "Outside configured monitoring window.")
        return 0

    if not active_chat_ids(state):
        print("No enabled Telegram subscribers.")
        return 0

    if (
        not env_flag("FORCE_MONITOR_RUN")
        and not snapshot_requested
        and has_recent_successful_check(state, now)
    ):
        print("Recent successful market check found. Backup run skipped.")
        return 0

    watchlist = load_watchlist(required_env("WATCHLIST_JSON"))
    threshold: float | None = None
    if monitoring_window:
        try:
            threshold = float(required_env("ALERT_THRESHOLD_PERCENT"))
        except ValueError as exc:
            raise RuntimeError("ALERT_THRESHOLD_PERCENT must be numeric") from exc
        if threshold <= 0:
            raise RuntimeError("ALERT_THRESHOLD_PERCENT must be positive")

    quotes = fetch_quotes(watchlist)
    if not quotes:
        raise RuntimeError("No market data was returned")

    delivery_changes: list[bool] = []

    def sender(message: str) -> None:
        delivery_changes.append(broadcast_telegram(token, state, message))

    if snapshot_requested:
        state_changed = fulfill_snapshot_requests(token, state, format_report(quotes, now)) or state_changed

    if not monitoring_window:
        if state_changed or not STATE_PATH.exists():
            save_state(encryption_key, state)
        print(f"On-demand snapshot completed. Quotes received: {len(quotes)}.")
        return 0

    sent_slots = set(state.get("reports_sent", []))
    for slot in due_report_slots(now, sent_slots):
        sender(format_report(quotes, now))
        sent_slots.add(slot)
        state["reports_sent"] = sorted(sent_slots)
        state_changed = True

    if threshold is None:
        raise RuntimeError("Alert threshold was not initialized")
    state_changed = process_alerts(quotes, threshold, state, now, sender) or state_changed
    state_changed = any(delivery_changes) or state_changed
    state["last_successful_market_check"] = now.astimezone(ISRAEL_TZ).isoformat(timespec="seconds")
    state_changed = True
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
