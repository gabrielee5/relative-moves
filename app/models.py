"""Exchange-independent types. The UI and logic only see these."""
from dataclasses import dataclass

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def duration_ms(s: str) -> int:
    """'5m' -> 300000, '24h' -> 86400000, '7d' -> 604800000."""
    return int(s[:-1]) * _UNIT_MS[s[-1]]


@dataclass(frozen=True, slots=True)
class Asset:
    symbol: str
    market: str  # "perp" | "spot"


@dataclass(frozen=True, slots=True)
class Ticker:
    symbol: str
    last: float
    volume_24h: float  # quote-currency notional


@dataclass(frozen=True, slots=True)
class Candle:
    t: int  # open time, epoch ms
    o: float
    h: float
    l: float
    c: float
    v: float  # base volume
