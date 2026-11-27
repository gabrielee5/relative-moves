"""Background refresh: live snapshot on a timer, candle series right after each close."""
import logging
import threading
import time
from dataclasses import replace
from typing import Callable

from app import metrics
from app.cache import CandleCache
from app.config import Compare, Config, Series
from app.exchanges import ExchangeAdapter, ExchangeError
from app.models import Ticker, duration_ms
from app.ratelimit import mark_background_thread
from app.signals import SignalStore
from app.strategies import STRATEGIES, Strategy, bars

log = logging.getLogger(__name__)

SYNC_DELAY_MS = 5_000  # wait a few seconds after a candle closes before fetching it
SNAPSHOT_RETRY_SEC = 30
LIVE_SLACK_MS = 10 * 60_000  # a signal is "live" if found within one sync period (+ slack) of its candle close


def now_ms() -> int:
    return int(time.time() * 1000)


def next_boundary(t_ms: int, period_ms: int) -> int:
    return (t_ms // period_ms + 1) * period_ms + SYNC_DELAY_MS


def _with_rank(note: str, rank: tuple[int, int, float] | None) -> str:
    if rank is None:
        return note
    r, n, vol = rank
    tag = f"vol #{r}/{n} ({metrics.fmt_usd(vol)} 24h)"
    return f"{tag} · {note}" if note else tag


class Refresher:
    def __init__(
        self,
        adapter: ExchangeAdapter,
        cache: CandleCache,
        cfg: Config,
        signals: SignalStore | None = None,
        strategies: list[Strategy] | None = None,
    ):
        self.adapter = adapter
        self.cache = cache
        self.cfg = cfg
        self.signals = signals
        self.strategies = STRATEGIES if strategies is None else strategies
        self.windows = {w: duration_ms(w) for w in cfg.ranking_windows}
        self.tickers: dict[str, Ticker] = {}
        self.ranking: list[dict] = []
        self.status: dict = {
            "exchange": adapter.name,
            "market": adapter.market,
            "snapshot_at": None,
            "synced_at": {},     # interval -> epoch ms
            "progress": {},      # interval -> "done/total" while a sync runs
            "last_error": None,
            "strategies_at": None,
        }
        # After a restart, report the newest cached candle close until the first sync finishes.
        for s in cfg.series:
            last = cache.last_ts_all(s.interval)
            if last:
                self.status["synced_at"][s.interval] = max(last.values()) + duration_ms(s.interval)
        self._compare_memo: dict[str, tuple[tuple, dict]] = {}
        self._next_snapshot = 0.0
        self._next_sync = {s.interval: 0 for s in cfg.series}
        self._manual = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- units of work -----------------------------------------------------

    def refresh_snapshot(self) -> None:
        try:
            self.tickers = self.adapter.get_snapshot()
        except ExchangeError as e:
            self._error(f"snapshot: {e}")
            self._next_snapshot = time.time() + SNAPSHOT_RETRY_SEC
            return
        self.status["snapshot_at"] = now_ms()
        self._next_snapshot = time.time() + self.cfg.snapshot_every_sec
        self.recompute()

    def sync(self, series: Series, on_progress: Callable[[int, int], None] | None = None) -> None:
        """Fetch candles that closed since the last cached one, for every asset."""
        iv, step = series.interval, duration_ms(series.interval)
        now = now_ms()
        oldest = now - series.keep_days * 86_400_000
        last = self.cache.last_ts_all(iv)
        symbols = sorted(self.tickers)
        for i, sym in enumerate(symbols, 1):
            if self._stop.is_set():
                return
            self.status["progress"][iv] = f"{i}/{len(symbols)}"
            if on_progress:
                on_progress(i, len(symbols))
            self._maybe_snapshot()  # keep prices fresh during a long backfill
            start = max(last[sym] + step, oldest) if sym in last else oldest
            if start + step > now:
                continue  # no newly closed candle
            try:
                candles = self.adapter.get_candles(sym, iv, start, now)
            except ExchangeError as e:
                self._error(f"{sym} {iv}: {e}")
                continue  # retried on the next sync
            self.cache.upsert(sym, iv, [c for c in candles if c.t + step <= now])
        self.cache.prune(iv, oldest - step)
        self.status["progress"].pop(iv, None)
        self.status["synced_at"][iv] = now
        if iv == self.cfg.ranking_interval:
            self.recompute()
        self.run_strategies(iv)

    def run_strategies(self, interval: str | None = None) -> int:
        """Run strategies fed by `interval` (all if None) over the cached candles. Returns new signals."""
        if self.signals is None:
            return 0
        series = {s.interval: s for s in self.cfg.series}
        added = 0
        loaded: dict[str, dict] = {}
        for st in self.strategies:
            if interval is not None and st.source != interval:
                continue
            src = series.get(st.source)
            if src is None:
                self._error(f"strategy {st.id}: source {st.source!r} is not a cached [[series]]")
                continue
            now = now_ms()
            if st.source not in loaded:
                loaded[st.source] = self.cache.candles(st.source, now - src.keep_days * 86_400_000)
            # Trigger candle opened at t closes at t + timeframe. Live = closed within one sync period.
            live_after = now - duration_ms(src.sync_every) - LIVE_SLACK_MS - duration_ms(st.timeframe)
            found: dict[str, list] = {}
            for sym, candles in loaded[st.source].items():
                try:
                    found[sym] = st.evaluate(sym, bars(st, candles))
                except Exception as e:  # a strategy bug must not stop the refresher
                    self._error(f"strategy {st.id} {sym}: {e!r}")
            tf = duration_ms(st.timeframe)
            # Volume rank at signal time: 24h notional volume up to the trigger candle close, across all assets.
            ends = {sig.t + tf for sigs in found.values() for sig in sigs}
            ranks = metrics.volume_ranks(loaded[st.source], ends, duration_ms(st.source))
            for sym, sigs in found.items():
                if not sigs:
                    continue
                sigs = [replace(sig, note=_with_rank(sig.note, ranks[sig.t + tf].get(sym))) for sig in sigs]
                added += self.signals.add(st.id, sym, sigs, now, live_after)
        self.status["strategies_at"] = now_ms()
        if added:
            log.info("strategies: %d new signals", added)
        return added

    def recompute(self) -> None:
        step = duration_ms(self.cfg.ranking_interval)
        now = now_ms()
        since = now - max(self.windows.values()) - (metrics.MAX_GAP_BARS + 1) * step
        series = self.cache.closes(self.cfg.ranking_interval, since)
        self.ranking = metrics.ranking(self.tickers, series, now, step, self.windows)
        self.status["ranking_at"] = now

    def compare(self, c: Compare) -> dict:
        """Rebased % series for every asset over `c.lookback`, plus the live price as the last point."""
        key = (self.status["snapshot_at"], self.status["synced_at"].get(c.source))
        memo = self._compare_memo.get(c.lookback)
        if memo and memo[0] == key:
            return memo[1]
        src, now = duration_ms(c.source), now_ms()
        start = now - duration_ms(c.lookback)
        # First point is exactly now - lookback (same reference as the ranking table),
        # then bars aligned to `step`. So the chart's last value equals the table's % change.
        grid = [start] + metrics.time_grid(start + 1, now, duration_ms(c.step))
        cached = self.cache.closes(c.source, grid[0] - (metrics.MAX_GAP_BARS + 1) * src)
        series = {}
        for sym, tk in self.tickers.items():
            if sym not in cached:
                continue
            prices = metrics.resample(*cached[sym], src, grid)
            if all(p is None for p in prices):
                continue
            series[sym] = [None if x is None else round(x, 3) for x in metrics.rebase(prices + [tk.last])]
        result = {
            "lookback": c.lookback,
            "step": c.step,
            "times": [g // 1000 for g in grid] + [now // 1000],
            "series": series,
            "missing": sorted(set(self.tickers) - set(series)),
        }
        self._compare_memo[c.lookback] = (key, result)
        return result

    # --- scheduling --------------------------------------------------------

    def _maybe_snapshot(self) -> None:
        if time.time() >= self._next_snapshot:
            self.refresh_snapshot()

    def _error(self, msg: str) -> None:
        log.warning(msg)
        self.status["last_error"] = {"at": now_ms(), "message": msg}

    def run_forever(self) -> None:
        mark_background_thread()
        self.run_strategies()  # backfill from the cache before the first sync
        while not self._stop.is_set():
            manual = self._manual.is_set()
            self._manual.clear()
            if manual:
                self.refresh_snapshot()
            else:
                self._maybe_snapshot()
            for s in self.cfg.series:
                if self.tickers and (manual or now_ms() >= self._next_sync[s.interval]):
                    self.sync(s)
                    self._next_sync[s.interval] = next_boundary(now_ms(), duration_ms(s.sync_every))
            self._manual.wait(timeout=1.0)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="refresher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._manual.set()

    def refresh_now(self) -> None:
        self._manual.set()
