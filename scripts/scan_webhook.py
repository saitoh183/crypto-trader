#!/usr/bin/env python3
"""Lightweight HTTP webhook that exposes the paper scan to n8n.

Routes
------
GET  /health        -- liveness check, no auth required
POST /scan          -- run build_scan_result(), return JSON; requires X-Scan-Token
POST /scan-outcome  -- record n8n-side workflow failures; requires X-Scan-Token

Auth
----
Set SCAN_WEBHOOK_TOKEN in the environment (via .env.webhook loaded by the
systemd EnvironmentFile). Startup fails if the token is missing so the scan
endpoint is never accidentally exposed without auth. n8n sends the same token
in the X-Scan-Token header.

Concurrency
-----------
Single-threaded TCPServer prevents concurrent scan calls from racing on SQLite.

Side effects after a successful scan
-------------------------------------
- Calls write_report() to regenerate reports/latest.html.
- Overwrites team/last_scan.json in the coordination folder.
- Appends a one-liner to team/SCAN_LOG.md.
- Appends structured review rows to team/SCAN_OUTCOMES.jsonl.
"""

from __future__ import annotations

import http.server
import json
import os
import sqlite3
import socketserver
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

# WorkingDirectory in systemd service is the repo root.
# PYTHONPATH=src is set via Environment= so this import works.
from crypto_trader.config import get_settings
from crypto_trader.dashboard import write_report
from crypto_trader.scan import build_scan_result

PORT = int(os.getenv("SCAN_WEBHOOK_PORT", "8790"))
TOKEN = os.getenv("SCAN_WEBHOOK_TOKEN", "")
if not TOKEN:
    raise SystemExit("SCAN_WEBHOOK_TOKEN is required; refusing to start without webhook auth")
