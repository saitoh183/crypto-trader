from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from typing import cast

from .collector import Candle
from .config import Settings, get_settings
from .db import connect, init_db
from .paper import PaperAccount, PaperOrder, execute_paper_order
from .risk import RiskState, evaluate_risk
from .scan import load_recent_candles
from .strategy import generate_signal


@dataclass
class BacktestSummary:
    symbol: str
    interval: str
    candles_used: int
    starting_cash_usdt: float
    ending_cash_usdt: float
    ending_equity_usdt: float
    realized_pnl_usdt: float
    unrealized_pnl_usdt: float
    total_return_pct: float
    signals: dict[str, int] = field(default_factory=lambda: {"BUY": 0, "SELL": 0, "HOLD": 0})
    orders: dict[str, int] = field(default_factory=lambda: {"FILLED": 0, "REJECTED": 0, "SKIPPED": 0})
    filled_orders: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "candles_used": self.candles_used,
            "starting_cash_usdt": self.starting_cash_usdt,
            "ending_cash_usdt": self.ending_cash_usdt,
            "ending_equity_usdt": self.ending_equity_usdt,
            "realized_pnl_usdt": self.realized_pnl_usdt,
            "unrealized_pnl_usdt": self.unrealized_pnl_usdt,
            "total_return_pct": self.total_return_pct,
            "signals": self.signals,
            "orders": self.orders,
            "filled_orders": self.filled_orders,
        }


def load_backtest_candles(conn: sqlite3.Connection, symbol: str, interval: str, limit: int) -> list[Candle]:
    return load_recent_candles(conn, symbol, interval, limit=limit)


def run_symbol_backtest(
    candles: list[Candle],
    settings: Settings,
    symbol: str,
    interval: str,
    min_history: int = 60,
) -> BacktestSummary:
    account = PaperAccount(cash_usdt=settings.paper_balance_usdt)
    signal_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    order_counts = {"FILLED": 0, "REJECTED": 0, "SKIPPED": 0}
    filled_orders: list[dict[str, object]] = []
    realized_pnl = 0.0
    trades_today = 0

    if len(candles) < min_history:
        latest_price = candles[-1].close if candles else 0.0
        equity = account.equity_usdt({symbol: latest_price})
        return BacktestSummary(
            symbol=symbol,
            interval=interval,
            candles_used=len(candles),
            starting_cash_usdt=settings.paper_balance_usdt,
            ending_cash_usdt=account.cash_usdt,
            ending_equity_usdt=equity,
            realized_pnl_usdt=0.0,
            unrealized_pnl_usdt=0.0,
            total_return_pct=0.0,
            signals=signal_counts,
            orders=order_counts,
            filled_orders=[],
        )

    for end_index in range(min_history, len(candles) + 1):
        window = candles[:end_index]
        signal = generate_signal(symbol, interval, window)
        signal_counts[signal.action] += 1
        price = signal.price if signal.price > 0 else window[-1].close
        equity = account.equity_usdt({symbol: price})
        state = RiskState(
            equity_usdt=equity,
            daily_loss_usdt=max(0.0, -realized_pnl),
            open_positions=len(account.positions),
            trades_today=trades_today,
            kill_switch=False,
        )
        risk = evaluate_risk(signal, settings, state)
        if not risk.allowed:
            continue

        order: PaperOrder = execute_paper_order(account, signal, risk.max_position_usdt)
        order_counts[order.status] = order_counts.get(order.status, 0) + 1
        if order.status == "FILLED":
            trades_today += 1
            realized_pnl += order.realized_pnl_usdt
            order_data = order.as_dict()
            order_data["at_candle_utc"] = window[-1].open_time_utc
            filled_orders.append(cast(dict[str, object], order_data))

    latest_price = candles[-1].close
    ending_equity = account.equity_usdt({symbol: latest_price})
    open_cost_basis = sum(position.cost_basis_usdt for position in account.positions.values())
    open_market_value = sum(position.market_value_usdt(latest_price) for position in account.positions.values())
    unrealized_pnl = round(open_market_value - open_cost_basis, 8)
    total_return = round(((ending_equity - settings.paper_balance_usdt) / settings.paper_balance_usdt) * 100, 4)

    return BacktestSummary(
        symbol=symbol,
        interval=interval,
        candles_used=len(candles),
        starting_cash_usdt=settings.paper_balance_usdt,
        ending_cash_usdt=account.cash_usdt,
        ending_equity_usdt=ending_equity,
        realized_pnl_usdt=round(realized_pnl, 8),
        unrealized_pnl_usdt=unrealized_pnl,
        total_return_pct=total_return,
        signals=signal_counts,
        orders=order_counts,
        filled_orders=filled_orders,
    )


def build_backtest_result(settings: Settings, limit: int = 500) -> dict[str, object]:
    conn = connect(settings.database_path)
    init_db(conn)
    try:
        summaries = []
        for symbol in settings.symbols:
            candles = load_backtest_candles(conn, symbol, settings.interval, limit)
            summaries.append(run_symbol_backtest(candles, settings, symbol, settings.interval).as_dict())
    finally:
        conn.close()

    return {
        "mode": "backtest",
        "quote_currency": "USDT",
        "risk_profile": settings.risk_profile,
        "paper_balance_usdt": settings.paper_balance_usdt,
        "candle_limit": limit,
        "summaries": summaries,
        "disclaimer": "Backtest only. Uses stored candles and does not place live or paper orders.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the conservative crypto paper strategy against stored candles")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--limit", type=int, default=500, help="Maximum candles per symbol to replay")
    args = parser.parse_args(argv)

    result = build_backtest_result(get_settings(), limit=args.limit)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for summary in cast(list[dict[str, object]], result["summaries"]):
            print(
                f"{summary['symbol']}: {summary['total_return_pct']}% "
                f"return, realized {summary['realized_pnl_usdt']} USDT, "
                f"orders {summary['orders']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
