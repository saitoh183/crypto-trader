import json
from typing import Any, cast

from crypto_trader.backtest import build_backtest_result, run_symbol_backtest
from crypto_trader.collector import Candle, upsert_candles
from crypto_trader.config import Settings
from crypto_trader.db import connect, init_db
from crypto_trader.paper import PaperAccount, PaperPosition, execute_paper_order
from crypto_trader.risk import RiskState, evaluate_risk
from crypto_trader.scan import build_scan_result, load_risk_state
from crypto_trader.strategy import TradeSignal, generate_signal


def rising_candles(symbol="BTCUSDT", count=80):
    candles = []
    close = 100.0
    for index in range(count):
        close += 1.0
        if index % 5 == 0:
            close -= 3.5
        candles.append(
            Candle(
                exchange="binance",
                symbol=symbol,
                interval="15m",
                open_time_utc=f"2026-01-01T{index // 4:02d}:{(index % 4) * 15:02d}:00Z",
                open=close - 0.5,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1000.0 + index,
            )
        )
    return candles


def test_strategy_generates_buy_signal_for_confirmed_uptrend():
    signal = generate_signal("BTCUSDT", "15m", rising_candles())

    assert signal.action == "BUY"
    assert signal.confidence >= 0.6
    assert "above EMA50" in signal.reason
    assert signal.inputs["rsi_14"] is not None


def test_strategy_holds_when_not_enough_candles():
    signal = generate_signal("BTCUSDT", "15m", rising_candles(count=10))

    assert signal.action == "HOLD"
    assert signal.confidence == 0.0
    assert "insufficient" in signal.reason.lower()


def test_risk_allows_conservative_position_inside_limits():
    settings = Settings()
    signal = generate_signal("BTCUSDT", "15m", rising_candles())

    decision = evaluate_risk(signal, settings, RiskState(equity_usdt=1000.0))

    assert decision.allowed is True
    assert decision.max_position_usdt == 50.0
    assert decision.reasons == ["risk checks passed"]


def test_risk_blocks_when_daily_loss_limit_reached():
    settings = Settings()
    signal = generate_signal("BTCUSDT", "15m", rising_candles())

    decision = evaluate_risk(signal, settings, RiskState(equity_usdt=1000.0, daily_loss_usdt=21.0))

    assert decision.allowed is False
    assert any("daily loss" in reason for reason in decision.reasons)


def test_risk_allows_sell_signal_for_existing_exit_path():
    settings = Settings()
    signal = TradeSignal("BTCUSDT", "15m", "SELL", 0.55, 125.0, "exit", {})

    decision = evaluate_risk(signal, settings, RiskState(equity_usdt=1000.0, open_positions=1))

    assert decision.allowed is True


def test_paper_buy_reduces_cash_and_opens_position():
    account = PaperAccount(cash_usdt=1000.0)
    signal = generate_signal("BTCUSDT", "15m", rising_candles())

    order = execute_paper_order(account, signal, position_size_usdt=50.0, fee_rate=0.001)

    assert order.status == "FILLED"
    assert account.cash_usdt == 949.95
    assert account.positions["BTCUSDT"].quantity > 0
    assert account.positions["BTCUSDT"].entry_price == signal.price


def test_paper_buy_rejects_when_existing_position_hits_size_limit():
    account = PaperAccount(
        cash_usdt=949.95,
        positions={"BTCUSDT": PaperPosition("BTCUSDT", quantity=0.5, entry_price=100.0, cost_basis_usdt=50.0)},
    )
    signal = TradeSignal("BTCUSDT", "15m", "BUY", 0.7, 110.0, "buy", {})

    order = execute_paper_order(account, signal, position_size_usdt=50.0, fee_rate=0.001)

    assert order.status == "REJECTED"
    assert "size limit" in order.reason
    assert account.positions["BTCUSDT"].cost_basis_usdt == 50.0


def test_paper_sell_closes_position_and_records_realized_pnl():
    account = PaperAccount(
        cash_usdt=900.0,
        positions={"BTCUSDT": PaperPosition("BTCUSDT", quantity=1.0, entry_price=100.0, cost_basis_usdt=100.0)},
    )
    signal = TradeSignal("BTCUSDT", "15m", "SELL", 0.55, 120.0, "exit", {})

    order = execute_paper_order(account, signal, position_size_usdt=50.0, fee_rate=0.001)

    assert order.status == "FILLED"
    assert order.side == "SELL"
    assert order.realized_pnl_usdt == 19.88
    assert account.cash_usdt == 1019.88
    assert "BTCUSDT" not in account.positions


