"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 15_000;          // local API, cheap
const OHLC_RELOAD_MS = 60_000;   // matches the server-side OHLC cache TTL
const RIGHT_GAP_BARS = 15;       // empty space after the last candle
const TZ_SHIFT = -new Date().getTimezoneOffset() * 60; // show chart times in local time

const state = {
  cfg: null,
  rows: [],
  status: null,
  sort: { key: "chg_24h", desc: true },
  filter: "",
  minVol: 0,
  selected: null,
  tf: null,
  heatScale: {},        // window -> |chg| that maps to full tint (95th percentile of visible rows)
  lastSnap: null,       // snapshot_at of the last rendered status, for the live-dot pulse
};

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ---------- formatting ----------

function fmtPrice(p) {
  if (p == null) return "—";
  if (p >= 1000) return p.toLocaleString("en-US", { maximumFractionDigits: 1 });
  return String(Number(p.toPrecision(6)));
}

function fmtPct(x) {
  if (x == null) return '<span class="muted">—</span>';
  const cls = x > 0 ? "up" : x < 0 ? "down" : "";
  return `<span class="${cls}">${x > 0 ? "+" : ""}${x.toFixed(2)}%</span>`;
}

// Heat tint for a % change: class + strength in [0, 1], relative to the window's scale.
function heat(x, w) {
  if (x == null || x === 0) return { cls: "", h: 0 };
  const scale = state.heatScale[w] || 1;
  return { cls: x > 0 ? "hu" : "hd", h: Math.min(1, Math.abs(x) / scale) ** 1.4 };
}

function fmtVol(v) {
  if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(0) + "K";
  return v.toFixed(0);
}

