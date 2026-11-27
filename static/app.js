"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 15_000;          // local API, cheap
const OHLC_RELOAD_MS = 60_000;   // matches the server-side OHLC cache TTL
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
};

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

function renderBody() {
  const rows = visibleRows();
  const windows = state.cfg.windows;
  $("tbody").innerHTML = rows
    .map(
      (r, i) => `<tr data-sym="${r.symbol}" class="${r.symbol === state.selected ? "selected" : ""}">
        <td>${i + 1}</td><td class="sym">${r.symbol}</td><td>${fmtPrice(r.last)}</td>
        ${windows.map((w) => `<td>${fmtPct(r["chg_" + w])}</td>`).join("")}
        <td>${fmtVol(r.volume_24h)}</td></tr>`
    )
    .join("");
  $("count").textContent = `${rows.length} / ${state.rows.length}`;
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  const parts = [`Prices ${fmtTime(s.snapshot_at)}`, `candles ${fmtTime(s.synced_at?.[state.cfg.ranking_interval])}`];
  for (const [iv, p] of Object.entries(s.progress || {})) parts.push(`syncing ${iv} ${p}`);
  let html = parts.join(" · ");
  const err = s.last_error;
  if (err && Date.now() - err.at < 15 * 60_000) {
    const msg = err.message.replace(/"/g, "&quot;");
    html += ` · <span class="warn" title="${fmtTime(err.at)}: ${msg}">⚠ error</span>`;
  }
  $("status").innerHTML = html;
}

// ---------- chart ----------

let chart = null;
let candleSeries = null;
let ohlcLoadedAt = 0;
let ohlcToken = 0;

function initChart() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: v("--bg") }, textColor: v("--muted"), fontSize: 11, attributionLogo: false },
    grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } },
    rightPriceScale: { borderColor: v("--border") },
    timeScale: { borderColor: v("--border"), timeVisible: true, secondsVisible: false },
  });
  candleSeries = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: v("--up"), downColor: v("--down"),
    wickUpColor: v("--up"), wickDownColor: v("--down"),
    borderVisible: false,
  });
}

function renderTfButtons() {
  $("tf-buttons").innerHTML = state.cfg.ohlc_timeframes
    .map((tf) => `<button type="button" data-tf="${tf}" class="${tf === state.tf ? "active" : ""}">${tf}</button>`)
    .join("");
}

function renderChartTitle() {
  const r = state.rows.find((x) => x.symbol === state.selected);
  if (!r) return;
  const pcts = state.cfg.windows.map((w) => `<span class="muted">${w}</span> ${fmtPct(r["chg_" + w])}`).join(" ");
  $("chart-title").className = "";
  $("chart-title").innerHTML = `<b>${r.symbol}</b><span>${fmtPrice(r.last)}</span><span>${pcts}</span>`;
}

async function loadOhlc(fit) {
  if (!state.selected) return;
  const token = ++ohlcToken;
  const res = await fetch(`/api/ohlc?symbol=${encodeURIComponent(state.selected)}&tf=${state.tf}`);
  if (token !== ohlcToken) return; // a newer request superseded this one
  if (!res.ok) {
    $("chart-title").innerHTML += ` <span class="warn">chart failed: ${res.status}</span>`;
    return;
  }
  const data = await res.json();
  if (token !== ohlcToken || !data.length) return;
  if (!chart) initChart();
  candleSeries.applyOptions({ priceFormat: priceFormat(data[data.length - 1].c) });
  candleSeries.setData(data.map((c) => ({ time: c.t / 1000 + TZ_SHIFT, open: c.o, high: c.h, low: c.l, close: c.c })));
  if (fit) chart.timeScale().fitContent();
  ohlcLoadedAt = Date.now();
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
    return;
  }
  renderBody();
  renderStatus();
  renderChartTitle();
  const dl = $("symbols");
  if (dl.children.length !== state.rows.length) {
    dl.innerHTML = state.rows.map((r) => r.symbol).sort().map((s) => `<option value="${s}">`).join("");
  }
  if (state.selected && Date.now() - ohlcLoadedAt > OHLC_RELOAD_MS) loadOhlc(false);
}

async function refreshNow() {
  const btn = $("refresh");
  const before = state.status?.snapshot_at;
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  await fetch("/api/refresh", { method: "POST" });
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    await loadRanking();
    if (state.status?.snapshot_at !== before) break;
  }
  ohlcLoadedAt = 0;
  loadOhlc(false);
  if (currentView() === "compare") loadCompare(false);
  btn.disabled = false;
  btn.textContent = "Refresh";
}

// ---------- compare view ----------

const PALETTE = ["#58a6ff", "#f0883e", "#d2a8ff", "#3fb950", "#ffd33d", "#ff7b72", "#39c5cf", "#f778ba"];
const GREY = "rgba(125, 133, 144, 0.28)";

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
  tip.innerHTML = `<b style="color:${color}">${sym}</b>${fmtPct(v)} <span class="muted">${when}</span>`;
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
  const line = (s) => hl.includes(s)
    ? { label: s, stroke: hlColor(s), width: 2, spanGaps: true }
    : { label: s, stroke: GREY, width: 1, spanGaps: true };

  const el = $("cmp-chart");
  cmp.tip = document.createElement("div"); // created before uPlot: its hooks can fire during setup
  cmp.tip.className = "cmp-tip";
  cmp.tip.hidden = true;
  const axis = { stroke: "#7d8590", grid: { stroke: "#161b22" }, ticks: { stroke: "#222a35" }, font: "11px -apple-system, sans-serif" };
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
        c.strokeStyle = "#3a4350";
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
        const color = cmp.highlights.includes(u.series[s].label) ? hlColor(u.series[s].label) : "#e6edf3";
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

// ---------- views ----------

function currentView() {
  return location.hash === "#compare" ? "compare" : "ranking";
}

function showView() {
  const view = currentView();
  for (const b of $("tabs").children) b.classList.toggle("active", b.dataset.view === view);
  $("view-ranking").hidden = view !== "ranking";
  $("view-compare").hidden = view !== "compare";
  if (view === "compare") loadCompare(!cmp.plot);
}

async function poll() {
  await loadRanking();
  if (currentView() === "compare") loadCompare(false);
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
    const view = e.target.dataset?.view;
    if (view) location.hash = view === "compare" ? "compare" : "";
  });
  window.addEventListener("hashchange", showView);

  cmp.highlights = (await (await fetch("/api/highlights")).json()).symbols;
  initCompare();
  renderHead();
  renderTfButtons();
  await loadRanking();
  showView();
  setInterval(poll, POLL_MS);
}

main();
