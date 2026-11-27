import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.models import duration_ms

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Series:
    interval: str
    keep_days: int
    sync_every: str


@dataclass(frozen=True)
class Compare:
    lookback: str
    step: str
    source: str


@dataclass(frozen=True)
class Config:
    exchange: str
    market: str
    max_weight_per_min: int
    snapshot_every_sec: int
    series: list[Series]
    ranking_interval: str
    ranking_windows: list[str]
    compare: list[Compare]
    ohlc_timeframes: list[str]
    data_dir: Path
    highlights_path: Path
    host: str
    port: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"{self.exchange}_{self.market}.db"


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("RM_CONFIG", ROOT / "config.toml"))
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    series = [Series(**s) for s in raw["series"]]
    cfg = Config(
        exchange=raw["exchange"]["name"],
        market=raw["exchange"]["market"],
        max_weight_per_min=raw["exchange"]["max_weight_per_min"],
        snapshot_every_sec=raw["refresh"]["snapshot_every_sec"],
        series=series,
        ranking_interval=raw["ranking"]["interval"],
        ranking_windows=raw["ranking"]["windows"],
        compare=[Compare(**c) for c in raw["compare"]],
        ohlc_timeframes=raw["ui"]["ohlc_timeframes"],
        data_dir=ROOT / raw["paths"]["data_dir"],
        highlights_path=ROOT / raw["paths"]["highlights"],
        host=raw["server"]["host"],
        port=raw["server"]["port"],
    )
    if cfg.ranking_interval not in {s.interval for s in series}:
        raise ValueError(f"ranking.interval {cfg.ranking_interval!r} must be one of the [[series]]")
    keep = {s.interval: s.keep_days * 86_400_000 for s in series}
    for c in cfg.compare:
        if c.source not in keep or duration_ms(c.lookback) > keep[c.source]:
            raise ValueError(f"compare {c.lookback}: source {c.source!r} must be a [[series]] that keeps the lookback")
    return cfg