function fmtTime(ms) {
  return ms ? new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

function priceFormat(p) {
  // enough decimals to show ~5 significant digits
  const decimals = p >= 1 ? Math.max(2, 5 - Math.floor(Math.log10(p)) - 1) : Math.ceil(-Math.log10(p)) + 3;
  const d = Math.min(decimals, 10);
  return { type: "price", precision: d, minMove: 10 ** -d };
}

// ---------- table ----------

function columns() {
  return [
    { key: "rank", label: "#", sortable: false },
    { key: "symbol", label: "Symbol" },
    { key: "last", label: "Last" },
    ...state.cfg.windows.map((w) => ({ key: `chg_${w}`, label: w })),
    { key: "volume_24h", label: "Vol 24h" },
  ];
}

function renderHead() {
  $("thead").innerHTML = columns()
    .map((c) => {
      const sorted = state.sort.key === c.key;
      const arrow = sorted ? (state.sort.desc ? " ▼" : " ▲") : "";
      return `<th data-key="${c.key}" class="${sorted ? "sorted" : ""}">${c.label}${arrow}</th>`;
    })
    .join("");
}

function visibleRows() {
  const q = state.filter.trim().toUpperCase();
  const { key, desc } = state.sort;
  const rows = state.rows.filter((r) => r.symbol.toUpperCase().includes(q) && r.volume_24h >= state.minVol);
  const present = rows.filter((r) => r[key] != null);
  const missing = rows.filter((r) => r[key] == null); // always last
  present.sort((a, b) => {
    const cmp = typeof a[key] === "string" ? a[key].localeCompare(b[key]) : a[key] - b[key];
    return desc ? -cmp : cmp;
  });
  return present.concat(missing);
}

function updateHeatScale(rows) {
  for (const w of state.cfg.windows) {
    const abs = rows.map((r) => Math.abs(r["chg_" + w])).filter((x) => x > 0).sort((a, b) => a - b);
    state.heatScale[w] = abs.length ? abs[Math.floor((abs.length - 1) * 0.95)] : 1;
  }
}

function heatCell(x, w) {
  const { cls, h } = heat(x, w);
  return `<td class="heat ${cls}" style="--h:${h.toFixed(2)}">${fmtPct(x)}</td>`;
}

function renderBody() {
  const rows = visibleRows();
  const windows = state.cfg.windows;
  updateHeatScale(rows);
  $("tbody").innerHTML = rows
    .map(
      (r, i) => `<tr data-sym="${r.symbol}" class="${r.symbol === state.selected ? "selected" : ""}">
        <td>${i + 1}</td><td class="sym">${r.symbol}</td><td class="last">${fmtPrice(r.last)}</td>
        ${windows.map((w) => heatCell(r["chg_" + w], w)).join("")}
        <td class="vol">${fmtVol(r.volume_24h)}</td></tr>`
    )
    .join("");
  $("count").textContent = `${rows.length} / ${state.rows.length}`;
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  const parts = [`Prices ${fmtTime(s.snapshot_at)}`, `candles ${fmtTime(s.synced_at?.[state.cfg.ranking_interval])}`];
  const syncing = Object.entries(s.progress || {});
  for (const [iv, p] of syncing) parts.push(`syncing ${iv} ${p}`);
  let html = parts.join(" · ");
  const err = s.last_error;
  const hasErr = err && Date.now() - err.at < 15 * 60_000;
  if (hasErr) {
    const msg = err.message.replace(/"/g, "&quot;");
    html += ` · <span class="warn" title="${fmtTime(err.at)}: ${msg}">⚠ error</span>`;
  }
  $("status").innerHTML = html;
  setDot(hasErr ? "error" : syncing.length ? "syncing" : "fresh");
  if (state.lastSnap != null && s.snapshot_at !== state.lastSnap) {
    const dot = $("live-dot");
    dot.classList.remove("pulse");
    void dot.offsetWidth; // restart the animation
    dot.classList.add("pulse");
  }
  state.lastSnap = s.snapshot_at;
}

function setDot(kind) {
  const dot = $("live-dot");
  for (const k of ["fresh", "syncing", "error"]) dot.classList.toggle(k, k === kind);
}

// ---------- chart ----------

// One candlestick chart in `el`. load() fetches /api/ohlc; markers and price lines are optional.
function makeOhlcChart(el) {
  let chart = null;
  let series = null;
  let markers = null;
  let lines = [];
  let token = 0;
  let pending = { marks: [], lines: [] }; // applied after the next load
  const c = { loadedAt: 0, bars: [] };

  function init() {
    const v = cssVar;
    chart = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { color: v("--bg") }, textColor: v("--muted"), fontSize: 11, fontFamily: v("--font"), attributionLogo: false },
      grid: { vertLines: { color: v("--grid") }, horzLines: { color: v("--grid") } },
      crosshair: { vertLine: { color: v("--line-strong"), labelBackgroundColor: v("--raised") }, horzLine: { color: v("--line-strong"), labelBackgroundColor: v("--raised") } },
      rightPriceScale: { borderColor: v("--line") },
      timeScale: { borderColor: v("--line"), timeVisible: true, secondsVisible: false, rightOffset: RIGHT_GAP_BARS },
    });
    series = chart.addSeries(LightweightCharts.CandlestickSeries, {
      upColor: v("--up"), downColor: v("--down"),
      wickUpColor: v("--up"), wickDownColor: v("--down"),
      borderVisible: false,
    });
    markers = LightweightCharts.createSeriesMarkers(series, []);
  }

  function apply() {
    markers.setMarkers(pending.marks);
    for (const l of lines) series.removePriceLine(l);
    lines = pending.lines.map((o) => series.createPriceLine(o));
  }

  // Returns an error message, or null on success (also null when superseded by a newer call).
  c.load = async (symbol, tf, fit) => {
    const t = ++token;
    const res = await fetch(`/api/ohlc?symbol=${encodeURIComponent(symbol)}&tf=${tf}`);
    if (t !== token) return null; // a newer request superseded this one
    if (!res.ok) return `chart failed: ${res.status}`;
    const data = await res.json();
    if (t !== token || !data.length) return null;
    if (!chart) init();
    series.applyOptions({ priceFormat: priceFormat(data[data.length - 1].c) });
    c.bars = data;
    series.setData(data.map((b) => ({ time: b.t / 1000 + TZ_SHIFT, open: b.o, high: b.h, low: b.l, close: b.c })));
    apply();
    // fitContent() would put the last candle against the price scale; leave a gap instead
    if (fit) chart.timeScale().setVisibleLogicalRange({ from: 0, to: data.length - 1 + RIGHT_GAP_BARS });
    c.loadedAt = Date.now();
    return null;
  };

  // marks: [{t (ms), side, text}] ; priceLines: lightweight-charts price line options
  c.setOverlays = (marks, priceLines) => {
    pending = {
      marks: marks
        .map((m) => ({
          time: m.t / 1000 + TZ_SHIFT,
          position: m.side === "long" ? "belowBar" : "aboveBar",
          shape: m.side === "long" ? "arrowUp" : "arrowDown",
          color: m.color,
          text: m.text || "",
        }))
        .sort((a, b) => a.time - b.time),
      lines: priceLines,
    };
    if (chart) apply();
  };

  c.scrollTo = (tMs) => { // center the visible range on a time, keep the zoom
    if (!chart || !c.bars.length) return;
    const i = c.bars.findIndex((b) => b.t >= tMs);
    if (i < 0) return;
    const r = chart.timeScale().getVisibleLogicalRange();
    const half = r ? (r.to - r.from) / 2 : 60;
    if (r && i > r.from + 2 && i < r.to - 2) return; // already visible
    chart.timeScale().setVisibleLogicalRange({ from: i - half, to: i + half });
  };

  return c;
}