COORD_DIR = Path(os.getenv("CRYPTO_COORD_DIR", "/home/saitoh183/projects/crypto"))
CANDLE_STALE_MINUTES = int(os.getenv("CANDLE_STALE_MINUTES", "70"))
OUTCOME_REVIEW_HOURS = int(os.getenv("SCAN_OUTCOME_REVIEW_HOURS", "48"))


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _jsonl_records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _write_jsonl_record(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _is_duplicate_alert(path: Path, fingerprint: str, now: datetime) -> bool:
    if not fingerprint:
        return False
    cutoff = now - timedelta(hours=OUTCOME_REVIEW_HOURS)
    for record in reversed(_jsonl_records(path)):
        if record.get("alert_fingerprint") != fingerprint:
            continue
        seen_at = _parse_utc(record.get("recorded_at_utc") or record.get("generated_at_utc"))
        if seen_at is None or seen_at >= cutoff:
            return True
    return False


def _filled_orders(decisions: object) -> list[dict[str, object]]:
    if not isinstance(decisions, list):
        return []
    filled: list[dict[str, object]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        order = decision.get("paper_order") or decision.get("order")
        raw_signal = decision.get("signal")
        signal: dict[str, object] = raw_signal if isinstance(raw_signal, dict) else {}
        action = signal.get("action") or decision.get("action")
        if isinstance(order, dict) and order.get("status") == "FILLED" and action in {"BUY", "SELL"}:
            filled.append(decision)
    return filled


def _decision_action(decision: object) -> str:
    if not isinstance(decision, dict):
        return "?"
    raw_signal = decision.get("signal")
    signal: dict[str, object] = raw_signal if isinstance(raw_signal, dict) else {}
    action = signal.get("action") or decision.get("action") or "?"
    return str(action)


def _alert_fingerprint(outcome_type: str, decisions: object, error_message: str = "") -> str:
    if outcome_type == "buy_sell_alert":
        parts: list[str] = []
        for decision in _filled_orders(decisions):
            raw_order = decision.get("paper_order") or decision.get("order")
            order: dict[str, object] = raw_order if isinstance(raw_order, dict) else {}
            raw_signal = decision.get("signal")
            signal: dict[str, object] = raw_signal if isinstance(raw_signal, dict) else {}
            parts.append(
                "|".join(
                    [
                        str(decision.get("symbol", "?")),
                        str(signal.get("action") or decision.get("action") or order.get("side") or "?"),
                        str(order.get("quantity") or order.get("qty") or "?"),
                        str(order.get("price") or order.get("fill_price") or "?"),
                    ]
                )
            )
        return "paper_order|" + ";".join(sorted(parts))
    if outcome_type == "workflow_failure":
        compact_error = " ".join(error_message.split())[:200]
        return "workflow_failure|" + compact_error
    return ""


def _build_scan_outcome(result: dict[str, object], candle_age: float | None, now: datetime) -> dict[str, object]:
    decisions = result.get("decisions", [])
    decision_list = decisions if isinstance(decisions, list) else []
    filled = _filled_orders(decision_list)
    generated_at = str(result.get("generated_at_utc") or now.isoformat().replace("+00:00", "Z"))
    generated = _parse_utc(generated_at) or now
    is_hourly_heartbeat = generated.minute == 0

    if filled:
        outcome_type = "buy_sell_alert"
        notify_reason = "paper_order"
        posted_to_discord = True
    elif is_hourly_heartbeat:
        outcome_type = "heartbeat"
        notify_reason = "hourly_heartbeat"
        posted_to_discord = True
    else:
        outcome_type = "quiet_hold_no_post"
        notify_reason = "none"
        posted_to_discord = False

    actions = [_decision_action(decision) for decision in decision_list]
    fingerprint = _alert_fingerprint(outcome_type, decision_list)
    outcome_log_path = COORD_DIR / "team" / "SCAN_OUTCOMES.jsonl"
    duplicate = _is_duplicate_alert(outcome_log_path, fingerprint, now)
    candle_age_value = round(candle_age, 1) if candle_age is not None else None

    return {
        "recorded_at_utc": now.isoformat().replace("+00:00", "Z"),
        "run_started_at_utc": now.isoformat().replace("+00:00", "Z"),
        "generated_at_utc": generated_at,
        "workflow_id": "bNig1L0nrUQ0YzAP",
        "workflow_name": "Crypto Paper Scan - Discord Alerts",
        "execution_id": "unavailable_from_scan_webhook",
        "mode": result.get("mode", "paper"),
        "outcome_type": outcome_type,
        "notify_reason": notify_reason,
        "posted_to_discord": posted_to_discord,
        "duplicate_alert_candidate": duplicate,
        "alert_fingerprint": fingerprint,
        "decision_count": len(decision_list),
        "hold_count": sum(1 for action in actions if action == "HOLD"),
        "filled_order_count": len(filled),
        "candle_freshness": result.get("candle_freshness", "unknown"),
        "candle_age_minutes": candle_age_value,
        "summary": " ".join(actions),
        "error_message": "",
    }


def _build_failure_outcome(payload: dict[str, object], now: datetime) -> dict[str, object]:
    error_message = str(
        payload.get("error_message")
        or payload.get("status_message")
        or payload.get("message")
        or "unknown workflow failure"
    )
    fingerprint = _alert_fingerprint("workflow_failure", [], error_message)
    outcome_log_path = COORD_DIR / "team" / "SCAN_OUTCOMES.jsonl"
    return {
        "recorded_at_utc": now.isoformat().replace("+00:00", "Z"),
        "run_started_at_utc": str(payload.get("run_started_at_utc") or now.isoformat().replace("+00:00", "Z")),
        "generated_at_utc": str(payload.get("generated_at_utc") or now.isoformat().replace("+00:00", "Z")),
        "workflow_id": str(payload.get("workflow_id") or "bNig1L0nrUQ0YzAP"),
        "workflow_name": str(payload.get("workflow_name") or "Crypto Paper Scan - Discord Alerts"),
        "execution_id": str(payload.get("execution_id") or "unknown"),
        "mode": "paper",
        "outcome_type": "workflow_failure",
        "notify_reason": "scan_failure",
        "posted_to_discord": True,
        "duplicate_alert_candidate": _is_duplicate_alert(outcome_log_path, fingerprint, now),
        "alert_fingerprint": fingerprint,
        "decision_count": 0,
        "hold_count": 0,
        "filled_order_count": 0,
        "candle_freshness": "unknown",
        "candle_age_minutes": None,
        "summary": "workflow failure before decision processing",
        "error_message": error_message[:1000],
    }


def _candle_age_minutes(database_path: Path) -> float | None:
    try:
        conn = sqlite3.connect(database_path)
        row = conn.execute("SELECT MAX(open_time_utc) FROM candles").fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        latest = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        return (datetime.now(UTC) - latest).total_seconds() / 60
    except Exception:
        return None


def _candle_freshness(age: float | None) -> str:
    if age is None:
        return "no_data"
    if age > CANDLE_STALE_MINUTES:
        return "stale"
    return "ok"


def _write_coordination_artifacts(result: dict[str, object], candle_age: float | None, now: datetime) -> None:
    team_dir = COORD_DIR / "team"
    team_dir.mkdir(parents=True, exist_ok=True)

    outcome = _build_scan_outcome(result, candle_age, now)
    _write_jsonl_record(team_dir / "SCAN_OUTCOMES.jsonl", outcome)
    result["scan_outcome"] = outcome

    last_scan_path = team_dir / "last_scan.json"
    last_scan_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    decisions = result.get("decisions", [])
    signal_parts = []
    order_parts = []
    for d in decisions:  # type: ignore[union-attr]
        sig = d.get("signal", {})
        action = sig.get("action", "?")
        conf = sig.get("confidence", 0)
        symbol = d.get("symbol", "?")
        signal_parts.append(f"{symbol}:{action}({conf:.2f})")
        order = d.get("paper_order")
        if order and order.get("status") == "FILLED":
            pnl = order.get("realized_pnl_usdt", 0)
            order_parts.append(f"{symbol} {order.get('side')} FILLED pnl={pnl:+.2f}")

    paper = result.get("paper_account", {})
    equity = paper.get("equity_usdt", "?")
    ts = result.get("generated_at_utc", "?")
    age_str = f"{candle_age:.0f}min" if candle_age is not None else "unknown"
    orders_str = ", ".join(order_parts) if order_parts else "none"
    line = (
        f"{ts} | {' '.join(signal_parts)} | orders: {orders_str} "
        f"| equity: {equity} USDT | candle_age: {age_str}\n"
    )

    scan_log_path = team_dir / "SCAN_LOG.md"
    with scan_log_path.open("a", encoding="utf-8") as f:
        f.write(line)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        ts = datetime.now(UTC).replace(microsecond=0).isoformat()
        print(f"{ts} {self.address_string()} {format % args}", flush=True)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        return self.headers.get("X-Scan-Token", "") == TOKEN

    def _read_json_body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw_body = self.rfile.read(length)
        parsed = json.loads(raw_body.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("JSON body must be an object")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "mode": "paper"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in {"/scan", "/scan-outcome"}:
            self._send_json(404, {"error": "not found"})
            return

        if not self._check_auth():
            self._send_json(403, {"error": "forbidden"})
            return

        if self.path == "/scan-outcome":
            try:
                now = _utc_now()
                payload = self._read_json_body()
                outcome = _build_failure_outcome(payload, now)
                _write_jsonl_record(COORD_DIR / "team" / "SCAN_OUTCOMES.jsonl", outcome)
                response = dict(payload)
                response["recorded"] = True
                response["scan_outcome"] = outcome
                self._send_json(200, response)
            except Exception as exc:
                self._send_json(
                    500,
                    {
                        "mode": "paper",
                        "status": "error",
                        "error": str(exc),
                        "detail": traceback.format_exc(limit=10),
                    },
                )
            return

        try:
            now = _utc_now()
            settings = get_settings()
            candle_age = _candle_age_minutes(settings.database_path)
            result = build_scan_result(settings)
            result["candle_age_minutes"] = round(candle_age, 1) if candle_age is not None else None
            result["candle_freshness"] = _candle_freshness(candle_age)

            try:
                write_report()
            except Exception:
                pass

            try:
                _write_coordination_artifacts(result, candle_age, now)
            except Exception:
                pass

            self._send_json(200, result)

        except Exception as exc:
            self._send_json(
                500,
                {
                    "mode": "paper",
                    "status": "error",
                    "error": str(exc),
                    "detail": traceback.format_exc(limit=10),
                },
            )


class _Server(socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    print(f"crypto-scan-webhook starting on 0.0.0.0:{PORT} (paper trading only)", flush=True)
    print(f"auth: {'enabled' if TOKEN else 'disabled (no token set)'}", flush=True)
    with _Server(("0.0.0.0", PORT), _Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
