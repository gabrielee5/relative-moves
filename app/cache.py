"""SQLite candle cache. Stores closed candles only."""
import sqlite3
import threading
from pathlib import Path

from app.models import Candle


class CandleCache:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS candles (
                 symbol TEXT, interval TEXT, t INTEGER,
                 o REAL, h REAL, l REAL, c REAL, v REAL,
                 PRIMARY KEY (symbol, interval, t)
               ) WITHOUT ROWID"""
        )

    def upsert(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        rows = [(symbol, interval, c.t, c.o, c.h, c.l, c.c, c.v) for c in candles]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows)

    def last_ts_all(self, interval: str) -> dict[str, int]:
        """{symbol: open time of newest cached candle}."""
        with self.lock:
            cur = self.db.execute(
                "SELECT symbol, MAX(t) FROM candles WHERE interval=? GROUP BY symbol", (interval,)
            )
            return dict(cur.fetchall())

    def closes(self, interval: str, since_ms: int) -> dict[str, tuple[list[int], list[float]]]:
        """{symbol: (open times, closes)}, oldest first."""
        with self.lock:
            cur = self.db.execute(
                "SELECT symbol, t, c FROM candles WHERE interval=? AND t>=? ORDER BY symbol, t",
                (interval, since_ms),
            )
            rows = cur.fetchall()
        out: dict[str, tuple[list[int], list[float]]] = {}
        for sym, t, c in rows:
            ts, cs = out.setdefault(sym, ([], []))
            ts.append(t)
            cs.append(c)
        return out

    def candles(self, interval: str, since_ms: int) -> dict[str, list[Candle]]:
        """{symbol: candles}, oldest first."""
        with self.lock:
            cur = self.db.execute(
                "SELECT symbol, t, o, h, l, c, v FROM candles WHERE interval=? AND t>=? ORDER BY symbol, t",
                (interval, since_ms),
            )
            rows = cur.fetchall()
        out: dict[str, list[Candle]] = {}
        for sym, *ohlcv in rows:
            out.setdefault(sym, []).append(Candle(*ohlcv))
        return out

    def prune(self, interval: str, before_ms: int) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM candles WHERE interval=? AND t<?", (interval, before_ms))