const rankChart = makeOhlcChart($("chart"));

function renderTfButtons() {
  $("tf-buttons").innerHTML = state.cfg.ohlc_timeframes
    .map((tf) => `<button type="button" data-tf="${tf}" class="${tf === state.tf ? "active" : ""}">${tf}</button>`)
    .join("");
}

function pills(r) {
  return `<span class="pills">${state.cfg.windows.map((w) => {
    const x = r["chg_" + w];
    const { cls, h } = heat(x, w);
    return `<span class="pill ${cls}" style="--h:${h.toFixed(2)}"><span class="k">${w}</span>${fmtPct(x)}</span>`;
  }).join("")}</span>`;
}

function renderChartTitle() {
  const r = state.rows.find((x) => x.symbol === state.selected);
  $("chart-empty").hidden = !!state.selected;
  if (!r) return;
  $("chart-title").innerHTML = `<span class="sym">${r.symbol}</span><span class="px">${fmtPrice(r.last)}</span>${pills(r)}`;
}

async function loadOhlc(fit) {
  if (!state.selected) return;
  const err = await rankChart.load(state.selected, state.tf, fit);
  if (err) $("chart-title").innerHTML += ` <span class="warn">${err}</span>`;
}

function selectAsset(sym) {
  state.selected = sym;
  renderBody();
  renderChartTitle();
  loadOhlc(true);
}

// ---------- data ----------

async function loadRanking() {
  try {
    const res = await fetch("/api/ranking");
    const data = await res.json();
    state.rows = data.rows;
    state.status = data.status;
  } catch (e) {
    $("status").innerHTML = `<span class="warn">server unreachable</span>`;
    setDot("error");
    return;
  }
  renderBody();
  renderStatus();
  renderChartTitle();
  const dl = $("symbols");
  if (dl.children.length !== state.rows.length) {
    dl.innerHTML = state.rows.map((r) => r.symbol).sort().map((s) => `<option value="${s}">`).join("");
  }
  if (state.selected && Date.now() - rankChart.loadedAt > OHLC_RELOAD_MS) loadOhlc(false);
}

async function refreshNow() {
  const btn = $("refresh");
  const before = state.status?.snapshot_at;
  btn.disabled = true;
  btn.classList.add("spinning");
  $("refresh-label").textContent = "Refreshing…";
  await fetch("/api/refresh", { method: "POST" });
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    await loadRanking();
    if (state.status?.snapshot_at !== before) break;
  }
  loadOhlc(false);
  if (currentView() === "compare") loadCompare(false);
  loadSignals();
  if (strat.selected) loadStratChart(false);
  btn.disabled = false;
  btn.classList.remove("spinning");
  $("refresh-label").textContent = "Refresh";
}

// ---------- compare view ----------

