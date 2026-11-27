from app.models import Candle
from app.strategies.volatility_system import (
    RESAMPLE_MS, WARMUP_BARS, VolatilitySystem, entry_flags, replay, resample_3h, talib_atr,
)

H = 3_600_000
F, T = False, True


def test_replay_open_add_reverse():
    longs  = [F, T, T, F, T, F, T, F, F, F, T]
    shorts = [F, F, F, F, F, F, F, T, T, F, F]
    assert replay(longs, shorts) == [
        (1, "long", "open", False),
        (4, "long", "add", False),      # new signal after a gap; repeat on row 2 is no order
        # row 6: third entry not allowed (max 2)
        (7, "short", "reverse", True),  # row 8 repeat: no order
        (10, "long", "reverse", True),
    ]


def test_replay_add_needs_flag_off_on_previous_row():
    longs  = [T, T, T, T]
    shorts = [F, F, F, F]
    assert replay(longs, shorts) == [(0, "long", "open", False)]


def test_resample_3h_keeps_partial_buckets_and_aligns_to_utc():
    cs = [Candle(t * H, 1, 2 + t, 0.5, 1 + t, 1) for t in range(1, 7)]  # starts at 01:00
    bars = resample_3h(cs)
    assert [b.t for b in bars] == [0, 3 * H, 6 * H]
    assert (bars[0].o, bars[0].h, bars[0].c, bars[0].v) == (1, 4, 3, 2)


def test_talib_atr_seed_and_wilder():
    h, l, c = [2.0] * 20, [1.0] * 20, [1.5] * 20
    atr = talib_atr(h, l, c, 14)
    assert atr[:14] == [None] * 14 and atr[14] == 1.0 and atr[19] == 1.0


def test_signal_waits_for_3h_close_and_uses_previous_atr():
    # flat 3h bars (TR = 1), then one 3h bar whose close jumps +10
    cs = []
    for k in range(30):
        for j in range(3):
            c = 100 + (10 if k == 29 else 0)
            cs.append(Candle((k * 3 + j) * H, 100, 100.5, 99.5, c if j == 2 or k != 29 else 100, 1))
    longs, shorts, chg, atr = entry_flags(cs)
    last_bucket_end = 29 * 3 + 2  # 1h row where the jump bar's values are merged
    assert not any(longs[:last_bucket_end])
    assert longs[last_bucket_end] and chg[last_bucket_end] == 10
    assert atr[last_bucket_end - 1] == 2.0  # ATR(14)*2 of the flat bars, from the previous row
    assert not any(shorts)


def test_evaluate_skips_warmup_and_unknown_state():
    # long move, short move, long move, each once, all inside the warmup: no signals
    cs = [Candle(i * H, 1, 1.01, 0.99, 1, 1) for i in range(3 * 40)]
    assert VolatilitySystem().evaluate("X", cs) == []
    assert WARMUP_BARS * RESAMPLE_MS > len(cs) * H
