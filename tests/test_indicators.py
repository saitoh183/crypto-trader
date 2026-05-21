import pytest

from crypto_trader.indicators import atr, ema, macd, rsi, sma


def test_sma_returns_none_until_period_available():
    assert sma([1.0, 2.0], 3) is None
    assert sma([1.0, 2.0, 3.0], 3) == 2.0


def test_ema_uses_standard_multiplier_and_series_seed():
    values = [10.0, 11.0, 12.0, 13.0]

    assert ema(values, 3) == pytest.approx(12.125)


def test_rsi_returns_bounded_value_for_moving_series():
    closes = [100, 101, 102, 101, 103, 104, 105, 104, 106, 108, 107, 109, 111, 110, 112, 113]

    value = rsi(closes, 14)

    assert value is not None
    assert 0 <= value <= 100
    assert value == pytest.approx(80.0)


def test_atr_uses_true_range_average():
    highs = [11.0, 13.0, 14.0, 16.0]
    lows = [9.0, 10.0, 12.0, 13.0]
    closes = [10.0, 12.0, 13.0, 15.0]

    assert atr(highs, lows, closes, 3) == pytest.approx(8 / 3)


def test_macd_returns_line_signal_and_histogram():
    values = [float(i) for i in range(1, 40)]

    result = macd(values, fast_period=12, slow_period=26, signal_period=9)

    assert result is not None
    assert result.macd_line > 0
    assert result.signal_line > 0
    assert result.histogram == pytest.approx(result.macd_line - result.signal_line)
