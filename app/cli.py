"""CLI: python -m app.cli rank [--by 24h] [--asc] [--top 30] [--min-vol 1e6] [--no-sync]"""
import argparse
import sys

from app.cache import CandleCache
from app.config import load_config
from app.exchanges import make_adapter
from app.metrics import sort_rows
from app.ratelimit import RateLimiter
from app.refresher import Refresher


def fmt_pct(x: float | None) -> str:
    return f"{x:+8.2f}%" if x is not None else "       —"


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rank", help="print assets ranked by % change")
    r.add_argument("--by", default="24h")
    r.add_argument("--asc", action="store_true", help="worst performers first")
    r.add_argument("--top", type=int, default=30)
    r.add_argument("--min-vol", type=float, default=0.0, help="min 24h volume (USD)")
    r.add_argument("--no-sync", action="store_true", help="use cached candles only")
    args = p.parse_args()

    cfg = load_config()
    if args.by not in cfg.ranking_windows:
        p.error(f"--by must be one of {cfg.ranking_windows}")
    adapter = make_adapter(cfg.exchange, cfg.market, RateLimiter(cfg.max_weight_per_min))
    ref = Refresher(adapter, CandleCache(cfg.db_path), cfg)

    ref.refresh_snapshot()
    if not ref.tickers:
        sys.exit(f"snapshot failed: {ref.status['last_error']}")
    if not args.no_sync:
        series = next(s for s in cfg.series if s.interval == cfg.ranking_interval)
        ref.sync(series, on_progress=lambda i, n: print(f"\rsync {series.interval} {i}/{n}", end="", file=sys.stderr))
        print(file=sys.stderr)

    rows = [x for x in ref.ranking if x["volume_24h"] >= args.min_vol]
    rows = sort_rows(rows, f"chg_{args.by}", descending=not args.asc)[: args.top]
    head = f"{'symbol':<10}{'last':>14}" + "".join(f"{w:>10}" for w in cfg.ranking_windows) + f"{'vol 24h $M':>13}"
    print(head)
    print("-" * len(head))
    for x in rows:
        pcts = "".join(f"{fmt_pct(x['chg_' + w]):>10}" for w in cfg.ranking_windows)
        print(f"{x['symbol']:<10}{x['last']:>14.6g}{pcts}{x['volume_24h'] / 1e6:>13.2f}")


if __name__ == "__main__":
    main()
