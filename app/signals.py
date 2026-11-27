"""Signal store (SQLite). Re-running a strategy over the same candles adds only new signals."""
import sqlite3
import threading
from pathlib import Path

from app.strategies import Signal

COLUMNS = ("id", "strategy", "symbol", "t", "side", "price", "stop", "target", "note", "detected_at", "backfill")


class SignalStore:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS signals (
                 id INTEGER PRIMARY KEY,
                 strategy TEXT, symbol TEXT, t INTEGER, side TEXT,
                 price REAL, stop REAL, target REAL, note TEXT,
                 detected_at INTEGER, backfill INTEGER,
                 UNIQUE (strategy, symbol, t, side)
               )"""
        )

    def add(self, strategy: str, symbol: str, signals: list[Signal], detected_at: int, live_after: int) -> int:
        """Insert signals not stored yet. Returns the number of new rows.

        For signals already stored, only the note is refreshed (so a note format change
        reaches old rows); time, price, detection time and backfill flag never change.

        A signal whose trigger candle opened before `live_after` is stored as backfill
        (found in history, e.g. on the first run or after downtime). The UI alerts only on live ones.
        """
        rows = [
            (strategy, symbol, s.t, s.side, s.price, s.stop, s.target, s.note, detected_at, int(s.t < live_after))
            for s in signals
        ]
        with self.lock, self.db:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO signals (strategy, symbol, t, side, price, stop, target, note, detected_at, backfill)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            added = self.db.total_changes - before
            self.db.executemany(
                "UPDATE signals SET note=? WHERE strategy=? AND symbol=? AND t=? AND side=? AND note IS NOT ?",
                [(s.note, strategy, symbol, s.t, s.side, s.note) for s in signals],
            )
            return added

    def list(self, after_id: int = 0, strategy: str | None = None, symbol: str | None = None, limit: int = 500) -> list[dict]:
        """Newest first (by trigger candle time, then id)."""
        sql = f"SELECT {', '.join(COLUMNS)} FROM signals WHERE id>?"
        args: list = [after_id]
        if strategy:
            sql += " AND strategy=?"
            args.append(strategy)
        if symbol:
            sql += " AND symbol=?"
            args.append(symbol)
        sql += " ORDER BY t DESC, id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(sql, args).fetchall()
        return [dict(zip(COLUMNS, r)) | {"backfill": bool(r[-1])} for r in rows]
