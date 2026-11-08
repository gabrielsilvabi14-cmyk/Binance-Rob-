from datetime import date
from decimal import Decimal as D
from io import BytesIO
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pandas as pd

from binance_scalper.__main__ import date_to_ms
from binance_scalper.data import BinanceData, Candle, DataError, MarketRules, SYMBOLS
from binance_scalper.risk import Risk, size_position
from binance_scalper.simulation import FIVE_MINUTES, simulate
from binance_scalper.strategy import signal_distances

START = date_to_ms(date(2026, 11, 3))
STEP = FIVE_MINUTES
RULES = MarketRules((D("0.001"),), D("0.001"), D("10000"), D("5"))


def bar(index, open="100", high="100.2", low="99.8", close="100"):
    timestamp = START + index * STEP
    return Candle(timestamp, timestamp + STEP - 1, D(open), D(high), D(low), D(close), D("100"))


def raw_candle(timestamp):
    return [timestamp, "100", "101", "99", "100", "5", timestamp + STEP - 1]


def run_simulation(candles, signal_indexes=(0,), risk=Risk()):
    return simulate(
        {"BTCUSDT": candles},
        {"BTCUSDT": {bar(index).close_time: 1.0 for index in signal_indexes}},
        {"BTCUSDT": RULES}, D("100"), candles[0].open_time, candles[-1].close_time + 1, risk,
    )


class DataTests(TestCase):
    def test_pagination_has_no_missing_or_repeated_candles(self):
        api = BinanceData()
        first_page = [raw_candle(START + index * STEP) for index in range(1000)]
        api._get = Mock(side_effect=[first_page, [raw_candle(START + 1000 * STEP)]])
        candles = api.candles("BTCUSDT", "5m", START, START + 1001 * STEP)
        self.assertEqual(len(candles), 1001)
        self.assertEqual(api._get.call_args_list[1].kwargs["startTime"], START + 1000 * STEP)
        self.assertEqual(api._get.call_args_list[1].kwargs["limit"], 1)

    def test_missing_candle_fails_instead_of_filling_data(self):
        api = BinanceData()
        api._get = Mock(return_value=[raw_candle(START + STEP)])
        with self.assertRaisesRegex(DataError, "fora de sequência"):
            api.candles("BTCUSDT", "5m", START, START + 2 * STEP)

    def test_rate_limit_stops_requests(self):
        error = HTTPError("https://data-api.binance.vision", 429, "", {"Retry-After": "60"}, BytesIO())
        with patch("binance_scalper.data.urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(DataError, "Retry-After: 60s"):
                BinanceData().server_time()
            request.assert_called_once()

    def test_spot_filters_include_both_lot_limits_and_notional(self):
        rules = MarketRules.parse({
            "symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.003", "minQty": "0.003", "maxQty": "1000"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.002", "minQty": "0.004", "maxQty": "100"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                {"filterType": "NOTIONAL", "minNotional": "7", "maxNotional": "9000"},
            ],
        })
        self.assertEqual(rules, MarketRules((D("0.003"), D("0.002")), D("0.004"), D("100"), D("7"), D("9000")))

    def test_exchange_info_sends_symbol_array_without_spaces(self):
        api = BinanceData()
        api._get = Mock(return_value={"symbols": [{"symbol": symbol} for symbol in SYMBOLS]})
        with patch("binance_scalper.data.MarketRules.parse", return_value=RULES):
            self.assertEqual(api.market_rules(), dict.fromkeys(SYMBOLS, RULES))
        api._get.assert_called_once_with(
            "exchangeInfo", symbols='["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]',
        )


class StrategyTests(TestCase):
    def test_uses_only_last_already_closed_slow_candle(self):
        fast = pd.DataFrame({
            "close_time": [5 * 60_000 - 1, 15 * 60_000 - 1],
            "ema_fast": [12, 12], "ema_slow": [11, 11],
            "rsi": [60, 60], "volume": [200, 200],
            "volume_mean_previous": [100, 100], "atr": [1, 1],
        })
        slow = pd.DataFrame({
            "close_time": [-1, 15 * 60_000 - 1],
            "ema_fast": [10, 12], "ema_slow": [11, 11],
        }, index=[25, 26])
        with patch("binance_scalper.strategy.add_indicators", side_effect=[fast, slow]):
            self.assertEqual(signal_distances([], []), {15 * 60_000 - 1: 1.5})


