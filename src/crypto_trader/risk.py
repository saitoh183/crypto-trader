from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .strategy import TradeSignal


@dataclass(frozen=True)
class RiskState:
    equity_usdt: float
    daily_loss_usdt: float = 0.0
    open_positions: int = 0
    trades_today: int = 0
    kill_switch: bool = False


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    max_position_usdt: float
    max_trade_risk_usdt: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "max_position_usdt": self.max_position_usdt,
            "max_trade_risk_usdt": self.max_trade_risk_usdt,
            "reasons": self.reasons,
        }


def evaluate_risk(signal: TradeSignal, settings: Settings, state: RiskState) -> RiskDecision:
    max_position = round(state.equity_usdt * settings.max_position_pct / 100, 2)
    max_trade_risk = round(state.equity_usdt * settings.max_trade_risk_pct / 100, 2)
    max_daily_loss = state.equity_usdt * settings.max_daily_loss_pct / 100
    reasons: list[str] = []

    if signal.action != "BUY":
        reasons.append(f"signal action is {signal.action}; no new buy allowed")
    if state.kill_switch:
        reasons.append("kill switch is enabled")
    if state.daily_loss_usdt >= max_daily_loss:
        reasons.append("daily loss limit reached")
    if state.open_positions >= settings.max_open_positions:
        reasons.append("maximum open positions reached")
    if state.trades_today >= settings.max_trades_per_day:
        reasons.append("maximum trades per day reached")
    if signal.confidence < 0.6:
        reasons.append("signal confidence below conservative threshold")

    allowed = not reasons
    if allowed:
        reasons.append("risk checks passed")

    return RiskDecision(allowed=allowed, max_position_usdt=max_position, max_trade_risk_usdt=max_trade_risk, reasons=reasons)
