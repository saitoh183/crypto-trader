from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from typing import cast

from .collector import Candle
from .config import Settings, get_settings
from .db import connect, init_db
from .paper import PaperAccount, PaperOrder, execute_paper_order
from .risk import RiskState, evaluate_risk
from .strategy import TradeSignal, generate_signal


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_recent_candles(conn: sqlite3.Connection, symbol: str, interval: str, limit: int = 120) -> list[Candle]:
    rows = conn.execute(
        """
        SELECT exchange, symbol, interval, open_time_utc, open, high, low, close, volume
        FROM candles
        WHERE exchange = 'binance' AND symbol = ? AND interval = ?
        ORDER BY open_time_utc DESC
        LIMIT ?
        """,
        (symbol, interval, limit),
    ).fetchall()
    return [
        Candle(
            exchange=row["exchange"],
            symbol=row["symbol"],
            interval=row["interval"],
            open_time_utc=row["open_time_utc"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in reversed(rows)
    ]


def save_trade_signal(conn: sqlite3.Connection, generated_at: str, signal: TradeSignal) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO trade_signals
        (generated_at_utc, symbol, interval, action, confidence, price, reason, inputs_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at,
            signal.symbol,
            signal.timeframe,
            signal.action,
            signal.confidence,
            signal.price,
            signal.reason,
            json.dumps(signal.inputs, sort_keys=True),
        ),
    )


def save_paper_order(conn: sqlite3.Connection, generated_at: str, order: PaperOrder) -> None:
    order_data = order.as_dict()
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_orders
        (generated_at_utc, symbol, side, quantity, price, notional_usdt, fee_usdt, status, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated_at,
            order_data["symbol"],
            order_data["side"],
            order_data["quantity"],
            order_data["price"],
            order_data["notional_usdt"],
            order_data["fee_usdt"],
            order_data["status"],
            order_data["reason"],
        ),
    )


def build_scan_result(settings: Settings) -> dict[str, object]:
    conn = connect(settings.database_path)
    init_db(conn)
    generated_at = utc_now_iso()
    account = PaperAccount(cash_usdt=settings.paper_balance_usdt)
    decisions: list[dict[str, object]] = []

    for symbol in settings.symbols:
        candles = load_recent_candles(conn, symbol, settings.interval, settings.candle_limit)
        signal = generate_signal(symbol, settings.interval, candles)
        save_trade_signal(conn, generated_at, signal)
        risk_state = RiskState(equity_usdt=settings.paper_balance_usdt, open_positions=len(account.positions))
        risk = evaluate_risk(signal, settings, risk_state)
        order = None
        if risk.allowed:
            order = execute_paper_order(account, signal, risk.max_position_usdt)
            save_paper_order(conn, generated_at, order)

        decisions.append(
            {
                "symbol": symbol,
                "timeframe": settings.interval,
                "signal": signal.as_dict(),
                "risk": risk.as_dict(),
                "paper_order": order.as_dict() if order else None,
            }
        )

    conn.commit()
    conn.close()
    return {
        "generated_at_utc": generated_at,
        "mode": "paper",
        "quote_currency": "USDT",
        "paper_balance_usdt": settings.paper_balance_usdt,
        "risk_profile": settings.risk_profile,
        "decisions": decisions,
        "paper_account": {
            "cash_usdt": account.cash_usdt,
            "positions": {symbol: position.as_dict() for symbol, position in account.positions.items()},
        },
        "disclaimer": "Paper trading only. No live exchange order path is enabled.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate NovaAI-ready crypto paper trading scan")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    result = build_scan_result(get_settings())
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        decisions = cast(list[dict[str, object]], result["decisions"])
        for decision in decisions:
            signal = cast(dict[str, object], decision["signal"])
            risk = cast(dict[str, object], decision["risk"])
            print(f"{decision['symbol']}: {signal['action']} ({signal['confidence']}) - risk allowed: {risk['allowed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
