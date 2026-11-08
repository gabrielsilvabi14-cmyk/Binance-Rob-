"""Ponto de entrada para simulações usando apenas dados públicos."""
import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
from urllib.error import URLError

from .data import INTERVALS, SYMBOLS, BinanceData
from .risk import Risk
from .simulation import LOCAL_ZONE, simulate
from .strategy import signal_distances

CAPITAL_BRL = Decimal("500")
WARMUP_BARS = 100


def date_to_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, LOCAL_ZONE).timestamp()) * 1000


def backtest(data: BinanceData, start: int, end: int, brl_per_usdt: Decimal) -> dict:
    if not brl_per_usdt.is_finite() or brl_per_usdt <= 0:
        raise ValueError("Informe uma cotação BRL por USDT positiva e finita.")
    if end > data.server_time():
        raise ValueError("A data final deve estar no passado ou ser a data de hoje.")
    markets = data.market_rules()
    fast_step = INTERVALS["5m"]
    slow_step = INTERVALS["15m"]
    candles = {}
    signals = {}
    for symbol in SYMBOLS:
        candles[symbol] = data.candles(symbol, "5m", start - WARMUP_BARS * fast_step, end)
        slow = data.candles(symbol, "15m", start - WARMUP_BARS * slow_step, end)
        signals[symbol] = signal_distances(candles[symbol], slow)

    risk = Risk()
    report = simulate(candles, signals, markets, CAPITAL_BRL / brl_per_usdt, start, end, risk)
    report.update({
        "modo": "simulado",
        "inicio_ms": start,
        "fim_exclusivo_ms": end,
        "fuso_horario": LOCAL_ZONE.key,
        "capital_inicial_brl": CAPITAL_BRL,
        "brl_por_usdt": brl_per_usdt,
        "saldo_final_brl": report["saldo_final_usdt"] * brl_per_usdt,
        "resultado_liquido_brl": report["resultado_liquido_usdt"] * brl_per_usdt,
        "parametros_de_risco": asdict(risk),
        "pares": SYMBOLS,
    })
    return report


def main():
    parser = argparse.ArgumentParser(description="Binance Scalper 1.0 • somente simulação histórica")
    parser.add_argument("--inicio", required=True, type=date.fromisoformat, help="AAAA-MM-DD")
    parser.add_argument("--fim", required=True, type=date.fromisoformat, help="AAAA-MM-DD, exclusivo")
    parser.add_argument("--brl-por-usdt", required=True, type=Decimal, help="Cotação usada na simulação")
    args = parser.parse_args()
    if not 1 <= (args.fim - args.inicio).days <= 31:
        parser.error("Escolha uma janela de 1 a 31 dias.")
    try:
        report = backtest(BinanceData(), date_to_ms(args.inicio), date_to_ms(args.fim), args.brl_por_usdt)
    except (ValueError, KeyError, TypeError, IndexError, DecimalException, URLError) as exc:
        parser.exit(1, f"Erro na simulação: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
