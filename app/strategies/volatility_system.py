"""Volatility System: port of the freqtrade strategy `VolatilitySystem` (timeframe 1h, can_short).

Original logic (freqtrade + technical.util), replicated exactly:

1. Resample the 1h candles to 3h bars, buckets aligned to 00:00 UTC (pandas resample "180min",
   label="left"). Partial buckets are kept, empty ones dropped.
2. On the 3h bars: atr = TA-Lib ATR(14) * 2.0 and close_change = close - previous 3h close.
3. `resampled_merge`: the values of a 3h bar attach to the 1h row that opens 2h after the bucket
   start (the last 1h candle of the bucket), then forward-fill. So a 3h value is known exactly when
   its 3h bar closes, never earlier.
4. Entry, on every 1h row:
     enter_long  = close_change  > atr.shift(1)
     enter_short = -close_change > atr.shift(1)
   shift(1) is one 1h row, not one 3h bar. On the last hour of a 3h bar the change is compared with
   the previous bar's ATR; on the next two hours with the ATR that includes the new bar. So one
   move can stay true for several consecutive hours.
5. Exits: exit_long = enter_short, exit_short = enter_long (always in the market after the first
   entry). minimal_roi 100 and stoploss -1 never trigger before liquidation, so they are not modelled.
6. Position adjustment: one extra entry (same stake) on a new signal in the trade direction, i.e.
   the flag is 1 on this row and was not 1 on the previous row, at most 2 entries per trade.
   Initial stake is half of the normal stake (custom_stake_amount), leverage 3.

This app shows the ORDERS the freqtrade bot would place, by replaying that position logic over the
flags: open (when flat), add (2nd entry), reverse (exit + entry in the other direction on the same
candle, as freqtrade does when exit and entry signals coincide). Repeated flags while a position is
already open place no order, so they are not listed.

Differences on purpose, history only (no effect on new signals):
- The first WARMUP_BARS 3h bars of the cached history emit nothing: the ATR is a recursive (Wilder)
  average, so its first values depend on where the data starts.
- Nothing is emitted before the first reversal in the history: before it, the bot's position
  (flat / one entry / two entries) is unknown, so "open" vs "add" vs "no order" is unknown.
"""
from app.models import Candle
from app.strategies.base import Signal, Strategy

H = 3_600_000
RESAMPLE_MS = 3 * H
ATR_PERIOD = 14
ATR_MULT = 2.0
WARMUP_BARS = 100  # 3h bars; Wilder ATR seed weight (13/14)^100 ≈ 0.06 %
MAX_ENTRIES = 2


def talib_atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    """TA-Lib ATR, same arithmetic order: first value at index `period` is the mean of
    TRANGE[1..period], then Wilder smoothing prev = (prev * (period - 1) + tr) / period."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    tr = [None] + [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, n)
    ]
    total = 0.0
    for i in range(1, period + 1):
        total += tr[i]
    prev = total / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = prev * (period - 1)
        prev = prev + tr[i]
        prev = prev / period
        out[i] = prev
    return out


def resample_3h(candles: list[Candle]) -> list[Candle]:
    """pandas resample("180min", label="left").agg(ohlc).dropna(): partial buckets kept."""
    out: list[Candle] = []
    for c in candles:
        b = c.t - c.t % RESAMPLE_MS
        if out and out[-1].t == b:
            last = out[-1]
            out[-1] = Candle(b, last.o, max(last.h, c.h), min(last.l, c.l), c.c, last.v + c.v)
        else:
            out.append(Candle(b, c.o, c.h, c.l, c.c, c.v))
    return out


def entry_flags(candles: list[Candle]) -> tuple[list[bool], list[bool], list[float | None], list[float | None]]:
    """Per 1h candle: (enter_long, enter_short, close_change, atr) as in the freqtrade dataframe."""
    bars = resample_3h(candles)
    atr = talib_atr([b.h for b in bars], [b.l for b in bars], [b.c for b in bars], ATR_PERIOD)
    atr = [None if a is None else a * ATR_MULT for a in atr]
    change = [None] + [bars[k].c - bars[k - 1].c for k in range(1, len(bars))]
    # resampled_merge: bucket start + 180m - 60m == open time of the bucket's last 1h candle
    by_merge_t = {b.t + RESAMPLE_MS - H: k for k, b in enumerate(bars)}

    n = len(candles)
    m_atr: list[float | None] = [None] * n
    m_chg: list[float | None] = [None] * n
    cur_atr = cur_chg = None
    for i, c in enumerate(candles):
        k = by_merge_t.get(c.t)
        if k is not None:  # ffill: a NaN cell keeps the previous value
            if atr[k] is not None:
                cur_atr = atr[k]
            if change[k] is not None:
                cur_chg = change[k]
        m_atr[i], m_chg[i] = cur_atr, cur_chg

    longs, shorts = [False] * n, [False] * n
    for i in range(1, n):
        a, ch = m_atr[i - 1], m_chg[i]  # atr.shift(1): previous 1h row
        if a is None or ch is None:
            continue
        longs[i] = ch * 1 > a
        shorts[i] = ch * -1 > a
    return longs, shorts, m_chg, m_atr


def replay(longs: list[bool], shorts: list[bool]) -> list[tuple[int, str, str, bool]]:
    """Orders of the freqtrade bot for these flags: (row, side, action, known).

    `known` is False until the first reversal: before it the result depends on the bot's
    position when the history starts (assumed flat here).
    """
    out = []
    pos, entries, known = None, 0, False
    for i, (L, S) in enumerate(zip(longs, shorts)):
        if pos == "long" and S or pos == "short" and L:  # exit signal + opposite entry, same candle
            pos, entries, known = ("short" if S else "long"), 1, True
            out.append((i, pos, "reverse", known))
        elif pos is not None and (L if pos == "long" else S):
            prev = i > 0 and (longs[i - 1] if pos == "long" else shorts[i - 1])
            if not prev and entries < MAX_ENTRIES:  # adjust_trade_position: new signal, max 2 entries
                entries += 1
                out.append((i, pos, "add", known))
        elif pos is None and (L or S):
            pos, entries = ("long" if L else "short"), 1
            out.append((i, pos, "open", known))
    return out


ACTION_NOTE = {
    "open": "open {side} (½ stake)",
    "add": "add {side} (+½ stake)",
    "reverse": "reverse to {side} (exit + ½ stake)",
}


class VolatilitySystem(Strategy):
    id = "volatility_system"
    name = "Volatility System"
    description = (
        "3h close change beyond 2×ATR(14) of 3h bars, checked every 1h. Always in the market: "
        "add ½ stake on a new signal, reverse on the opposite signal. Leverage 3, no stop."
    )
    source = "1h"
    timeframe = "1h"

    def evaluate(self, symbol: str, candles: list[Candle]) -> list[Signal]:
        if not candles:
            return []
        longs, shorts, chg, atr = entry_flags(candles)
        emit_from = candles[0].t - candles[0].t % RESAMPLE_MS + WARMUP_BARS * RESAMPLE_MS
        return [
            Signal(
                t=candles[i].t,
                side=side,
                price=candles[i].c,
                note=f"{ACTION_NOTE[action].format(side=side)} · Δ3h {chg[i]:+.6g} vs 2×ATR {atr[i - 1]:.6g}",
            )
            for i, side, action, known in replay(longs, shorts)
            if known and candles[i].t >= emit_from
        ]
