import pytest

from app.metrics import close_at, pct_change, ranking, rebase, resample, sort_rows, time_grid
from app.models import Ticker

STEP = 300_000  # 5m
T0 = 1_700_000_000_000 // STEP * STEP


def series(closes):
    return [T0 + i * STEP for i in range(len(closes))], list(closes)


def test_close_at_picks_last_closed_candle():
    times, closes = series([10, 11, 12, 13])
    # candle i closes at T0 + (i+1)*STEP
    assert close_at(times, closes, T0 + 2 * STEP, STEP) == 11  # exactly at close of candle 1
    assert close_at(times, closes, T0 + 2 * STEP + 1, STEP) == 11  # candle 2 not closed yet
    assert close_at(times, closes, T0 + 3 * STEP, STEP) == 12


def test_close_at_before_history_is_none():
    times, closes = series([10, 11])
    assert close_at(times, closes, T0 + STEP - 1, STEP) is None
    assert close_at([], [], T0, STEP) is None


def test_close_at_rejects_stale_reference():
    times, closes = series([10, 11])  # last candle closes at T0 + 2*STEP
    assert close_at(times, closes, T0 + 5 * STEP, STEP) == 11  # 3 bars gap: allowed
    assert close_at(times, closes, T0 + 5 * STEP + 1, STEP) is None


def test_pct_change():
    assert pct_change(110, 100) == pytest.approx(10.0)
    assert pct_change(90, 100) == pytest.approx(-10.0)
    assert pct_change(100, None) is None
    assert pct_change(100, 0) is None


def test_ranking_uses_same_method_for_every_window():
    closes = [100.0] * 12 + [200.0] * 12  # 24 candles = 2h
    times, cs = series(closes)
    now = T0 + 24 * STEP
    tk = {"X": Ticker("X", 220.0, 5.0)}
    windows = {"1h": 3_600_000, "2h": 7_200_000, "7d": 7 * 86_400_000}
    (row,) = ranking(tk, {"X": (times, cs)}, now, STEP, windows)
    # now-1h = T0+12*STEP, which is the close of candle 11 (the last 100): 220/100 - 1
    assert row["chg_1h"] == pytest.approx(120.0)
    assert row["chg_2h"] is None  # now-2h is the open of the first candle: nothing closed yet
    assert row["chg_7d"] is None  # no history that far back
    assert row["volume_24h"] == 5.0


def test_ranking_asset_without_candles():
    (row,) = ranking({"Y": Ticker("Y", 1.0, 0)}, {}, T0, STEP, {"1h": 3_600_000})
    assert row["chg_1h"] is None


def test_sort_rows_puts_missing_last_both_ways():
    rows = [{"s": "a", "k": 1.0}, {"s": "b", "k": None}, {"s": "c", "k": -2.0}, {"s": "d", "k": 3.0}]
    assert [r["s"] for r in sort_rows(rows, "k")] == ["d", "a", "c", "b"]
    assert [r["s"] for r in sort_rows(rows, "k", descending=False)] == ["c", "a", "d", "b"]


# --- comparison chart ---

def test_time_grid_aligns_to_step():
    H = 3_600_000
    assert time_grid(10 * H + 1, 13 * H, H) == [11 * H, 12 * H, 13 * H]
    assert time_grid(10 * H, 11 * H - 1, H) == [10 * H]


def test_resample_5m_to_15m_takes_close_at_bar_end():
    times, closes = series([1, 2, 3, 4, 5, 6])  # six 5m candles from T0
    grid = [T0 + 3 * STEP, T0 + 6 * STEP]  # ends of two 15m bars
    assert resample(times, closes, STEP, grid) == [3, 6]


def test_rebase_to_first_available_price():
    out = rebase([100.0, 110.0, None, 90.0])
    assert out[0] == 0.0 and out[1] == pytest.approx(10.0) and out[2] is None and out[3] == pytest.approx(-10.0)


def test_rebase_asset_listed_mid_window():
    out = rebase([None, None, 50.0, 75.0])
    assert out[:2] == [None, None] and out[2] == 0.0 and out[3] == pytest.approx(50.0)
    assert rebase([None, None]) == [None, None]
