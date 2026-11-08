"""Dimensionamento de posições Spot simuladas em USDT."""
from dataclasses import dataclass
from decimal import Decimal
from math import lcm

from .data import MarketRules

D = Decimal


@dataclass(frozen=True)
class Risk:
    risk_per_trade: Decimal = D("0.005")
    daily_loss_limit: Decimal = D("0.02")
    fee_per_side: Decimal = D("0.001")
    slippage_per_side: Decimal = D("0.0005")
    reward_risk: Decimal = D("1.8")


def size_position(
    cash: Decimal, budget: Decimal, entry: Decimal, stop: Decimal,
    rules: MarketRules, risk: Risk,
) -> Decimal:
    entry_cost = entry * (1 + risk.fee_per_side)
    stop_proceeds = stop * (1 - risk.slippage_per_side) * (1 - risk.fee_per_side)
    if not 0 < stop < entry or budget <= 0 or cash <= 0:
        return D(0)
    quantity = min(budget / (entry_cost - stop_proceeds), cash / entry_cost, rules.max_qty)
    if rules.max_notional is not None:
        quantity = min(quantity, rules.max_notional / entry)
    if rules.lot_steps:
        # Múltiplo comum dos incrementos decimais de LOT_SIZE e MARKET_LOT_SIZE.
        scale = 10 ** max(0, *(-step.as_tuple().exponent for step in rules.lot_steps))
        increment = D(lcm(*(int(step * scale) for step in rules.lot_steps))) / scale
        quantity = (quantity // increment) * increment
    if quantity < rules.min_qty or quantity * stop * (1 - risk.slippage_per_side) < rules.min_notional:
        return D(0)
    return quantity
