"""FastAPI app: JSON API + static frontend. Run with `make dev`."""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import highlights
from app.cache import CandleCache
from app.config import ROOT, load_config
from app.exchanges import ExchangeError, make_adapter
from app.models import duration_ms
from app.ratelimit import RateLimiter
from app.refresher import Refresher, now_ms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

OHLC_BARS = 300
OHLC_TTL_SEC = 60

cfg = load_config()
adapter = make_adapter(cfg.exchange, cfg.market, RateLimiter(cfg.max_weight_per_min))
refresher = Refresher(adapter, CandleCache(cfg.db_path), cfg)
_ohlc_cache: dict[tuple[str, str], tuple[float, list]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresher.start()
    yield
    refresher.stop()


app = FastAPI(title="Relative Moves", lifespan=lifespan)
STATIC = ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def ui_config():
    return {
        "exchange": cfg.exchange,
        "market": cfg.market,
        "windows": cfg.ranking_windows,
        "ranking_interval": cfg.ranking_interval,
        "ohlc_timeframes": cfg.ohlc_timeframes,
        "compare_lookbacks": [c.lookback for c in cfg.compare],
        "snapshot_every_sec": cfg.snapshot_every_sec,
    }


@app.get("/api/status")
def status():
    return refresher.status


@app.get("/api/ranking")
def ranking():
    return {"rows": refresher.ranking, "status": refresher.status}


@app.post("/api/refresh")
def refresh():
    refresher.refresh_now()
    return {"ok": True}


@app.get("/api/ohlc")
def ohlc(symbol: str, tf: str):
    """Recent candles for one asset, fetched on demand (not stored in SQLite)."""
    if tf not in cfg.ohlc_timeframes:
        raise HTTPException(400, f"tf must be one of {cfg.ohlc_timeframes}")
    if symbol not in refresher.tickers:
        raise HTTPException(404, f"unknown symbol {symbol!r}")
    key = (symbol, tf)
    hit = _ohlc_cache.get(key)
    if hit and time.time() - hit[0] < OHLC_TTL_SEC:
        return hit[1]
    end = now_ms()
    try:
        candles = adapter.get_candles(symbol, tf, end - OHLC_BARS * duration_ms(tf), end)
    except ExchangeError as e:
        raise HTTPException(502, str(e)) from e
    data = [{"t": c.t, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in candles]
    _ohlc_cache[key] = (time.time(), data)
    return data


@app.get("/api/compare")
def compare(lookback: str):
    c = next((c for c in cfg.compare if c.lookback == lookback), None)
    if c is None:
        raise HTTPException(400, f"lookback must be one of {[c.lookback for c in cfg.compare]}")
    return refresher.compare(c)


class Highlights(BaseModel):
    symbols: list[str]


@app.get("/api/highlights")
def get_highlights():
    return {"symbols": highlights.load(cfg.highlights_path)}


@app.put("/api/highlights")
def put_highlights(body: Highlights):
    return {"symbols": highlights.save(cfg.highlights_path, body.symbols)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.host, port=cfg.port)
