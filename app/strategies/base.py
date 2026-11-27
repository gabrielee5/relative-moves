"""Strategy interface. A strategy reads closed candles and returns signals. No I/O."""
from dataclasses import dataclass

from app.models import Candle, duration_ms


@dataclass(frozen=True, slots=True)
class Signal:
    t: int  # open time of the candle that triggered the signal, epoch ms
    side: str  # "long" | "short"
    price: float  # entry price
    stop: float | None = None
    target: float | None = None
    note: str = ""


class Strategy:
    """Subclass this and add an instance to STRATEGIES in app/strategies/__init__.py.

    `source` must be a cached [[series]] interval (config.toml). `timeframe` is the bar
    size `evaluate` sees: the source candles are resampled to it. Only closed candles
    are passed in, so a signal never changes after it appears.
    """

    id: str = ""
    name: str = ""
    description: str = ""
    source: str = "1h"
    timeframe: str = "1h"

    def evaluate(self, symbol: str, candles: list[Candle]) -> list[Signal]:
        """Return every signal in `candles` (oldest first). Called for backfill and live runs."""
        raise NotImplementedError

    def info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "description": self.description,
            "source": self.source,
            "timeframe": self.timeframe,
        }


def resample_ohlc(candles: list[Candle], src_ms: int, step_ms: int) -> list[Candle]:
    """Aggregate `candles` (bar size `src_ms`, oldest first) into `step_ms` bars.

    A bucket is kept only when every source candle in it is present, so a bar
    never shows a partial (still open or gappy) period.
    """
    if step_ms == src_ms:
        return list(candles)
    need = step_ms // src_ms
    out: list[Candle] = []
    bucket: list[Candle] = []
    start = None
    for c in candles:
        b = c.t - c.t % step_ms
        if b != start:
            if bucket and len(bucket) == need:
                out.append(_merge(start, bucket))
            bucket, start = [], b
        bucket.append(c)
    if bucket and len(bucket) == need:
        out.append(_merge(start, bucket))
    return out


def _merge(t: int, cs: list[Candle]) -> Candle:
    return Candle(
        t=t,
        o=cs[0].o,
        h=max(c.h for c in cs),
        l=min(c.l for c in cs),
        c=cs[-1].c,
        v=sum(c.v for c in cs),
    )


def bars(strategy: Strategy, candles: list[Candle]) -> list[Candle]:
    """Source candles resampled to the strategy timeframe."""
    return resample_ohlc(candles, duration_ms(strategy.source), duration_ms(strategy.timeframe))