class RiskTests(TestCase):
    def test_size_respects_cash_risk_and_both_steps(self):
        rules = MarketRules((D("0.003"), D("0.002")), D("0.004"), D("100"), D("5"))
        risk = Risk()
        cash, budget, entry, stop = D("100"), D("0.5"), D("100"), D("99")
        quantity = size_position(cash, budget, entry, stop, rules, risk)
        entry_cost = entry * (1 + risk.fee_per_side)
        stop_value = stop * (1 - risk.slippage_per_side) * (1 - risk.fee_per_side)
        self.assertGreater(quantity, 0)
        self.assertEqual(quantity % D("0.006"), 0)
        self.assertLessEqual(quantity * entry_cost, cash)
        self.assertLessEqual(quantity * (entry_cost - stop_value), budget)
        self.assertGreater((quantity + D("0.006")) * (entry_cost - stop_value), budget)

    def test_below_notional_or_no_daily_budget_skips(self):
        self.assertEqual(size_position(D("100"), D("0.01"), D("100"), D("99"), RULES, Risk()), 0)
        self.assertEqual(size_position(D("100"), D("0"), D("100"), D("99"), RULES, Risk()), 0)


class SimulationTests(TestCase):
    def test_enters_next_open_and_stop_wins_if_both_levels_hit(self):
        candles = [bar(0), bar(1, "101", "104", "98", "102"), bar(2)]
        report = run_simulation(candles)
        trade, = report["operacoes"]
        self.assertEqual(trade["entrada_ms"], START + STEP)
        self.assertEqual(trade["entrada_usdt"], D("101.0505"))
        self.assertEqual(trade["motivo"], "stop")
        self.assertLess(trade["resultado_liquido_usdt"], 0)
        self.assertGreater(trade["taxas_usdt"], 0)
        self.assertGreater(trade["slippage_usdt"], 0)
        self.assertEqual(report["resultado_liquido_usdt"].quantize(D("0.000001")),
                         trade["resultado_liquido_usdt"].quantize(D("0.000001")))

    def test_gap_below_stop_uses_open_and_blocks_later_entry_after_daily_loss(self):
        candles = [bar(0), bar(1), bar(2, "90", "90.5", "89.5", "90"), bar(3), bar(4)]
        report = run_simulation(candles, signal_indexes=(0, 2))
        trade, = report["operacoes"]
        self.assertEqual(trade["saida_usdt"], D("89.955"))
        self.assertLess(report["resultado_liquido_usdt"], D("-2"))

    def test_only_one_position_even_when_two_pairs_signal(self):
        candles = [bar(0), bar(1), bar(2)]
        report = simulate(
            {pair: candles for pair in ("ETHUSDT", "BTCUSDT")},
            {pair: {bar(0).close_time: 1.0} for pair in ("ETHUSDT", "BTCUSDT")},
            {pair: RULES for pair in ("ETHUSDT", "BTCUSDT")}, D("100"), START, START + 3 * STEP,
        )
        trade, = report["operacoes"]
        self.assertEqual(trade["par"], "BTCUSDT")
        self.assertEqual(trade["motivo"], "fim_do_periodo")

    def test_time_exit_is_one_hour(self):
        report = run_simulation([bar(index) for index in range(15)])
        trade, = report["operacoes"]
        self.assertEqual(trade["motivo"], "tempo")
        self.assertEqual(trade["candle_saida_ms"] + STEP - trade["entrada_ms"], 60 * 60_000)

    def test_position_closes_before_local_midnight(self):
        report = run_simulation([bar(index) for index in range(285, 290)], signal_indexes=(285,))
        trade, = report["operacoes"]
        self.assertEqual(trade["motivo"], "fim_do_dia")
        self.assertEqual(trade["candle_saida_ms"] + STEP, date_to_ms(date(2026, 11, 4)))
