"""Estratégia da Fase 1 aplicada apenas a candles fechados."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import INTERVALS, Candle


@dataclass(frozen=True)
class Strategy:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    volume_period: int = 20
    volume_multiplier: float = 1.2
    rsi_min: float = 50.0
    rsi_max: float = 68.0
    atr_stop_multiple: float = 1.5


def add_indicators(df: pd.DataFrame, cfg: Strategy) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    change = out["close"].diff()
    rsi_options = {"alpha": 1 / cfg.rsi_period, "adjust": False, "min_periods": cfg.rsi_period}
    gain = change.clip(lower=0).ewm(**rsi_options).mean()
    loss = (-change.clip(upper=0)).ewm(**rsi_options).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - 100 / (1 + rs)
    out.loc[(loss == 0) & (gain > 0), "rsi"] = 100.0
    out.loc[(loss == 0) & (gain == 0), "rsi"] = 50.0
    previous_close = out["close"].shift(1)
    true_range = pd.concat([
        out["high"] - out["low"],
        (out["high"] - previous_close).abs(),
        (out["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = true_range.ewm(
        alpha=1 / cfg.atr_period, adjust=False, min_periods=cfg.atr_period,
    ).mean()
    out["volume_mean_previous"] = out["volume"].shift(1).rolling(cfg.volume_period).mean()
    return out


def as_frame(candles: list[Candle]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "close_time": candle.close_time,
            "high": float(candle.high),
            "low": float(candle.low),
            "close": float(candle.close),
            "volume": float(candle.volume),
        }
        for candle in candles
    ])


def signal_distances(fast: list[Candle], slow: list[Candle]) -> dict[int, float]:
    """Mapeia fechamento do sinal para distância do stop em USDT."""
    config = Strategy()
    fast_indicators = add_indicators(as_frame(fast), config)
    slow_indicators = add_indicators(as_frame(slow), config)
    slow_indicators["trend"] = (
        (slow_indicators["ema_fast"] > slow_indicators["ema_slow"])
        & (slow_indicators.index >= config.ema_slow - 1)
    )
    aligned = pd.merge_asof(
        fast_indicators, slow_indicators[["close_time", "trend"]], on="close_time",
        direction="backward", tolerance=INTERVALS["15m"] - 1,
    )
    candidates = aligned.loc[
        aligned["trend"].eq(True)
        & (aligned["ema_fast"] > aligned["ema_slow"])
        & aligned["rsi"].between(config.rsi_min, config.rsi_max)
        & (aligned["volume"] > aligned["volume_mean_previous"] * config.volume_multiplier)
        & (aligned["atr"] > 0)
    ]
    return {
        int(row.close_time): float(row.atr) * config.atr_stop_multiple
        for row in candidates.itertuples()
    }
