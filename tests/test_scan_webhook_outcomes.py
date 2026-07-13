import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def load_scan_webhook(tmp_path: Path):
    os.environ["SCAN_WEBHOOK_TOKEN"] = "test-token"
    os.environ["CRYPTO_COORD_DIR"] = str(tmp_path)
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "scan_webhook.py"
    spec = importlib.util.spec_from_file_location("scan_webhook_for_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decision(symbol: str, action: str, *, filled: bool = False) -> dict[str, Any]:
    order = None
    if filled:
        order = {
            "status": "FILLED",
            "side": action,
            "quantity": 1.25,
            "price": 100.0,
        }
    return {
        "symbol": symbol,
        "signal": {"action": action, "confidence": 0.75 if filled else 0.25},
        "paper_order": order,
    }


def test_scan_outcome_categorizes_quiet_hold_and_heartbeat(tmp_path):
    module = load_scan_webhook(tmp_path)
    now = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    quiet = module._build_scan_outcome(
        {
            "mode": "paper",
            "generated_at_utc": "2026-01-01T00:15:00Z",
            "candle_freshness": "ok",
            "decisions": [decision("BTCUSDT", "HOLD")],
        },
        15.2,
        now,
    )
    heartbeat = module._build_scan_outcome(
        {
            "mode": "paper",
            "generated_at_utc": "2026-01-01T01:00:00Z",
            "candle_freshness": "ok",
            "decisions": [decision("BTCUSDT", "HOLD")],
        },
        45.0,
        now,
    )

    assert quiet["outcome_type"] == "quiet_hold_no_post"
    assert quiet["posted_to_discord"] is False
    assert quiet["notify_reason"] == "none"
    assert heartbeat["outcome_type"] == "heartbeat"
    assert heartbeat["posted_to_discord"] is True
    assert heartbeat["notify_reason"] == "hourly_heartbeat"


def test_scan_outcome_flags_duplicate_filled_alerts(tmp_path):
    module = load_scan_webhook(tmp_path)
    now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    result = {
        "mode": "paper",
        "generated_at_utc": "2026-01-01T02:00:00Z",
        "candle_freshness": "ok",
        "decisions": [decision("BTCUSDT", "BUY", filled=True)],
    }

    first = module._build_scan_outcome(result, 10.0, now)
    module._write_jsonl_record(tmp_path / "team" / "SCAN_OUTCOMES.jsonl", first)
    second = module._build_scan_outcome(result, 10.0, now + timedelta(minutes=15))

    assert first["outcome_type"] == "buy_sell_alert"
    assert first["posted_to_discord"] is True
    assert first["duplicate_alert_candidate"] is False
    assert second["duplicate_alert_candidate"] is True
    assert second["alert_fingerprint"] == first["alert_fingerprint"]


def test_failure_outcome_is_visible_and_posted(tmp_path):
    module = load_scan_webhook(tmp_path)
    now = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    outcome = module._build_failure_outcome(
        {
            "workflow_id": "bNig1L0nrUQ0YzAP",
            "execution_id": "123",
            "error_message": "HTTP 500 from scan webhook",
        },
        now,
    )

    assert outcome["outcome_type"] == "workflow_failure"
    assert outcome["notify_reason"] == "scan_failure"
    assert outcome["posted_to_discord"] is True
    assert outcome["execution_id"] == "123"
    assert "HTTP 500" in outcome["error_message"]
