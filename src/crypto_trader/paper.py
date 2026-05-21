from __future__ import annotations

from dataclasses import dataclass, field

from .strategy import TradeSignal


@dataclass
class PaperPosition:
    symbol: str
    quantity: float
    entry_price: float
    cost_basis_usdt: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "cost_basis_usdt": self.cost_basis_usdt,
        }


@dataclass
class PaperAccount:
    cash_usdt: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperOrder:
    symbol: str
    side: str
    quantity: float
    price: float
    notional_usdt: float
    fee_usdt: float
    status: str
    reason: str

    def as_dict(self) -> dict[str, float | str]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "notional_usdt": self.notional_usdt,
            "fee_usdt": self.fee_usdt,
            "status": self.status,
            "reason": self.reason,
        }


def execute_paper_order(account: PaperAccount, signal: TradeSignal, position_size_usdt: float, fee_rate: float = 0.001) -> PaperOrder:
    if signal.action != "BUY":
        return PaperOrder(signal.symbol, signal.action, 0.0, signal.price, 0.0, 0.0, "SKIPPED", "signal is not BUY")
    if signal.price <= 0:
        return PaperOrder(signal.symbol, "BUY", 0.0, signal.price, 0.0, 0.0, "REJECTED", "invalid signal price")

    notional = min(position_size_usdt, account.cash_usdt)
    fee = round(notional * fee_rate, 8)
    total = notional + fee
    if total > account.cash_usdt:
        notional = round(account.cash_usdt / (1 + fee_rate), 8)
        fee = round(notional * fee_rate, 8)
        total = notional + fee
    if notional <= 0:
        return PaperOrder(signal.symbol, "BUY", 0.0, signal.price, 0.0, 0.0, "REJECTED", "insufficient cash")

    quantity = notional / signal.price
    account.cash_usdt = round(account.cash_usdt - total, 8)
    existing = account.positions.get(signal.symbol)
    if existing:
        combined_quantity = existing.quantity + quantity
        combined_cost = existing.cost_basis_usdt + notional
        existing.quantity = combined_quantity
        existing.cost_basis_usdt = combined_cost
        existing.entry_price = combined_cost / combined_quantity
    else:
        account.positions[signal.symbol] = PaperPosition(signal.symbol, quantity, signal.price, notional)

    return PaperOrder(signal.symbol, "BUY", quantity, signal.price, notional, fee, "FILLED", "paper buy executed")
