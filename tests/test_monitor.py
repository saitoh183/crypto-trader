from crypto_trader.collector import analyse_candles, Candle, calculate_rsi
from crypto_trader.db import connect, init_db


def test_schema_initializes(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    init_db(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"candles", "market_snapshots", "news_items", "run_summaries"}.issubset(tables)


def test_rsi_returns_value_for_moving_series():
    closes = [100, 101, 102, 101, 103, 104, 105, 104, 106, 108, 107, 109, 111, 110, 112, 113]
    rsi = calculate_rsi(closes)
    assert rsi is not None
    assert 0 <= rsi <= 100


def test_analyse_candles_generates_snapshot():
    candles = [
        Candle("binance", "BTCUSDT", "1h", f"2026-01-01T{i:02d}:00:00Z", 100 + i, 101 + i, 99 + i, 100 + i, 10)
        for i in range(30)
    ]
    snapshot = analyse_candles("BTCUSDT", candles)
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.last_close == 129
    assert snapshot.sma_20 is not None
    assert snapshot.trend in {"bullish", "bearish", "mixed"}