def test_load_risk_state_preserves_zero_equity(tmp_path):
    db_path = tmp_path / "scan.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    conn.execute(
        "INSERT INTO paper_account (account_id, cash_usdt, updated_at_utc) VALUES (?, ?, ?)",
        ("default", 0.0, "2026-01-01T00:00:00Z"),
    )
    conn.commit()

    settings = Settings(database_path=db_path, symbols=("BTCUSDT",), interval="15m")
    account = PaperAccount(cash_usdt=0.0)
    state = load_risk_state(conn, account, settings, prices={})
    conn.close()

    assert state.equity_usdt == 0.0


def test_scan_result_contains_nova_ready_decisions(tmp_path):
    db_path = tmp_path / "scan.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    upsert_candles(conn, rising_candles("BTCUSDT"))
    conn.close()
    settings = Settings(database_path=db_path, symbols=("BTCUSDT",), interval="15m")

    result = cast(dict[str, Any], build_scan_result(settings))

    conn = connect(db_path)
    signal_count = conn.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
    paper_order_count = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    position_count = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    conn.close()

    assert result["mode"] == "paper"
    assert result["paper_balance_usdt"] == 1000.0
    assert result["decisions"][0]["symbol"] == "BTCUSDT"
    assert result["decisions"][0]["signal"]["action"] == "BUY"
    assert result["decisions"][0]["risk"]["allowed"] is True
    assert result["paper_account"]["cash_usdt"] == 949.95
    assert signal_count == 1
    assert paper_order_count == 1
    assert position_count == 1
    json.dumps(result)


def test_scan_loads_persisted_account_and_does_not_overbuy_position_cap(tmp_path):
    db_path = tmp_path / "scan.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    upsert_candles(conn, rising_candles("BTCUSDT"))
    conn.close()
    settings = Settings(database_path=db_path, symbols=("BTCUSDT",), interval="15m")

    first = build_scan_result(settings)
    second = build_scan_result(settings)

    assert first["paper_account"]["cash_usdt"] == 949.95
    assert second["paper_account"]["cash_usdt"] == 949.95
    assert second["decisions"][0]["paper_order"]["status"] == "REJECTED"
    assert "size limit" in second["decisions"][0]["paper_order"]["reason"]


def test_scan_result_handles_empty_candle_history(tmp_path):
    settings = Settings(database_path=tmp_path / "empty.sqlite3", symbols=("BTCUSDT",), interval="15m")

    result = cast(dict[str, Any], build_scan_result(settings))

    assert result["decisions"][0]["signal"]["action"] == "HOLD"
    assert result["decisions"][0]["risk"]["allowed"] is False
    assert result["paper_account"]["cash_usdt"] == 1000.0


def test_backtest_replays_strategy_without_mutating_paper_tables(tmp_path):
    db_path = tmp_path / "backtest.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    upsert_candles(conn, rising_candles("BTCUSDT", count=90))
    conn.close()
    settings = Settings(database_path=db_path, symbols=("BTCUSDT",), interval="15m")

    result = cast(dict[str, Any], build_backtest_result(settings, limit=90))

    conn = connect(db_path)
    paper_order_count = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    paper_position_count = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    conn.close()

    summary = result["summaries"][0]
    assert result["mode"] == "backtest"
    assert summary["symbol"] == "BTCUSDT"
    assert summary["candles_used"] == 90
    assert summary["signals"]["BUY"] > 0
    assert summary["orders"]["FILLED"] >= 1
    assert paper_order_count == 0
    assert paper_position_count == 0
    json.dumps(result)


def test_backtest_handles_insufficient_history():
    settings = Settings(symbols=("BTCUSDT",), interval="15m")

    summary = run_symbol_backtest(rising_candles("BTCUSDT", count=20), settings, "BTCUSDT", "15m")

    assert summary.candles_used == 20
    assert summary.ending_equity_usdt == 1000.0
    assert summary.signals == {"BUY": 0, "SELL": 0, "HOLD": 0}
