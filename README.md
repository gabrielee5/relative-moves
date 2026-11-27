# Relative Moves

A local dashboard that ranks crypto assets by relative move over different time windows.
Data comes from the Hyperliquid public Info API (perpetuals). No account or API key is needed.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Run

```sh
make dev     # install deps, start the server on http://127.0.0.1:8000
make test    # unit tests
make rank    # CLI: print the top 30 assets by 24h change
```

CLI options:

```sh
uv run python -m app.cli rank --by 1h|24h|7d [--asc] [--top 30] [--min-vol 1000000] [--no-sync]
```

### First start

The first start backfills the candle cache. This takes time because of the exchange rate limit:

- 5m candles, 8 days (ranking): about 10 min.
- 1h candles, 31 days (30d comparison): about 6 min more.

Later starts fetch only the candles that closed while the app was off.
The cache is in `data/<exchange>_<market>.db`. You can delete it at any time.

## How it works

```
exchange adapter ──► rate limiter ──► SQLite cache ──► metrics ──► FastAPI ──► browser
```

- **Snapshot** (every 5 min, one request): live price and 24h volume for all assets.
- **Candle series**: each series syncs a few seconds after its `sync_every` boundary. A sync fetches only newly closed candles.
- **% change** for every window uses one formula:
  `live price / close of the 5m candle that closed at or before (now − window) − 1`.
  The reference is at most 5 min older than the exact window start. When the cache has no
  candle near that time, the value is empty ("—"). It is never estimated.
- **Rate limits**: a token bucket in request-weight units (`max_weight_per_min`, default 1000 out of
  Hyperliquid's 1200/min per IP). A candle request costs `20 + ceil(candles / 60)`.
  On HTTP 429 or 5xx the client backs off exponentially (3 retries). A failed asset is retried at the next sync.

### UI (http://127.0.0.1:8000)

- **Table**: click a column header to sort, click again to reverse. Assets with no value go last.
  Filter by symbol text and by minimum 24h volume.
- **Chart**: click a row to open its candlestick chart (15m / 1h / 4h / 1d, last 300 candles, local time).
  Chart data is fetched on demand and cached for 60 s.
- **Status line**: time of the last price snapshot and the last candle sync, sync progress, and the last error (hover).
- **Refresh**: fetches a new snapshot and any newly closed candles.
- **Compare** tab (`/#compare`): every asset as a line, rebased to % change from the start of the lookback.
  - Lookback 24h (15m bars), 7d (1h bars) or 30d (4h bars). Each comes from the cached 5m or 1h series,
    with no extra API calls. The last point is the live price.
  - The first point is the same reference price the table uses. So each line ends at the table's % change.
    An asset listed during the window is rebased to its first price.
  - Highlight list: type or pick a symbol. Highlighted assets draw as colored lines on top; all others are thin and grey.
    The list is saved in `data/highlights.json`. Each chip shows the asset's current % move.
  - "All assets" / "Highlighted only" toggles the grey lines.
  - Hover near a line to see its name, % move and time.

The candle sync runs in the background and never uses the last 10% of the rate budget.
That reserve keeps chart clicks fast during a long sync.

### API

| Endpoint | Description |
|---|---|
| `GET /api/ranking` | Rows with last price, 24h volume, and % change per window |
| `GET /api/status` | Last snapshot / sync times, sync progress, last error |
| `POST /api/refresh` | Refresh now (snapshot + any newly closed candles) |
| `GET /api/ohlc?symbol=BTC&tf=1h` | Recent candles for one asset |
| `GET /api/config` | Settings the UI needs |
| `GET /api/compare?lookback=7d` | Rebased % series for all assets |
| `GET /api/highlights`, `PUT /api/highlights` | Read / replace the highlight list (`{"symbols": [...]}`) |

## Configuration

Settings are in `config.toml`: exchange, rate budget, refresh interval, candle series, ranking windows,
chart timeframes and file paths. Use `RM_CONFIG=/path/to/other.toml` to load another file.

## Adding an exchange

1. Write `app/exchanges/<name>.py` with a class that implements `ExchangeAdapter` (`app/exchanges/base.py`).
   It returns the normalized `Asset`, `Ticker` and `Candle` types from `app/models.py`.
2. Register it in `ADAPTERS` in `app/exchanges/__init__.py`.
3. Set `[exchange] name` in `config.toml`.

No other code calls an exchange directly.
