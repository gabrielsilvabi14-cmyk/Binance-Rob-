"""Backtest cronológico com uma única posição comprada e custos por lado."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .data import INTERVALS, SYMBOLS, Candle, MarketRules
from .risk import Risk, size_position

D = Decimal
FIVE_MINUTES = INTERVALS["5m"]
LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")
MAX_HOLD_MS = 60 * 60_000


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: Decimal
    opened_at: int
    reference: Decimal
    entry: Decimal
    entry_fee: Decimal
    stop: Decimal
    target: Decimal

    @property
    def cost(self) -> Decimal:
        return self.quantity * self.entry + self.entry_fee


def exit_price(position: Position, candle: Candle) -> tuple[Decimal, str] | None:
    if candle.open <= position.stop:
        return candle.open, "stop"
    if candle.open >= position.target:
        return candle.open, "alvo"
    if candle.low <= position.stop:
        return position.stop, "stop"
    if candle.high >= position.target:
        return position.target, "alvo"
    if candle.close_time + 1 - position.opened_at >= MAX_HOLD_MS:
        return candle.close, "tempo"
    return None


def simulate(
    candles: dict[str, list[Candle]], signals: dict[str, dict[int, float]],
    markets: dict[str, MarketRules], capital: Decimal, start: int, end: int,
    risk: Risk = Risk(),
) -> dict:
    symbols = [symbol for symbol in SYMBOLS if symbol in candles]
    cash = equity = peak = capital
    drawdown = D(0)
    position = None
    trades = []
    day = None
    opening_equity = capital
    daily_pnl = D(0)

    for rows in zip(*(candles[symbol] for symbol in symbols), strict=True):
        bar = dict(zip(symbols, rows, strict=True))
        timestamp = rows[0].open_time
        if any(candle.open_time != timestamp for candle in rows):
            raise ValueError("Os cinco pares precisam usar a mesma janela de candles.")
        if not start <= timestamp < end:
            continue

        current_day = datetime.fromtimestamp(timestamp // 1000, LOCAL_ZONE).date()
        next_day = datetime.fromtimestamp((timestamp + FIVE_MINUTES) // 1000, LOCAL_ZONE).date()
        if current_day != day:
            day = current_day
            opening_equity = equity
            daily_pnl = D(0)

        # Entradas usam somente o sinal do candle imediatamente anterior.
        if position is None and timestamp > start and next_day == current_day:
            remaining = opening_equity * risk.daily_loss_limit + daily_pnl
            budget = min(cash * risk.risk_per_trade, remaining)
            for symbol, candle in bar.items():
                distance = signals[symbol].get(timestamp - 1)
                if distance is None:
                    continue
                entry = candle.open * (1 + risk.slippage_per_side)
                stop = entry - D(str(distance))
                quantity = size_position(cash, budget, entry, stop, markets[symbol], risk)
                if quantity <= 0:
                    continue
                position = Position(
                    symbol, quantity, timestamp, candle.open, entry,
                    quantity * entry * risk.fee_per_side, stop,
                    entry + (entry - stop) * risk.reward_risk,
                )
                cash -= position.cost
                break

        if position is not None:
            candle = bar[position.symbol]
            exit_order = exit_price(position, candle)
            if exit_order is None and candle.close_time + 1 == end:
                exit_order = candle.close, "fim_do_periodo"
            if exit_order is None and next_day != current_day:
                exit_order = candle.close, "fim_do_dia"
            if exit_order is not None:
                reference, reason = exit_order
                fill = reference * (1 - risk.slippage_per_side)
                proceeds = position.quantity * fill
                exit_fee = proceeds * risk.fee_per_side
                net = proceeds - exit_fee - position.cost
                cash += proceeds - exit_fee
                daily_pnl += net
                trades.append({
                    "par": position.symbol,
                    "entrada_ms": position.opened_at,
                    "candle_saida_ms": timestamp,
                    "motivo": reason,
                    "quantidade": position.quantity,
                    "entrada_usdt": position.entry,
                    "saida_usdt": fill,
                    "taxas_usdt": position.entry_fee + exit_fee,
                    "slippage_usdt": position.quantity * (
                        position.entry - position.reference + reference - fill
                    ),
                    "resultado_liquido_usdt": net,
                })
                position = None

        equity = cash
        if position is not None:
            mark = bar[position.symbol].close
            equity += position.quantity * mark * (1 - risk.slippage_per_side) * (1 - risk.fee_per_side)
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)

    return {
        "saldo_inicial_usdt": capital,
        "saldo_final_usdt": cash,
        "resultado_liquido_usdt": cash - capital,
        "queda_maxima_pct": drawdown * 100,
        "total_operacoes": len(trades),
        "total_taxas_usdt": sum((trade["taxas_usdt"] for trade in trades), D(0)),
        "total_slippage_usdt": sum((trade["slippage_usdt"] for trade in trades), D(0)),
        "operacoes": trades,
    }
