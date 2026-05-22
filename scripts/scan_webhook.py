#!/usr/bin/env python3
"""Lightweight HTTP webhook that exposes the paper scan to n8n.

Routes
------
GET  /health  -- liveness check, no auth required
POST /scan    -- run build_scan_result(), return JSON; requires X-Scan-Token

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
"""

from __future__ import annotations

import http.server
import json
import os
import sqlite3
import socketserver
import traceback
from datetime import UTC, datetime
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


def _write_coordination_artifacts(result: dict[str, object], candle_age: float | None) -> None:
    team_dir = COORD_DIR / "team"
    team_dir.mkdir(parents=True, exist_ok=True)

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

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "mode": "paper"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/scan":
            self._send_json(404, {"error": "not found"})
            return

        if not self._check_auth():
            self._send_json(403, {"error": "forbidden"})
            return

        try:
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
                _write_coordination_artifacts(result, candle_age)
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
