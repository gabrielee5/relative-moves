from app.ratelimit import RateLimiter


def test_background_leaves_reserve_for_interactive():
    lim = RateLimiter(600, initial_fraction=0.2, reserve_fraction=0.1)  # 120 tokens, reserve 60
    assert lim._try_take(50, floor=lim.reserve) == 0  # 70 left
    assert lim._try_take(20, floor=lim.reserve) > 0   # would leave 50 < 60: background waits
    assert lim._try_take(20, floor=0) == 0            # interactive goes through


def test_wait_time_matches_refill_rate():
    lim = RateLimiter(600, initial_fraction=0.0)  # 10 tokens/s
    assert abs(lim._try_take(20, floor=0) - 2.0) < 0.05
