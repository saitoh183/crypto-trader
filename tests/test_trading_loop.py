import json

from crypto_trader.collector import Candle, upsert_candles
from crypto_trader.config import Settings
from crypto_trader.db import connect, init_db
from crypto_trader.paper import PaperAccount, execute_paper_order
from crypto_trader.risk import RiskState, evaluate_risk
from crypto_trader.scan import build_scan_result
from crypto_trader.strategy import generate_signal


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


def test_paper_buy_reduces_cash_and_opens_position():
    account = PaperAccount(cash_usdt=1000.0)
    signal = generate_signal("BTCUSDT", "15m", rising_candles())

    order = execute_paper_order(account, signal, position_size_usdt=50.0, fee_rate=0.001)

    assert order.status == "FILLED"
    assert account.cash_usdt == 949.95
    assert account.positions["BTCUSDT"].quantity > 0
    assert account.positions["BTCUSDT"].entry_price == signal.price


def test_scan_result_contains_nova_ready_decisions(tmp_path):
    db_path = tmp_path / "scan.sqlite3"
    conn = connect(db_path)
    init_db(conn)
    upsert_candles(conn, rising_candles("BTCUSDT"))
    conn.close()
    settings = Settings(database_path=db_path, symbols=("BTCUSDT",), interval="15m")

    result = build_scan_result(settings)

    conn = connect(db_path)
    signal_count = conn.execute("SELECT COUNT(*) FROM trade_signals").fetchone()[0]
    paper_order_count = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    conn.close()

    assert result["mode"] == "paper"
    assert result["paper_balance_usdt"] == 1000.0
    assert result["decisions"][0]["symbol"] == "BTCUSDT"
    assert result["decisions"][0]["signal"]["action"] == "BUY"
    assert result["decisions"][0]["risk"]["allowed"] is True
    assert signal_count == 1
    assert paper_order_count == 1
    json.dumps(result)
