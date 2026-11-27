from typing import Protocol

from app.models import Asset, Candle, Ticker


class ExchangeError(Exception):
    """A request failed after retries."""


class ExchangeAdapter(Protocol):
    name: str
    market: str

    def list_assets(self) -> list[Asset]:
        """Tradable (not delisted) assets."""

    def get_snapshot(self) -> dict[str, Ticker]:
        """Last price and 24h volume for every tradable asset."""

    def get_candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Candles with open time in [start_ms, end_ms], oldest first.
        The last one may still be in progress."""
