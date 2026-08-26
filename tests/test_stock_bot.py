from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

import stock_bot
from stock_bot import Quote, Stock


TZ = ZoneInfo("Asia/Jerusalem")


def sample_stock() -> Stock:
    return Stock("חברה לדוגמה", "DEMO", "TASE", "₪", "DEMO")


def test_derived_state_key_is_valid_and_stable(monkeypatch) -> None:
    monkeypatch.delenv("STATE_ENCRYPTION_KEY", raising=False)
    first = stock_bot.state_encryption_key("telegram-token")
    second = stock_bot.state_encryption_key("telegram-token")
    assert first == second
    Fernet(first)


def test_monitoring_window() -> None:
    assert stock_bot.is_monitoring_window(datetime(2026, 8, 26, 10, 0, tzinfo=TZ))
    assert stock_bot.is_monitoring_window(datetime(2026, 8, 28, 18, 0, tzinfo=TZ))
    assert not stock_bot.is_monitoring_window(datetime(2026, 8, 29, 12, 0, tzinfo=TZ))
    assert not stock_bot.is_monitoring_window(datetime(2026, 8, 26, 9, 59, tzinfo=TZ))


def test_due_report_is_sent_once() -> None:
    now = datetime(2026, 8, 26, 11, 17, tzinfo=TZ)
    assert stock_bot.due_report_slots(now, set()) == ["11:00"]
    assert stock_bot.due_report_slots(now, {"11:00"}) == []


def test_report_matches_requested_shape() -> None:
    quote = Quote(sample_stock(), 23010.0, -170.0, -0.73)
    report = stock_bot.format_report([quote], datetime(2026, 8, 26, 11, 0, tzinfo=TZ))
    assert "עדכון מניות הלקוחות" in report
    assert "1. <b>חברה לדוגמה</b> (DEMO): 23010.00 ₪ | 🔴 -170.00 (-0.73%)" in report


def test_alert_only_on_crossing_and_rearms() -> None:
    messages: list[str] = []
    state = {"alerts": {}}
    now = datetime(2026, 8, 26, 12, 0, tzinfo=TZ)
    stock = sample_stock()

    assert stock_bot.process_alerts([Quote(stock, 104.0, 4.0, 4.0)], 4.0, state, now, messages.append)
    assert len(messages) == 1
    assert not stock_bot.process_alerts([Quote(stock, 105.0, 5.0, 5.0)], 4.0, state, now, messages.append)
    assert len(messages) == 1
    assert stock_bot.process_alerts([Quote(stock, 103.0, 3.0, 3.0)], 4.0, state, now, messages.append)
    assert stock_bot.process_alerts([Quote(stock, 96.0, -4.0, -4.0)], 4.0, state, now, messages.append)
    assert len(messages) == 2


def test_state_is_encrypted(tmp_path: Path, monkeypatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(stock_bot, "STATE_PATH", tmp_path / "runtime_state.enc")
    state = {"local_date": "2026-08-26", "reports_sent": ["11:00"], "alerts": {"private": "up"}}
    stock_bot.save_state(key, state)
    raw = stock_bot.STATE_PATH.read_bytes()
    assert b"reports_sent" not in raw
    assert stock_bot.load_state(key, "2026-08-26") == state


def test_chat_id_survives_daily_state_reset(tmp_path: Path, monkeypatch) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(stock_bot, "STATE_PATH", tmp_path / "runtime_state.enc")
    state = {"local_date": "2026-08-26", "reports_sent": ["11:00"], "alerts": {}, "chat_id": "12345"}
    stock_bot.save_state(key, state)
    loaded = stock_bot.load_state(key, "2026-08-27")
    assert loaded == {
        "local_date": "2026-08-27",
        "reports_sent": [],
        "alerts": {},
        "subscribers": {},
        "update_offset": None,
        "chat_id": "12345",
    }


def test_legacy_admin_is_migrated_to_encrypted_subscriber_list() -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=TZ)
    state = {"chat_id": "12345", "subscribers": {}}
    assert stock_bot.ensure_multiuser_state(state, now)
    assert state["admin_chat_id"] == "12345"
    assert state["subscribers"]["12345"]["enabled"] is True
    assert "chat_id" not in state


def test_new_subscriber_starts_enabled_and_block_cannot_be_bypassed() -> None:
    now = datetime(2026, 8, 26, 12, 0, tzinfo=TZ)
    state = {"subscribers": {}}
    message = {
        "chat": {"id": 98765, "type": "private"},
        "from": {"id": 98765, "username": "new_user", "first_name": "New", "last_name": "User"},
    }
    subscriber, is_new, changed = stock_bot.register_subscriber(state, message, now)
    assert is_new and changed
    assert subscriber and subscriber["enabled"] is True
    assert stock_bot.set_subscriber_enabled(state, "98765", False)

    subscriber, is_new, changed = stock_bot.register_subscriber(state, message, now)
    assert not is_new and not changed
    assert subscriber and subscriber["enabled"] is False
    assert stock_bot.active_chat_ids(state) == []


def test_admin_user_list_contains_yes_no_controls() -> None:
    state = {
        "subscribers": {
            "98765": {
                "chat_id": "98765",
                "username": "new_user",
                "first_name": "New",
                "last_name": "User",
                "enabled": True,
                "joined_at": "2026-08-26T12:00:00+03:00",
            }
        }
    }
    message, markup = stock_bot.users_message(state)
    assert "@new_user" in message
    assert "YES" in message
    buttons = markup["inline_keyboard"][0]
    assert buttons[0]["callback_data"] == "subscriber:yes:98765"
    assert buttons[1]["callback_data"] == "subscriber:no:98765"
