"""Adapter registry. Adding an exchange = one new module + one line here."""
from app.exchanges.base import ExchangeAdapter, ExchangeError
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.ratelimit import RateLimiter

ADAPTERS = {
    "hyperliquid": HyperliquidAdapter,
}


def make_adapter(name: str, market: str, limiter: RateLimiter) -> ExchangeAdapter:
    try:
        cls = ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown exchange {name!r}; known: {sorted(ADAPTERS)}") from None
    return cls(limiter, market=market)


__all__ = ["ExchangeAdapter", "ExchangeError", "make_adapter"]
