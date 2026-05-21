from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .collector import Candle
from .indicators import atr, ema, macd, rsi, sma

SignalAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    timeframe: str
    action: SignalAction
    confidence: float
    price: float
    reason: str
    inputs: dict[str, float | str | None]

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "action": self.action,
            "confidence": self.confidence,
            "price": self.price,
            "reason": self.reason,
            "inputs": self.inputs,
        }


def generate_signal(symbol: str, timeframe: str, candles: list[Candle]) -> TradeSignal:
    if not candles:
        return TradeSignal(symbol, timeframe, "HOLD", 0.0, 0.0, "insufficient candle data", {})

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]
    price = closes[-1]

    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    ema_50 = ema(closes, 50)
    rsi_14 = rsi(closes, 14)
    atr_14 = atr(highs, lows, closes, 14)
    volume_sma_20 = sma(volumes, 20)
    macd_value = macd(closes)

    inputs: dict[str, float | str | None] = {
        "close": price,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "rsi_14": rsi_14,
        "atr_14": atr_14,
        "volume": volumes[-1],
        "volume_sma_20": volume_sma_20,
        "macd_line": macd_value.macd_line if macd_value else None,
        "macd_signal": macd_value.signal_line if macd_value else None,
        "macd_histogram": macd_value.histogram if macd_value else None,
    }

    required = [ema_9, ema_21, ema_50, rsi_14, atr_14, volume_sma_20, macd_value]
    if any(value is None for value in required):
        return TradeSignal(symbol, timeframe, "HOLD", 0.0, price, "insufficient indicator history", inputs)

    assert ema_9 is not None
    assert ema_21 is not None
    assert ema_50 is not None
    assert rsi_14 is not None
    assert volume_sma_20 is not None
    assert macd_value is not None

    bullish_trend = price > ema_50 and ema_9 > ema_21
    healthy_rsi = 45 <= rsi_14 <= 72
    volume_confirms = volumes[-1] >= volume_sma_20
    macd_confirms = macd_value.macd_line >= macd_value.signal_line

    if bullish_trend and healthy_rsi and volume_confirms and macd_confirms:
        confidence = 0.60
        confidence += 0.10 if rsi_14 < 65 else 0.0
        confidence += 0.05 if volumes[-1] > volume_sma_20 * 1.05 else 0.0
        confidence = min(confidence, 0.85)
        return TradeSignal(
            symbol,
            timeframe,
            "BUY",
            round(confidence, 2),
            price,
            "price above EMA50 with EMA9 above EMA21; RSI acceptable; volume/MACD confirm",
            inputs,
        )

    bearish_trend = price < ema_50 and ema_9 < ema_21
    if bearish_trend or rsi_14 >= 78:
        return TradeSignal(
            symbol,
            timeframe,
            "SELL",
            0.55,
            price,
            "bearish trend or overheated RSI; exit/reduce exposure signal",
            inputs,
        )

    return TradeSignal(symbol, timeframe, "HOLD", 0.25, price, "no conservative setup confirmed", inputs)
