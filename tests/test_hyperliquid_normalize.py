"""Normalization of Hyperliquid payloads (shapes copied from live responses)."""
import httpx
import pytest

from app.exchanges.base import ExchangeError
from app.exchanges.hyperliquid import (
    HyperliquidAdapter,
    candle_weight,
    parse_candle,
    parse_snapshot,
    parse_universe,
)
from app.models import Candle, Ticker
from app.ratelimit import RateLimiter

META = {
    "universe": [
        {"szDecimals": 5, "name": "BTC", "maxLeverage": 40, "marginTableId": 56},
        {"szDecimals": 0, "name": "OLD", "maxLeverage": 3, "isDelisted": True},
        {"szDecimals": 2, "name": "THIN", "maxLeverage": 3},
    ]
}
CTXS = [
    {"markPx": "62145.0", "midPx": "62134.5", "prevDayPx": "60666.0", "dayNtlVlm": "2004504410.37"},
    {"markPx": "0.01", "midPx": None, "prevDayPx": "0.01", "dayNtlVlm": "0.0"},
    {"markPx": "1.5", "midPx": None, "prevDayPx": "1.4", "dayNtlVlm": "1200.5"},
]
RAW_CANDLE = {
    "t": 1777770000000, "T": 1777773599999, "s": "BTC", "i": "1h",
    "o": "78510.0", "c": "78204.0", "h": "78510.0", "l": "78113.0", "v": "949.76931", "n": 16928,
}


def test_parse_universe_skips_delisted():
    assert [a.symbol for a in parse_universe(META)] == ["BTC", "THIN"]
    assert parse_universe(META)[0].market == "perp"


def test_parse_snapshot_prefers_mid_and_falls_back_to_mark():
    snap = parse_snapshot([META, CTXS])
    assert set(snap) == {"BTC", "THIN"}
    assert snap["BTC"] == Ticker("BTC", 62134.5, 2004504410.37)
    assert snap["THIN"].last == 1.5  # midPx null -> markPx


def test_parse_candle():
    assert parse_candle(RAW_CANDLE) == Candle(1777770000000, 78510.0, 78510.0, 78113.0, 78204.0, 949.76931)


def test_candle_weight():
    assert candle_weight(1) == 21
    assert candle_weight(60) == 21
    assert candle_weight(61) == 22
    assert candle_weight(5000) == 104


def _adapter(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HyperliquidAdapter(RateLimiter(10_000, initial_fraction=1.0), client=client)


def test_get_candles_sends_documented_request():
    seen = {}

    def handler(req: httpx.Request):
        import json
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=[RAW_CANDLE])

    out = _adapter(handler).get_candles("BTC", "1h", 1, 2)
    assert seen == {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": 1, "endTime": 2}}
    assert out[0].c == 78204.0


def test_client_error_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(422, text="bad coin")

    with pytest.raises(ExchangeError):
        _adapter(handler).get_snapshot()
    assert len(calls) == 1


def test_spot_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        HyperliquidAdapter(RateLimiter(100), market="spot")
