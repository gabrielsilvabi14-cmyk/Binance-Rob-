"""Leitura de dados públicos da Binance Spot."""
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
INTERVALS = {"5m": 300_000, "15m": 900_000}
BASE_URL = "https://data-api.binance.vision"


class DataError(ValueError):
    """Dados indisponíveis ou inconsistentes."""


@dataclass(frozen=True)
class Candle:
    open_time: int
    close_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def parse(cls, row):
        candle = cls(int(row[0]), int(row[6]), *(Decimal(x) for x in row[1:6]))
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if (any(not value.is_finite() for value in values)
                or candle.low <= 0 or candle.volume < 0
                or not candle.low <= min(candle.open, candle.close)
                <= max(candle.open, candle.close) <= candle.high):
            raise DataError("Candle com preço ou volume inválido.")
        return candle


@dataclass(frozen=True)
class MarketRules:
    lot_steps: tuple[Decimal, ...]
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    max_notional: Decimal | None = None

    @classmethod
    def parse(cls, entry):
        if entry["status"] != "TRADING" or not entry["isSpotTradingAllowed"]:
            raise DataError(f"{entry['symbol']} está indisponível no Spot.")
        filters = {item["filterType"]: item for item in entry["filters"]}
        lots = (filters["LOT_SIZE"], filters.get("MARKET_LOT_SIZE", {}))
        bounds = (filters.get("MIN_NOTIONAL", {}), filters.get("NOTIONAL", {}))
        return cls(
            tuple(Decimal(item["stepSize"]) for item in lots if Decimal(item.get("stepSize", "0")) > 0),
            max(Decimal(item.get("minQty", "0")) for item in lots),
            min(Decimal(item["maxQty"]) for item in lots if Decimal(item.get("maxQty", "0")) > 0),
            max(Decimal(item.get("minNotional", "0")) for item in bounds),
            min((Decimal(item["maxNotional"]) for item in bounds
                 if Decimal(item.get("maxNotional", "0")) > 0), default=None),
        )


class BinanceData:
    def _get(self, path, **params):
        url = f"{BASE_URL}/api/v3/{path}?{urlencode(params)}"
        for attempt in range(3):
            try:
                with urlopen(url, timeout=15) as response:
                    return json.load(response)
            except HTTPError as exc:
                # Respeita limites da API, inclusive quando não há Retry-After.
                if exc.code in (418, 429):
                    wait = exc.headers.get("Retry-After")
                    detail = f" Retry-After: {wait}s." if wait and wait.isdigit() else ""
                    raise DataError(f"Limite da Binance ({exc.code}).{detail}") from exc
                if exc.code < 500 or attempt == 2:
                    raise DataError(f"Binance retornou HTTP {exc.code} em {path}.") from exc
            except (URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise DataError(f"Não foi possível consultar {path} na Binance.") from exc
            except ValueError as exc:
                raise DataError(f"JSON inválido da Binance em {path}.") from exc
            time.sleep(2 ** attempt)

    def server_time(self) -> int:
        return int(self._get("time")["serverTime"])

    def market_rules(self) -> dict[str, MarketRules]:
        response = self._get("exchangeInfo", symbols=json.dumps(SYMBOLS, separators=(",", ":")))
        markets = {entry["symbol"]: MarketRules.parse(entry) for entry in response["symbols"]}
        if set(markets) != set(SYMBOLS):
            raise DataError("A Binance não retornou as regras dos cinco pares.")
        return markets

    def candles(self, symbol: str, interval: str, start: int, end: int) -> list[Candle]:
        """Retorna candles fechados no intervalo alinhado [start, end)."""
        step = INTERVALS[interval]
        if symbol not in SYMBOLS or start >= end or start % step or end % step:
            raise DataError("Par ou janela de candles inválida.")

        candles = []
        cursor = start
        while cursor < end:
            page = self._get("klines", symbol=symbol, interval=interval,
                             startTime=cursor, endTime=end - 1,
                             limit=min(1000, (end - cursor) // step))
            if not isinstance(page, list) or not page:
                raise DataError(f"Candles ausentes em {symbol} {interval} a partir de {cursor}.")
            for row in page:
                candle = Candle.parse(row)
                if (candle.open_time != cursor or candle.close_time != cursor + step - 1
                        or candle.close_time >= end):
                    raise DataError(f"Candles fora de sequência em {symbol} {interval}.")
                candles.append(candle)
                cursor += step
        return candles
