from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class MacdValue:
    macd_line: float
    signal_line: float
    histogram: float


def sma(values: list[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if len(values) < period:
        return None
    return mean(values[-period:])


def ema(values: list[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = (value - current) * multiplier + current
    return current


def ema_series(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if not values:
        return []

    multiplier = 2 / (period + 1)
    series = [values[0]]
    for value in values[1:]:
        series.append((value - series[-1]) * multiplier + series[-1])
    return series


def rsi(closes: list[float], period: int = 14) -> float | None:
    if period <= 0:
        raise ValueError("period must be greater than zero")
    if len(closes) <= period:
        return None

    gains: list[float] = []
    losses: list[float] = []
    recent = closes[-(period + 1) :]
    for previous, current in zip(recent, recent[1:]):
        delta = current - previous
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))

    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("highs, lows, and closes must have the same length")
    if not highs:
        return []

    ranges = [highs[0] - lows[0]]
    for index in range(1, len(highs)):
        previous_close = closes[index - 1]
        ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - previous_close),
                abs(lows[index] - previous_close),
            )
        )
    return ranges


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if period <= 0:
        raise ValueError("period must be greater than zero")

    ranges = true_ranges(highs, lows, closes)
    if len(ranges) < period:
        return None
    return mean(ranges[-period:])


def macd(values: list[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> MacdValue | None:
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("periods must be greater than zero")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    if len(values) < slow_period + signal_period:
        return None

    fast = ema_series(values, fast_period)
    slow = ema_series(values, slow_period)
    macd_series = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    signal = ema_series(macd_series, signal_period)[-1]
    line = macd_series[-1]
    return MacdValue(macd_line=line, signal_line=signal, histogram=line - signal)
