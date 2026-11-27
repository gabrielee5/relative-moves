from app.models import Candle
from app.signals import SignalStore
from app.strategies import Signal, Strategy, resample_ohlc

H = 3_600_000


def c(t, o, h, l, cl, v=1.0):
    return Candle(t, o, h, l, cl, v)


def test_resample_merges_complete_buckets_only():
    src = [c(i * H, 10 + i, 20 + i, 1 + i, 11 + i) for i in range(10)]  # 10 x 1h
    out = resample_ohlc(src, H, 4 * H)
    assert [b.t for b in out] == [0, 4 * H]  # last bucket (8h, 9h) is incomplete
    b = out[0]
    assert (b.o, b.h, b.l, b.c, b.v) == (10, 23, 1, 14, 4.0)


def test_resample_drops_bucket_with_gap():
    src = [c(0, 1, 1, 1, 1), c(2 * H, 1, 1, 1, 1), c(3 * H, 1, 1, 1, 1)]  # 1h missing
    assert resample_ohlc(src, H, 4 * H) == []


def test_store_dedupes_and_flags_backfill(tmp_path):
    store = SignalStore(tmp_path / "s.db")
    sigs = [Signal(1 * H, "long", 100.0, stop=95.0), Signal(5 * H, "short", 110.0)]
    assert store.add("s1", "BTC", sigs, detected_at=6 * H, live_after=4 * H) == 2
    assert store.add("s1", "BTC", sigs, detected_at=7 * H, live_after=6 * H) == 0  # same history: nothing new
    rows = store.list()
    assert [(r["t"], r["backfill"]) for r in rows] == [(5 * H, False), (1 * H, True)]
    assert rows[1]["stop"] == 95.0 and rows[0]["stop"] is None
    newest = max(r["id"] for r in rows)
    assert store.add("s1", "BTC", [Signal(8 * H, "long", 120.0)], 9 * H, 7 * H) == 1
    assert [r["t"] for r in store.list(after_id=newest)] == [8 * H]
    assert store.list(strategy="other") == [] and store.list(symbol="ETH") == []


class EveryThird(Strategy):
    id = "every_third"
    source = "1h"
    timeframe = "1h"

    def evaluate(self, symbol, candles):
        return [Signal(x.t, "long", x.c) for i, x in enumerate(candles) if i % 3 == 2]


class Broken(Strategy):
    id = "broken"
    source = "1h"
    timeframe = "1h"

    def evaluate(self, symbol, candles):
        raise ValueError("bug")


def test_refresher_runs_strategies_from_cache(tmp_path):
    from app.cache import CandleCache
    from app.config import load_config
    from app.refresher import Refresher, now_ms

    cfg = load_config()
    cache = CandleCache(tmp_path / "c.db")
    now = now_ms()
    start = now - now % H - 9 * H
    cache.upsert("BTC", "1h", [c(start + i * H, 1, 2, 0.5, 1.5) for i in range(9)])
    store = SignalStore(tmp_path / "c.db")
    r = Refresher(adapter=_FakeAdapter(), cache=cache, cfg=cfg, signals=store, strategies=[Broken(), EveryThird()])
    assert r.run_strategies("5m") == 0  # no strategy fed by 5m
    assert r.run_strategies("1h") == 3
    assert store.list()[0]["note"].startswith("vol #1/1 ")
    assert r.status["last_error"]["message"].startswith("strategy broken")
    rows = store.list()
    assert rows[0]["t"] == start + 8 * H and rows[0]["backfill"] is False  # just closed: live
    assert all(x["backfill"] for x in rows[1:])
    assert r.run_strategies() == 0


class _FakeAdapter:
    name = "fake"
    market = "perp"


def test_volume_ranks_window_and_order():
    from app.metrics import volume_ranks

    day = 24 * H
    cs = {
        "A": [c(i * H, 1, 1, 1, 2.0, v=10) for i in range(48)],   # 20 / h
        "B": [c(i * H, 1, 1, 1, 1.0, v=30) for i in range(48)],   # 30 / h
        "C": [c(i * H, 1, 1, 1, 1.0, v=1000) for i in range(10)], # big, but only in the first 10h
    }
    r = volume_ranks(cs, {day, 2 * day}, H)
    assert r[day]["C"] == (1, 3, 10_000.0)
    assert r[day]["B"] == (2, 3, 30 * 24)
    assert r[2 * day] == {"B": (1, 2, 30 * 24), "A": (2, 2, 20 * 24)}  # C has no candle in the window


def test_store_refreshes_note_only(tmp_path):
    store = SignalStore(tmp_path / "s.db")
    store.add("s1", "BTC", [Signal(H, "long", 100.0, note="old")], detected_at=2 * H, live_after=0)
    assert store.add("s1", "BTC", [Signal(H, "long", 999.0, note="new")], detected_at=9 * H, live_after=9 * H) == 0
    (row,) = store.list()
    assert (row["note"], row["price"], row["detected_at"], row["backfill"]) == ("new", 100.0, 2 * H, False)
