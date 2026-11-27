"""Pure calculations. No I/O."""
from bisect import bisect_left, bisect_right

from app.models import Ticker

# A reference candle may close at most this many bars before its target time.
# Hyperliquid emits a candle for every bar (no gaps, checked on thin assets),
# so a larger gap means the cache is not synced that far: show nothing.
MAX_GAP_BARS = 3


def close_at(times: list[int], closes: list[float], ts: int, step: int) -> float | None:
    """Close of the last candle that closed at or before `ts`.

    `times` are candle open times (sorted). A candle opened at t closes at t + step.
    """
    i = bisect_right(times, ts - step) - 1
    if i < 0 or ts - (times[i] + step) > MAX_GAP_BARS * step:
        return None
    return closes[i]


def pct_change(last: float | None, ref: float | None) -> float | None:
    if last is None or not ref:
        return None
    return (last / ref - 1.0) * 100.0


def ranking(
    tickers: dict[str, Ticker],
    series: dict[str, tuple[list[int], list[float]]],
    now_ms: int,
    step_ms: int,
    windows: dict[str, int],
) -> list[dict]:
    """One row per ticker: last price, 24h volume and % change for each window.

    Every window uses the same formula: live last price vs. the candle close at now - window.
    """
    rows = []
    for sym, tk in tickers.items():
        times, closes = series.get(sym, ([], []))
        row = {"symbol": sym, "last": tk.last, "volume_24h": tk.volume_24h}
        for name, ms in windows.items():
            row[f"chg_{name}"] = pct_change(tk.last, close_at(times, closes, now_ms - ms, step_ms))
        rows.append(row)
    return rows


def resample(times: list[int], closes: list[float], src_step: int, grid: list[int]) -> list[float | None]:
    """Close price at each grid time (end of each target bar), from candles of `src_step`."""
    return [close_at(times, closes, g, src_step) for g in grid]


def time_grid(start_ms: int, end_ms: int, step_ms: int) -> list[int]:
    """Bar close times aligned to `step_ms`, from the first boundary >= start to the last <= end."""
    first = -(-start_ms // step_ms) * step_ms
    return list(range(first, end_ms + 1, step_ms))


def rebase(prices: list[float | None]) -> list[float | None]:
    """% change from the first available price. An asset that starts later
    (e.g. listed mid-window) is rebased to its own first price."""
    base = next((p for p in prices if p), None)
    if base is None:
        return [None] * len(prices)
    return [None if p is None else (p / base - 1.0) * 100.0 for p in prices]


def sort_rows(rows: list[dict], key: str, descending: bool = True) -> list[dict]:
    """Sort by `key`; rows with a missing value go last in both directions."""
    present = [r for r in rows if r.get(key) is not None]
    missing = [r for r in rows if r.get(key) is None]
    return sorted(present, key=lambda r: r[key], reverse=descending) + missing


def volume_ranks(
    candles: dict[str, list], ends: set[int], step: int, window: int = 86_400_000
) -> dict[int, dict[str, tuple[int, int, float]]]:
    """{end: {symbol: (rank, n_assets, quote volume)}} by notional volume in [end - window, end).

    `candles`: {symbol: Candle list, oldest first, bar size `step`}. Notional = base volume × close.
    Rank 1 = highest volume. An asset with no candle in the window is not ranked.
    """
    pre: dict[str, tuple[list[int], list[float]]] = {}
    for sym, cs in candles.items():
        ts, acc, total = [], [0.0], 0.0
        for c in cs:
            ts.append(c.t)
            total += c.v * c.c
            acc.append(total)
        pre[sym] = (ts, acc)
    out = {}
    for end in ends:
        vols = []
        for sym, (ts, acc) in pre.items():
            lo = bisect_left(ts, end - window)      # first candle opening at/after window start
            hi = bisect_left(ts, end - step + 1)    # candles that closed by `end`
            if hi > lo:
                vols.append((acc[hi] - acc[lo], sym))
        vols.sort(reverse=True)
        out[end] = {sym: (i + 1, len(vols), v) for i, (v, sym) in enumerate(vols)}
    return out


def fmt_usd(v: float) -> str:
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= div:
            return f"${v / div:.1f}{unit}"
    return f"${v:.0f}"
