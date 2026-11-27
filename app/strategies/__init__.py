"""Strategy registry. Add one instance per strategy file to STRATEGIES."""
from app.strategies.base import Signal, Strategy, bars, resample_ohlc
from app.strategies.volatility_system import VolatilitySystem

STRATEGIES: list[Strategy] = [VolatilitySystem()]

__all__ = ["STRATEGIES", "Signal", "Strategy", "bars", "resample_ohlc"]