const PALETTE = ["#f2b33d", "#5aa9ff", "#c792ea", "#46c28e", "#ff8a65", "#4dd0e1", "#f48fb1", "#d4e157"];
const GREY_ALPHA = "48"; // hex alpha appended to --muted for background lines

const cmp = {
  lookback: null,
  mode: "all",          // "all" | "selected"
  highlights: [],
  data: null,
  version: null,        // last timestamp of the loaded data; unchanged data is not redrawn
  plot: null,
  focused: null,        // uPlot series index under the cursor
  tip: null,            // tooltip element inside the plot
};

const hlColor = (sym) => PALETTE[cmp.highlights.indexOf(sym) % PALETTE.length];

function renderCompareControls() {
  $("lookback-buttons").innerHTML = state.cfg.compare_lookbacks
    .map((lb) => `<button type="button" data-lb="${lb}" class="${lb === cmp.lookback ? "active" : ""}">${lb}</button>`)
    .join("");
  for (const b of $("mode-buttons").children) b.classList.toggle("active", b.dataset.mode === cmp.mode);
}

function renderChips() {
  const last = (sym) => cmp.data?.series[sym]?.at(-1);
  $("hl-chips").innerHTML = cmp.highlights
    .map((s) => `<span class="chip" style="--c:${hlColor(s)}"><b>${s}</b>${fmtPct(last(s))}` +
                `<button type="button" data-remove="${s}" title="Remove">×</button></span>`)
    .join("");
}

async function saveHighlights() {
  const res = await fetch("/api/highlights", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols: cmp.highlights }),
  });
  cmp.highlights = (await res.json()).symbols;
  renderChips();
  drawCompare();
}

function addHighlight(raw) {
  const input = $("hl-input");
  const sym = state.rows.map((r) => r.symbol).find((s) => s.toUpperCase() === raw.trim().toUpperCase());
  if (!sym) {
    input.classList.add("invalid");
    return;
  }
  input.classList.remove("invalid");
  input.value = "";
  if (!cmp.highlights.includes(sym)) {
    cmp.highlights.push(sym);
    saveHighlights();
  }
}

function showEmpty(msg) {
  $("cmp-empty").textContent = msg;
  $("cmp-empty").hidden = !msg;
}

