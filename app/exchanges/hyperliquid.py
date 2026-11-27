"""Hyperliquid public Info API adapter (https://api.hyperliquid.xyz/info).

Weights (docs, Rate limits page): 1200/min per IP. meta, metaAndAssetCtxs and
candleSnapshot cost 20; candleSnapshot adds weight per 60 candles returned.
Only the most recent 5000 candles per (coin, interval) are available.
"""
import logging
import random
import time

import httpx

from app.exchanges.base import ExchangeError
from app.models import Asset, Candle, Ticker, duration_ms
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)

URL = "https://api.hyperliquid.xyz/info"
BASE_WEIGHT = 20
MAX_CANDLES = 5000
MAX_RETRIES = 3


# --- normalization (pure, unit-tested) -------------------------------------

def parse_universe(meta: dict, market: str = "perp") -> list[Asset]:
    return [Asset(a["name"], market) for a in meta["universe"] if not a.get("isDelisted")]


def parse_snapshot(meta_and_ctxs: list) -> dict[str, Ticker]:
    """metaAndAssetCtxs -> {symbol: Ticker}. Universe and ctxs are index-aligned."""
    meta, ctxs = meta_and_ctxs
    out = {}
    for a, c in zip(meta["universe"], ctxs):
        if a.get("isDelisted"):
            continue
        # midPx tracks the order book (closest to candle closes); it is null when the book is empty.
        px = c.get("midPx") or c.get("markPx")
        if px is None:
            continue
        out[a["name"]] = Ticker(a["name"], float(px), float(c.get("dayNtlVlm") or 0))
    return out


def parse_candle(raw: dict) -> Candle:
    return Candle(
        t=int(raw["t"]),
        o=float(raw["o"]),
        h=float(raw["h"]),
        l=float(raw["l"]),
        c=float(raw["c"]),
        v=float(raw["v"]),
    )


def candle_weight(n_candles: int) -> int:
    return BASE_WEIGHT + -(-n_candles // 60)  # ceil


# --- adapter ---------------------------------------------------------------

class HyperliquidAdapter:
    name = "hyperliquid"

    def __init__(self, limiter: RateLimiter, market: str = "perp", client: httpx.Client | None = None):
        if market != "perp":
            # Spot: list pairs via spotMeta; candle coin is "PURR/USDC" or "@{universe index}".
            raise NotImplementedError("Hyperliquid spot is not implemented yet")
        self.market = market
        self.limiter = limiter
        self.http = client or httpx.Client(timeout=15)

    def _post(self, body: dict, weight: int):
        err: object = None
        for attempt in range(MAX_RETRIES + 1):
            self.limiter.acquire(weight)
            try:
                r = self.http.post(URL, json=body)
            except httpx.TransportError as e:
                err = e
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    self.limiter.drain()
                elif r.status_code < 500:
                    raise ExchangeError(f"{body['type']}: HTTP {r.status_code} {r.text[:200]}")
                err = f"HTTP {r.status_code}"
            if attempt < MAX_RETRIES:
                delay = 2 * 2**attempt + random.uniform(0, 1)
                log.warning("%s failed (%s), retry in %.1fs", body["type"], err, delay)
                time.sleep(delay)
        raise ExchangeError(f"{body['type']} failed after {MAX_RETRIES} retries: {err}")

    def list_assets(self) -> list[Asset]:
        return parse_universe(self._post({"type": "meta"}, BASE_WEIGHT), self.market)

    def get_snapshot(self) -> dict[str, Ticker]:
        return parse_snapshot(self._post({"type": "metaAndAssetCtxs"}, BASE_WEIGHT))

    def get_candles(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        # One request is enough: ranges beyond 5000 candles are not served by the API anyway.
        n = min(MAX_CANDLES, max(1, (end_ms - start_ms) // duration_ms(interval) + 1))
        body = {
            "type": "candleSnapshot",
            "req": {"coin": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
        return [parse_candle(x) for x in self._post(body, candle_weight(n))]
