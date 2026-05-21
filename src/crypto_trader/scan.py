from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from typing import cast

from .collector import Candle
from .config import Settings, get_settings
from .db import connect, init_db
from .paper import PaperAccount, PaperOrder, PaperPosition, execute_paper_order
from .risk import RiskState, evaluate_risk
from .strategy import TradeSignal, generate_signal


PAPER_ACCOUNT_ID = "default"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_today_prefix() -> str:
    return datetime.now(UTC).date().isoformat()


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


def load_paper_account(conn: sqlite3.Connection, settings: Settings, generated_at: str) -> PaperAccount:
    account_row = conn.execute(
        "SELECT cash_usdt FROM paper_account WHERE account_id = ?",
        (PAPER_ACCOUNT_ID,),
    ).fetchone()
    if account_row is None:
        cash_usdt = settings.paper_balance_usdt
        conn.execute(
            "INSERT INTO paper_account (account_id, cash_usdt, updated_at_utc) VALUES (?, ?, ?)",
            (PAPER_ACCOUNT_ID, cash_usdt, generated_at),
        )
    else:
        cash_usdt = float(account_row["cash_usdt"])

    position_rows = conn.execute(
        "SELECT symbol, quantity, entry_price, cost_basis_usdt FROM paper_positions ORDER BY symbol"
    ).fetchall()
    positions = {
        row["symbol"]: PaperPosition(
            symbol=row["symbol"],
            quantity=float(row["quantity"]),
            entry_price=float(row["entry_price"]),
            cost_basis_usdt=float(row["cost_basis_usdt"]),
        )
        for row in position_rows
    }
    return PaperAccount(cash_usdt=cash_usdt, positions=positions)


def save_paper_account(conn: sqlite3.Connection, generated_at: str, account: PaperAccount) -> None:
    conn.execute(
        """
        INSERT INTO paper_account (account_id, cash_usdt, updated_at_utc)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET cash_usdt = excluded.cash_usdt, updated_at_utc = excluded.updated_at_utc
        """,
        (PAPER_ACCOUNT_ID, account.cash_usdt, generated_at),
    )
    conn.execute("DELETE FROM paper_positions")
    conn.executemany(
        """
        INSERT INTO paper_positions (symbol, quantity, entry_price, cost_basis_usdt, updated_at_utc)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (position.symbol, position.quantity, position.entry_price, position.cost_basis_usdt, generated_at)
            for position in account.positions.values()
        ],
    )


def load_kill_switch(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM paper_settings WHERE key = 'kill_switch'").fetchone()
    return bool(row and row["value"].strip().lower() in {"1", "true", "yes", "on"})


def load_risk_state(
    conn: sqlite3.Connection,
    account: PaperAccount,
    settings: Settings,
    prices: dict[str, float],
) -> RiskState:
    today = utc_today_prefix()
    trades_today = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_orders
        WHERE generated_at_utc LIKE ? AND status = 'FILLED'
        """,
        (f"{today}%",),
    ).fetchone()[0]
    realized_losses_today = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN realized_pnl_usdt < 0 THEN -realized_pnl_usdt ELSE 0 END), 0)
        FROM paper_orders
        WHERE generated_at_utc LIKE ? AND side = 'SELL' AND status = 'FILLED'
        """,
        (f"{today}%",),
    ).fetchone()[0]
    equity_usdt = account.equity_usdt(prices)

    return RiskState(
        equity_usdt=equity_usdt,
        daily_loss_usdt=float(realized_losses_today),
        open_positions=len(account.positions),
        trades_today=int(trades_today),
        kill_switch=load_kill_switch(conn),
    )


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
        (generated_at_utc, symbol, side, quantity, price, notional_usdt, fee_usdt, status, reason, realized_pnl_usdt)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            order_data["realized_pnl_usdt"],
        ),
    )


def build_scan_result(settings: Settings) -> dict[str, object]:
    conn = connect(settings.database_path)
    init_db(conn)
    generated_at = utc_now_iso()
    account = load_paper_account(conn, settings, generated_at)
    decisions: list[dict[str, object]] = []
    latest_prices: dict[str, float] = {}

    try:
        for symbol in settings.symbols:
            candles = load_recent_candles(conn, symbol, settings.interval, settings.candle_limit)
            signal = generate_signal(symbol, settings.interval, candles)
            if signal.price > 0:
                latest_prices[symbol] = signal.price
            save_trade_signal(conn, generated_at, signal)

            risk_state = load_risk_state(conn, account, settings, latest_prices)
            risk = evaluate_risk(signal, settings, risk_state)
            order = None
            if risk.allowed:
                order = execute_paper_order(account, signal, risk.max_position_usdt)
                save_paper_order(conn, generated_at, order)
                if order.status == "FILLED":
                    save_paper_account(conn, generated_at, account)

            conn.commit()
            decisions.append(
                {
                    "symbol": symbol,
                    "timeframe": settings.interval,
                    "signal": signal.as_dict(),
                    "risk": risk.as_dict(),
                    "risk_state": {
                        "equity_usdt": risk_state.equity_usdt,
                        "daily_loss_usdt": risk_state.daily_loss_usdt,
                        "open_positions": risk_state.open_positions,
                        "trades_today": risk_state.trades_today,
                        "kill_switch": risk_state.kill_switch,
                    },
                    "paper_order": order.as_dict() if order else None,
                }
            )
    finally:
        save_paper_account(conn, generated_at, account)
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
            "equity_usdt": account.equity_usdt(latest_prices),
            "positions": {symbol: position.as_dict() for symbol, position in account.positions.items()},
        },
        "disclaimer": "Paper trading only. No live exchange order path is enabled.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate NovaAI-ready crypto paper trading scan")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        result = build_scan_result(get_settings())
    except Exception as exc:  # noqa: BLE001 - scanner should fail with a clear CLI message
        error = {"mode": "paper", "status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(error, indent=2, sort_keys=True))
        else:
            print(f"scan failed: {exc}", file=sys.stderr)
        return 1

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