function updateTip(u) {
  const tip = cmp.tip;
  const i = u.cursor.idx;
  const s = cmp.focused;
  const v = s > 0 && i != null && u.data[s] ? u.data[s][i] : null;
  if (v == null) {
    tip.hidden = true;
    return;
  }
  const sym = u.series[s].label;
  const color = cmp.highlights.includes(sym) ? hlColor(sym) : "var(--text)";
  const when = new Date(u.data[0][i] * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  tip.style.setProperty("--c", color);
  tip.innerHTML = `<b style="color:${color}">${sym}</b>${fmtPct(v)}<span class="muted">${when}</span>`;
  tip.hidden = false;
  const x = u.cursor.left;
  const flip = x > u.over.clientWidth - 200; // keep the tooltip inside the plot
  tip.style.left = flip ? "" : `${x + 12}px`;
  tip.style.right = flip ? `${u.over.clientWidth - x + 12}px` : "";
  tip.style.top = `${Math.max(0, u.valToPos(v, "y") - 28)}px`;
}

function drawCompare() {
  const d = cmp.data;
  if (cmp.plot) {
    cmp.plot.destroy();
    cmp.plot = null;
  }
  cmp.focused = null;
  if (!d) return;
  const hl = cmp.highlights;
  let syms = Object.keys(d.series);
  if (!syms.length) {
    showEmpty("No candles cached for this lookback yet: the background sync is still running.");
    return;
  }
  if (cmp.mode === "selected") syms = syms.filter((s) => hl.includes(s));
  if (!syms.length) {
    showEmpty("No highlighted assets: add a symbol above.");
    return;
  }
  showEmpty("");
  // Grey background lines first, highlighted lines last so they draw on top.
  const order = syms.filter((s) => !hl.includes(s)).sort().concat(hl.filter((s) => syms.includes(s)));
  const grey = cssVar("--muted") + GREY_ALPHA;
  const line = (s) => hl.includes(s)
    ? { label: s, stroke: hlColor(s), width: 2, spanGaps: true }
    : { label: s, stroke: grey, width: 1, spanGaps: true };

  const el = $("cmp-chart");
  cmp.tip = document.createElement("div"); // created before uPlot: its hooks can fire during setup
  cmp.tip.className = "cmp-tip";
  cmp.tip.hidden = true;
  const axis = { stroke: cssVar("--muted"), grid: { stroke: cssVar("--grid") }, ticks: { stroke: cssVar("--line") }, font: `11px ${cssVar("--font")}` };
  const zeroLine = cssVar("--line-strong");
  const hoverColor = cssVar("--text");
  const opts = {
    width: el.clientWidth,
    height: el.clientHeight,
    legend: { show: false },
    focus: { alpha: 0.3 },
    cursor: { focus: { prox: 24 }, points: { show: false }, drag: { x: false, y: false, setScale: false } },
    scales: { x: { time: true } },
    axes: [axis, { ...axis, size: 60, values: (u, vals) => vals.map((v) => `${v > 0 ? "+" : ""}${v}%`) }],
    series: [{}].concat(order.map(line)),
    hooks: {
      ready: [(u) => u.over.appendChild(cmp.tip)],
      drawAxes: [(u) => { // 0% reference line
        const y = Math.round(u.valToPos(0, "y", true));
        const c = u.ctx;
        c.save();
        c.strokeStyle = zeroLine;
        c.lineWidth = devicePixelRatio;
        c.beginPath();
        c.moveTo(u.bbox.left, y);
        c.lineTo(u.bbox.left + u.bbox.width, y);
        c.stroke();
        c.restore();
      }],
      draw: [(u) => { // redraw the hovered line on top, so a thin grey line becomes visible
        const s = cmp.focused;
        if (!(s > 0) || !u.series[s]) return;
        const c = u.ctx;
        const xs = u.data[0];
        const ys = u.data[s];
        const color = cmp.highlights.includes(u.series[s].label) ? hlColor(u.series[s].label) : hoverColor;
        c.save();
        c.beginPath();
        c.rect(u.bbox.left, u.bbox.top, u.bbox.width, u.bbox.height);
        c.clip();
        c.strokeStyle = color;
        c.lineWidth = 2 * devicePixelRatio;
        c.beginPath();
        let started = false;
        for (let i = 0; i < xs.length; i++) {
          if (ys[i] == null) continue; // same as spanGaps: join across missing points
          const x = u.valToPos(xs[i], "x", true);
          const y = u.valToPos(ys[i], "y", true);
          started ? c.lineTo(x, y) : c.moveTo(x, y);
          started = true;
        }
        c.stroke();
        c.restore();
      }],
      setSeries: [(u, idx) => { cmp.focused = idx; updateTip(u); }],
      setCursor: [updateTip],
    },
  };
  cmp.plot = new uPlot(opts, [d.times].concat(order.map((s) => d.series[s])), el);
}

async function loadCompare(force) {
  const res = await fetch(`/api/compare?lookback=${cmp.lookback}`);
  if (!res.ok) return;
  const d = await res.json();
  const version = `${d.lookback}:${d.times.at(-1)}:${Object.keys(d.series).length}`;
  if (!force && version === cmp.version) return; // nothing new: don't redraw under the cursor
  cmp.data = d;
  cmp.version = version;
  $("cmp-note").textContent = `${d.step} bars · ${Object.keys(d.series).length} assets` +
    (d.missing.length ? ` · ${d.missing.length} without data` : "");
  renderChips();
  drawCompare();
}

function initCompare() {
  cmp.lookback = state.cfg.compare_lookbacks.includes("7d") ? "7d" : state.cfg.compare_lookbacks[0];
  renderCompareControls();
  $("lookback-buttons").addEventListener("click", (e) => {
    const lb = e.target.dataset?.lb;
    if (!lb || lb === cmp.lookback) return;
    cmp.lookback = lb;
    renderCompareControls();
    loadCompare(true);
  });
  $("mode-buttons").addEventListener("click", (e) => {
    const mode = e.target.dataset?.mode;
    if (!mode || mode === cmp.mode) return;
    cmp.mode = mode;
    renderCompareControls();
    drawCompare();
  });
  $("hl-form").addEventListener("submit", (e) => { e.preventDefault(); addHighlight($("hl-input").value); });
  $("hl-input").addEventListener("input", (e) => {
    e.target.classList.remove("invalid");
    // picking from the datalist fires "input" with an exact symbol: add it right away
    if (state.rows.some((r) => r.symbol === e.target.value)) addHighlight(e.target.value);
  });
  $("hl-chips").addEventListener("click", (e) => {
    const sym = e.target.dataset?.remove;
    if (!sym) return;
    cmp.highlights = cmp.highlights.filter((s) => s !== sym);
    saveHighlights();
  });
  new ResizeObserver(() => {
    const el = $("cmp-chart");
    if (cmp.plot && el.clientWidth) cmp.plot.setSize({ width: el.clientWidth, height: el.clientHeight });
  }).observe($("cmp-chart"));
}

// ---------- strategies view ----------

const UNIT_MS = { m: 60_000, h: 3_600_000, d: 86_400_000, w: 604_800_000 };
const durMs = (s) => Number(s.slice(0, -1)) * UNIT_MS[s.slice(-1)];
const SEEN_KEY = "rm.signals.seenId";

const strat = {
  list: [],             // strategy info from /api/config
  signals: [],          // newest first
  maxId: 0,
  seenId: 0,            // live signals with id > seenId count in the tab badge
  hlAbove: Infinity,    // rows with id > hlAbove are highlighted as new
  viewing: false,       // Strategies tab is open: arriving signals count as seen
  filter: "",           // strategy id, "" = all
  selected: null,       // selected signal
  tf: null,
};
const stratChart = makeOhlcChart($("strat-chart"));

const stratInfo = (id) => strat.list.find((s) => s.id === id) || { id, name: id, timeframe: "1h" };

function readSeen() {
  try { return Number(localStorage.getItem(SEEN_KEY)) || 0; } catch { return 0; }
}
function writeSeen(id) {
  strat.seenId = id;
  try { localStorage.setItem(SEEN_KEY, String(id)); } catch { /* private mode: badge resets on reload */ }
}

function fmtDateTime(ms) {
  return new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Signal time = close of the trigger candle (the moment the condition was confirmed).
const signalTime = (r) => r.t + durMs(stratInfo(r.strategy).timeframe);

function renderStratFilter() {
  const opts = [`<option value="">All strategies</option>`].concat(
    strat.list.map((s) => `<option value="${s.id}" ${s.id === strat.filter ? "selected" : ""}>${s.name}</option>`)
  );
  $("strat-filter").innerHTML = opts.join("");
  const info = strat.filter ? stratInfo(strat.filter) : null;
  $("strat-desc").textContent = info ? `${info.timeframe} · ${info.description}` : "";
}

function renderBadge() {
  const n = strat.signals.filter((r) => !r.backfill && r.id > strat.seenId).length;
  $("strat-badge").textContent = n;
  $("strat-badge").hidden = n === 0;
}

function renderSignals() {
  const rows = strat.signals.filter((r) => !strat.filter || r.strategy === strat.filter);
  const sel = strat.selected?.id;
  $("sig-body").innerHTML = rows
    .map((r) => {
      const cls = [r.id === sel ? "selected" : "", !r.backfill && r.id > strat.hlAbove ? "new" : ""].join(" ");
      const side = r.side === "long" ? "up" : "down";
      return `<tr data-id="${r.id}" class="${cls}">
        <td>${fmtDateTime(signalTime(r))}</td><td class="sym">${r.symbol}</td>
        <td><span class="side ${side}">${r.side.toUpperCase()}</span></td><td>${fmtPrice(r.price)}</td>
        <td>${r.stop == null ? "—" : fmtPrice(r.stop)}</td><td>${r.target == null ? "—" : fmtPrice(r.target)}</td>
        <td class="strat-name">${stratInfo(r.strategy).name}</td><td class="note" title="${(r.note || "").replace(/"/g, "&quot;")}">${r.note || ""}</td></tr>`;
    })
    .join("");
  // hide Stop / Target when no listed signal has one (some strategies never set them)
  $("sig-table").classList.toggle("no-stop", !rows.some((r) => r.stop != null));
  $("sig-table").classList.toggle("no-target", !rows.some((r) => r.target != null));
  $("sig-count").textContent = `${rows.length} signal${rows.length === 1 ? "" : "s"}`;
  $("sig-empty").hidden = rows.length > 0;
  $("sig-empty").textContent = strat.list.length
    ? "No signals yet. Strategies run after each candle sync."
    : "No strategies registered. Add one in app/strategies/.";
}

async function loadSignals() {
  let rows;
  try {
    const res = await fetch(`/api/signals?after_id=${strat.maxId}&limit=${strat.maxId ? 500 : 5000}`);
    if (!res.ok) return;
    rows = await res.json();
  } catch {
    return;
  }
  if (!rows.length) return;
  strat.signals = rows.concat(strat.signals).sort((a, b) => b.t - a.t || b.id - a.id);
  strat.maxId = Math.max(strat.maxId, ...rows.map((r) => r.id));
  if (strat.viewing) writeSeen(strat.maxId);
  renderBadge();
  renderSignals();
  if (strat.selected && rows.some((r) => r.symbol === strat.selected.symbol)) setStratOverlays();
}

function renderStratTfButtons() {
  $("strat-tf-buttons").innerHTML = state.cfg.ohlc_timeframes
    .map((tf) => `<button type="button" data-tf="${tf}" class="${tf === strat.tf ? "active" : ""}">${tf}</button>`)
    .join("");
}

// Chart timeframe for a strategy: its own timeframe if the chart has it, else the closest one.
function chartTf(tf) {
  const tfs = state.cfg.ohlc_timeframes;
  if (tfs.includes(tf)) return tf;
  return tfs.reduce((best, x) => (Math.abs(durMs(x) - durMs(tf)) < Math.abs(durMs(best) - durMs(tf)) ? x : best));
}

function setStratOverlays() {
  const sel = strat.selected;
  if (!sel) return;
  const step = durMs(strat.tf);
  const v = cssVar;
  const marks = strat.signals
    .filter((r) => r.strategy === sel.strategy && r.symbol === sel.symbol)
    .map((r) => ({
      t: r.t - (r.t % step), // align to the chart bar that contains the trigger candle
      side: r.side,
      color: r.id === sel.id ? v("--accent") : r.side === "long" ? v("--up") : v("--down"),
      text: r.id === sel.id ? `${r.side === "long" ? "L" : "S"} ${fmtPrice(r.price)}` : r.side === "long" ? "L" : "S",
    }));
  const line = (price, color, title, style) => ({ price, color, title, lineStyle: style, lineWidth: 1, axisLabelVisible: true });
  const lines = [line(sel.price, v("--accent"), "entry", 0)];
  if (sel.stop != null) lines.push(line(sel.stop, v("--down"), "stop", 2));
  if (sel.target != null) lines.push(line(sel.target, v("--up"), "target", 2));
  stratChart.setOverlays(marks, lines);
}

function renderStratTitle() {
  const r = strat.selected;
  if (!r) return;
  const side = r.side === "long" ? "up" : "down";
  $("strat-empty").hidden = true;
  $("strat-title").innerHTML = `<span class="sym">${r.symbol}</span><span class="side ${side}">${r.side.toUpperCase()}</span>` +
    `<span class="px">${fmtPrice(r.price)}</span>` +
    `<span class="meta">${stratInfo(r.strategy).name} · ${fmtDateTime(signalTime(r))}</span>`;
}

async function loadStratChart(fit) {
  const r = strat.selected;
  if (!r) return;
  const err = await stratChart.load(r.symbol, strat.tf, fit);
  if (err) $("strat-title").innerHTML += ` <span class="warn">${err}</span>`;
  else if (fit) stratChart.scrollTo(r.t);
}

function selectSignal(id) {
  const r = strat.signals.find((x) => x.id === id);
  if (!r) return;
  const prev = strat.selected;
  strat.selected = r;
  const reload = !prev || prev.symbol !== r.symbol || prev.strategy !== r.strategy;
  if (reload) strat.tf = chartTf(stratInfo(r.strategy).timeframe);
  renderStratTfButtons();
  renderSignals();
  renderStratTitle();
  setStratOverlays();
  if (reload) loadStratChart(true);
  else stratChart.scrollTo(r.t);
}

function initStrategies() {
  strat.list = state.cfg.strategies || [];
  strat.seenId = readSeen();
  renderStratFilter();
  $("strat-filter").addEventListener("change", (e) => {
    strat.filter = e.target.value;
    renderStratFilter();
    renderSignals();
  });
  $("sig-body").addEventListener("click", (e) => {
    const id = Number(e.target.closest("tr")?.dataset.id);
    if (id) selectSignal(id);
  });
  $("strat-tf-buttons").addEventListener("click", (e) => {
    const tf = e.target.dataset?.tf;
    if (!tf || tf === strat.tf || !strat.selected) return;
    strat.tf = tf;
    renderStratTfButtons();
    setStratOverlays();
    loadStratChart(true);
  });
}

// ---------- views ----------

const VIEWS = ["ranking", "compare", "strategies"];

function currentView() {
  const v = location.hash.slice(1);
  return VIEWS.includes(v) ? v : "ranking";
}

function showView() {
  const view = currentView();
  for (const b of $("tabs").children) b.classList.toggle("active", b.dataset.view === view);
  for (const v of VIEWS) $(`view-${v}`).hidden = v !== view;
  if (view === "compare") loadCompare(!cmp.plot);
  const entering = view === "strategies" && !strat.viewing;
  strat.viewing = view === "strategies";
  if (entering) {
    strat.hlAbove = strat.seenId; // highlight what arrived since the last visit
    writeSeen(strat.maxId);
    renderBadge();
    renderSignals();
  }
}

async function poll() {
  await loadRanking();
  if (currentView() === "compare") loadCompare(false);
  await loadSignals();
  if (strat.selected && Date.now() - stratChart.loadedAt > OHLC_RELOAD_MS) loadStratChart(false);
}

// ---------- wiring ----------

async function main() {
  state.cfg = await (await fetch("/api/config")).json();
  state.tf = state.cfg.ohlc_timeframes.includes("1h") ? "1h" : state.cfg.ohlc_timeframes[0];
  $("exchange").textContent = `${state.cfg.exchange} · ${state.cfg.market}`;

  $("thead").addEventListener("click", (e) => {
    const key = e.target.closest("th")?.dataset.key;
    if (!key || key === "rank") return;
    if (state.sort.key === key) state.sort.desc = !state.sort.desc;
    else state.sort = { key, desc: key !== "symbol" };
    renderHead();
    renderBody();
  });
  $("tbody").addEventListener("click", (e) => {
    const sym = e.target.closest("tr")?.dataset.sym;
    if (sym) selectAsset(sym);
  });
  $("filter").addEventListener("input", (e) => { state.filter = e.target.value; renderBody(); });
  $("minvol").addEventListener("change", (e) => { state.minVol = Number(e.target.value); renderBody(); });
  $("refresh").addEventListener("click", refreshNow);
  $("tf-buttons").addEventListener("click", (e) => {
    const tf = e.target.dataset?.tf;
    if (!tf || tf === state.tf) return;
    state.tf = tf;
    renderTfButtons();
    loadOhlc(true);
  });

  $("tabs").addEventListener("click", (e) => {
    const view = e.target.closest("[data-view]")?.dataset.view;
    if (view) location.hash = view === "ranking" ? "" : view;
  });
  window.addEventListener("hashchange", showView);

  cmp.highlights = (await (await fetch("/api/highlights")).json()).symbols;
  initCompare();
  initStrategies();
  renderHead();
  renderTfButtons();
  await loadRanking();
  await loadSignals();
  showView();
  setInterval(poll, POLL_MS);
}

main();
