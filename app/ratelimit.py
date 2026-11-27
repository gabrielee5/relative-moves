import threading
import time

_local = threading.local()


def mark_background_thread() -> None:
    """Requests from the calling thread may not use the reserve kept for UI requests."""
    _local.background = True


class RateLimiter:
    """Token bucket in request-weight units. acquire() blocks until the weight fits.

    Background threads leave `reserve` tokens untouched, so an interactive request
    (e.g. a chart click) does not queue behind a long candle sync.
    """

    def __init__(self, per_minute: float, initial_fraction: float = 0.2, reserve_fraction: float = 0.1):
        self.capacity = per_minute
        self.rate = per_minute / 60.0  # tokens per second
        self.reserve = per_minute * reserve_fraction
        self.tokens = per_minute * initial_fraction  # don't burst right after a restart
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def _try_take(self, weight: float, floor: float) -> float:
        """Take `weight` if at least `floor` tokens remain afterwards. Returns 0, or seconds to wait."""
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens - weight >= floor:
                self.tokens -= weight
                return 0.0
            return (weight + floor - self.tokens) / self.rate

    def acquire(self, weight: float) -> None:
        floor = self.reserve if getattr(_local, "background", False) else 0.0
        weight = min(weight, self.capacity - floor)
        while (wait := self._try_take(weight, floor)) > 0:
            time.sleep(wait)

    def drain(self) -> None:
        """Call on HTTP 429: the server thinks we are over budget, so start from empty."""
        with self.lock:
            self.tokens = 0.0
            self.updated = time.monotonic()
