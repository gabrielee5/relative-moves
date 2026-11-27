"""Pure calculations. No I/O."""
from bisect import bisect_right

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
