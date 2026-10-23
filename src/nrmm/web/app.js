(() => {
'use strict';
const $ = (id) => document.getElementById(id);
const TOKEN = document.querySelector('meta[name=nrmm-token]').content;
const de = (n, d = 0) => Number(n).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d });
const store = {
  get(k, f) { try { const v = localStorage.getItem('nrmm.' + k); return v === null ? f : JSON.parse(v); } catch { return f; } },
  set(k, v) { try { localStorage.setItem('nrmm.' + k, JSON.stringify(v)); } catch { /* ohne Speicher weiter */ } },
};

async function api(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

/* ------------------------------------------------------------------ Zustand */
const S = {
  defs: null, byKey: {}, info: null,
  method: 'weighted',
  factors: {},               // key -> {v, locked}
  start: null, end: null, pick: null,
  gpx: null,                 // {name, points}
  net: null, route: null, sweep: null,
  view: { x: 0.5, y: 0.5, z: 4 },
  busy: false,
};
const DRAW_ORDER = ['primary', 'secondary', 'nebenstr', 'service', 'sidewalk', 'radweg_par', 'pfad_par', 'quer', 'fussg', 'radweg', 'feldweg', 'pfad'];
const ROADS = new Set(['primary', 'secondary', 'nebenstr', 'service', 'sidewalk', 'radweg_par', 'pfad_par']);

/* ------------------------------------------------------------------ Hilfen */
const D2R = Math.PI / 180;
const mx = (lon) => (lon + 180) / 360;
const my = (lat) => { const s = Math.sin(Math.max(-85, Math.min(85, lat)) * D2R); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
const lonOf = (x) => x * 360 - 180;
const latOf = (y) => { const n = Math.PI - 2 * Math.PI * y; return Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))) / D2R; };
const fmtLL = (p) => p ? `${p[0].toFixed(5)}, ${p[1].toFixed(5)}` : '';
function parseLL(s) {
  const m = String(s).trim().replace(/;/g, ',').split(/[\s,]+/).filter(Boolean).map(Number);
  if (m.length !== 2 || m.some((v) => !isFinite(v)) || Math.abs(m[0]) > 90 || Math.abs(m[1]) > 180) return null;
  return m;
}
function toast(msg, ms = 3200) {
  const t = $('toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, ms);
}
function showErr(el, msg) { el.textContent = msg || ''; el.hidden = !msg; }

/* ------------------------------------------------------------------ Hintergrund */
(function bubbles() {
  const host = $('bubbles');
  const spec = [[7, 12, 90], [18, 64, 54], [78, 8, 130], [88, 52, 70], [44, 80, 40], [62, 30, 34], [30, 20, 26], [94, 80, 56], [4, 70, 76]];
  for (const [l, t, s] of spec) {
    const b = document.createElement('div'); b.className = 'bubble';
    b.style.cssText = `left:${l}%;top:${t}%;width:${s}px;height:${s}px;opacity:${0.35 + (s % 7) / 14}`;
    host.appendChild(b);
  }
})();

/* ------------------------------------------------------------------ Gewichte */
const posToVal = (p) => Math.pow(10, p / 50);
const valToPos = (v) => Math.max(0, Math.min(100, 50 * Math.log10(Math.max(v, 1))));
function buildFactors() {
  const host = $('factors'); host.innerHTML = '';
  const saved = store.get('factors', null);
  for (const c of S.defs.classes) {
    const sv = saved && saved[c.key];
    S.factors[c.key] = { v: sv ? sv.v : c.v1, locked: sv ? !!sv.locked : false };
    const row = document.createElement('div'); row.className = 'frow'; row.style.setProperty('--c', c.color);
    row.innerHTML = `<span class="sw"></span>
      <label class="nm" for="f-${c.key}">${c.label}</label>
      <input type="number" min="0.1" max="1000" step="any" id="n-${c.key}" aria-label="Faktor ${c.label}">
      <button type="button" class="lock" id="l-${c.key}" aria-pressed="false" aria-label="${c.label} sperren" title="Wegtyp sperren">&#128274;</button>
      <input type="range" id="f-${c.key}" min="0" max="100" step="0.5" style="grid-column:2 / span 3;--thumb:${c.color}">`;
    host.appendChild(row);
    const rng = row.querySelector(`#f-${c.key}`), num = row.querySelector(`#n-${c.key}`), lk = row.querySelector(`#l-${c.key}`);
    const paint = () => {
      const f = S.factors[c.key];
      num.value = de(f.v, f.v < 10 ? 1 : 0).replace(/\./g, '').replace(',', '.');
      rng.value = valToPos(f.v); rng.disabled = f.locked; num.disabled = f.locked;
      lk.setAttribute('aria-pressed', f.locked);
    };
    rng.addEventListener('input', () => { const v = posToVal(+rng.value); S.factors[c.key].v = Math.round(v * 10) / 10; paint(); saveFactors(); });
    num.addEventListener('change', () => { const v = parseFloat(num.value); if (v > 0) S.factors[c.key].v = v; paint(); saveFactors(); });
    lk.addEventListener('click', () => { S.factors[c.key].locked = !S.factors[c.key].locked; paint(); saveFactors(); });
    paint(); row._paint = paint;
  }
}
const saveFactors = () => store.set('factors', S.factors);
$('reset-factors').addEventListener('click', () => {
  for (const c of S.defs.classes) S.factors[c.key] = { v: c.v1, locked: false };
  [...$('factors').children].forEach((r) => r._paint()); saveFactors();
});

function kValue() { const v = posToVal(+$('k').value); return v < 10 ? Math.round(v * 10) / 10 : Math.round(v); }
function paintK() {
  const k = kValue(); $('kval').textContent = de(k, k < 10 ? 1 : 0);
  const g = (key) => S.byKey[key] ? S.byKey[key].kmult * k : 0;
  $('kinfo').textContent = `Pfad 1 · Feldweg ${de(g('feldweg'), 1)} · Radweg ${de(g('radweg'), 1)} · Nebenstraße ${de(g('nebenstr'), 0)} · Hauptstraße ${de(g('secondary'), 0)} · Fernstraße ${de(g('primary'), 0)}`;
}
$('k').addEventListener('input', paintK);

function setMethod(m) {
  S.method = m;
  $('m-weighted').setAttribute('aria-pressed', m === 'weighted');
  $('m-paths').setAttribute('aria-pressed', m === 'paths');
  $('pane-weighted').hidden = m !== 'weighted'; $('pane-paths').hidden = m !== 'paths';
}
$('m-weighted').addEventListener('click', () => setMethod('weighted'));
$('m-paths').addEventListener('click', () => setMethod('paths'));

/* ------------------------------------------------------------------ Karte: Ansicht */
const cv = $('map'), ctx = cv.getContext('2d');
const off = document.createElement('canvas'), octx = off.getContext('2d');
let W = 800, H = 600, DPR = 1, snap = null, fetchTimer = null, fetchSeq = 0;

function resize() {
  const r = $('mapframe').getBoundingClientRect();
  W = Math.max(200, r.width); H = Math.max(200, r.height); DPR = window.devicePixelRatio || 1;
  cv.width = W * DPR; cv.height = H * DPR; off.width = cv.width; off.height = cv.height;
  snap = null; renderNet(); scheduleFetch(); draw();
}
new ResizeObserver(resize).observe($('mapframe'));

const scale = (z = S.view.z) => 256 * Math.pow(2, z);
const toScreen = (lat, lon, v = S.view) => { const s = scale(v.z); return [W / 2 + (mx(lon) - v.x) * s, H / 2 + (my(lat) - v.y) * s]; };
const fromScreen = (px, py) => { const s = scale(); return [latOf(S.view.y + (py - H / 2) / s), lonOf(S.view.x + (px - W / 2) / s)]; };

function fitBounds(minlat, minlon, maxlat, maxlon, pad = 0.12, rightInset = 0) {
  const r = $('mapframe').getBoundingClientRect();
  if (Math.abs(r.width - W) > 1 || Math.abs(r.height - H) > 1) resize();
  const x0 = mx(minlon), x1 = mx(maxlon), y0 = my(maxlat), y1 = my(minlat);
  const w = Math.max(x1 - x0, 1e-7), h = Math.max(y1 - y0, 1e-7), aw = Math.max(200, W - rightInset);
  const z = Math.max(2, Math.min(19, Math.log2(Math.min(aw * (1 - pad) / (256 * w), H * (1 - pad) / (256 * h)))));
  S.view = { x: (x0 + x1) / 2 + rightInset / (2 * 256 * Math.pow(2, z)), y: (y0 + y1) / 2, z };
  onViewChanged(true);
}

/* ------------------------------------------------------------------ Karte: Netz laden und zeichnen */
function scheduleFetch() { clearTimeout(fetchTimer); fetchTimer = setTimeout(fetchNet, 180); }
async function fetchNet() {
  if (!S.info || !S.info.loaded) return;
  const seq = ++fetchSeq, pad = 0.25;
  const [lat1, lon0] = fromScreen(-W * pad, -H * pad), [lat0, lon1] = fromScreen(W * (1 + pad), H * (1 + pad));
  try {
    const j = await api(`/api/network?minlat=${lat0}&minlon=${lon0}&maxlat=${lat1}&maxlon=${lon1}&limit=${Math.round(60000 + W * H / 6)}`);
    if (seq !== fetchSeq) return;
    const n = j.cls.length, X = new Float64Array(j.pts.length / 2), Y = new Float64Array(j.pts.length / 2);
    for (let i = 0, k = 0; i < j.pts.length; i += 2, k++) { X[k] = mx(j.pts[i + 1]); Y[k] = my(j.pts[i]); }
    const byCls = {};
    for (let i = 0; i < n; i++) (byCls[j.classes[j.cls[i]]] ||= []).push(i);
    S.net = { n, off: j.off, X, Y, byCls };
    renderNet(); draw();
  } catch (e) { /* Netz bleibt wie es war */ }
}
function renderNet() {
  octx.setTransform(DPR, 0, 0, DPR, 0, 0); octx.clearRect(0, 0, W, H);
  snap = { ...S.view };
  if (!S.net) return;
  const s = scale(), z = S.view.z, base = Math.max(0.5, Math.min(3.2, 0.5 + (z - 11) * 0.28));
  octx.lineJoin = 'round'; octx.lineCap = 'round';
  const { off: O, X, Y, byCls } = S.net, cx = S.view.x, cy = S.view.y;
  for (const key of DRAW_ORDER) {
    const list = byCls[key]; if (!list) continue;
    octx.beginPath();
    for (const i of list) {
      const a = O[i] / 2, b = O[i + 1] / 2;
      octx.moveTo(W / 2 + (X[a] - cx) * s, H / 2 + (Y[a] - cy) * s);
      for (let k = a + 1; k < b; k++) octx.lineTo(W / 2 + (X[k] - cx) * s, H / 2 + (Y[k] - cy) * s);
    }
    octx.strokeStyle = S.byKey[key].color; octx.globalAlpha = ROADS.has(key) ? 0.5 : (key === 'pfad' ? 0.62 : 0.8);
    octx.lineWidth = ROADS.has(key) ? base * 1.2 : base * (key === 'pfad' ? 0.95 : 1);
    octx.stroke();
  }
  octx.globalAlpha = 1;
}

function pin(x, y, label, c1, c2) {
  ctx.save();
  ctx.shadowColor = 'rgba(0,50,90,.55)'; ctx.shadowBlur = 8; ctx.shadowOffsetY = 3;
  const g = ctx.createRadialGradient(x - 4, y - 5, 1, x, y, 15);
  g.addColorStop(0, '#fff'); g.addColorStop(0.25, c1); g.addColorStop(1, c2);
  ctx.beginPath(); ctx.arc(x, y, 14, 0, 7); ctx.fillStyle = g; ctx.fill();
  ctx.shadowColor = 'transparent'; ctx.lineWidth = 2; ctx.strokeStyle = '#fff'; ctx.stroke();
  const gl = ctx.createLinearGradient(0, y - 14, 0, y);
  gl.addColorStop(0, 'rgba(255,255,255,.85)'); gl.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.beginPath(); ctx.ellipse(x, y - 6, 9, 6, 0, 0, 7); ctx.fillStyle = gl; ctx.fill();
  ctx.fillStyle = '#fff'; ctx.font = '700 13px "Segoe UI", sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.shadowColor = 'rgba(0,40,70,.7)'; ctx.shadowBlur = 2; ctx.fillText(label, x, y + 1);
  ctx.restore();
}
function strokeLL(pts, style, w, dash) {
  ctx.beginPath();
  pts.forEach((p, i) => { const [x, y] = toScreen(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = style; ctx.lineWidth = w; ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]);
}
function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, W, H);
  if (snap) {
    const k = Math.pow(2, S.view.z - snap.z), s = scale();
    const cx = W / 2 + (snap.x - S.view.x) * s, cy = H / 2 + (snap.y - S.view.y) * s;
    ctx.drawImage(off, 0, 0, off.width, off.height, cx - W / 2 * k, cy - H / 2 * k, W * k, H * k);
  }
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  if (S.gpx) strokeLL(S.gpx.points, 'rgba(10,60,100,.8)', 2.2, [7, 5]);
  const R = S.route;
  if (R) {
    if (R.ref && R.ref.length) strokeLL(R.ref, 'rgba(20,70,150,.85)', 2.6, [8, 6]);
    ctx.save(); ctx.shadowColor = 'rgba(0,40,80,.45)'; ctx.shadowBlur = 10;
    for (const r of R.runs) strokeLL(r.pts, 'rgba(255,255,255,1)', 13);
    ctx.restore();
    for (const r of R.runs) strokeLL(r.pts, S.byKey[r.cls].color, 6.5);
    for (const r of R.runs) strokeLL(r.pts, 'rgba(255,255,255,.5)', 1.8);
  }
  const a = R ? R.start : S.start, b = R ? R.end : S.end;
  if (a) { const [x, y] = toScreen(a[0], a[1]); pin(x, y, 'A', '#b6f77f', '#2a9a16'); }
  if (b) { const [x, y] = toScreen(b[0], b[1]); pin(x, y, 'B', '#ffb08a', '#d6361e'); }
  scalebar();
}
function scalebar() {
  const lat = latOf(S.view.y), mpp = 156543.03392 * Math.cos(lat * D2R) / Math.pow(2, S.view.z);
  const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000];
  let m = nice[0]; for (const n of nice) if (n / mpp <= 140) m = n;
  const sb = $('scalebar'); sb.innerHTML = `<i style="width:${m / mpp}px"></i>${m >= 1000 ? m / 1000 + ' km' : m + ' m'}`;
}
function onViewChanged(full) { if (full) { renderNet(); } draw(); clearTimeout(onViewChanged.t); onViewChanged.t = setTimeout(() => { renderNet(); draw(); scheduleFetch(); }, 140); }

/* ------------------------------------------------------------------ Karte: Eingaben */
let drag = null;
const ptrs = new Map(); let pinch = null;
cv.addEventListener('pointerdown', (e) => {
  cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]);
  if (ptrs.size === 2) { const [a, b] = [...ptrs.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: S.view.z }; if (drag) drag.moved = true; return; }
  drag = { x: e.clientX, y: e.clientY, vx: S.view.x, vy: S.view.y, moved: false };
});
cv.addEventListener('pointermove', (e) => {
  if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, [e.clientX, e.clientY]);
  if (pinch && ptrs.size >= 2) {
    const [a, b] = [...ptrs.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    S.view.z = Math.max(2, Math.min(19, pinch.z + Math.log2(d / pinch.d))); onViewChanged(false); return;
  }
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) { drag.moved = true; cv.classList.add('dragging'); }
  if (drag.moved) { const s = scale(); S.view.x = drag.vx - dx / s; S.view.y = drag.vy - dy / s; onViewChanged(false); }
});
cv.addEventListener('pointercancel', (e) => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null; drag = null; cv.classList.remove('dragging'); });
cv.addEventListener('pointerup', (e) => {
  cv.classList.remove('dragging');
  ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null;
  if (drag && !drag.moved && S.pick) {
    const r = cv.getBoundingClientRect(); const ll = fromScreen(e.clientX - r.left, e.clientY - r.top);
    setPoint(S.pick, ll); setPick(S.pick === 'start' ? 'end' : null);
  }
  drag = null;
});
cv.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const [lat, lon] = fromScreen(px, py);
  const z = Math.max(2, Math.min(19, S.view.z - e.deltaY * 0.0017));
  S.view.z = z; const s = scale();
  S.view.x = mx(lon) - (px - W / 2) / s; S.view.y = my(lat) - (py - H / 2) / s;
  onViewChanged(false);
}, { passive: false });
cv.addEventListener('dblclick', (e) => { if (S.pick) return; S.view.z = Math.min(19, S.view.z + 1); onViewChanged(false); });
$('zin').addEventListener('click', () => { S.view.z = Math.min(19, S.view.z + 1); onViewChanged(false); });
$('zout').addEventListener('click', () => { S.view.z = Math.max(2, S.view.z - 1); onViewChanged(false); });
$('zfit').addEventListener('click', () => { if (S.info && S.info.loaded) fitBounds(...S.info.bounds); });

function setPick(which) {
  S.pick = which; cv.classList.toggle('picking', !!which);
  $('pick-start').classList.toggle('active', which === 'start'); $('pick-end').classList.toggle('active', which === 'end');
  const h = $('maphint');
  h.hidden = !which; h.textContent = which === 'start' ? 'Klicke auf die Karte, um den Start zu setzen' : 'Klicke auf die Karte, um das Ziel zu setzen';
}
function setPoint(which, ll) {
  S[which] = ll; $(which).value = fmtLL(ll); S.route = null; $('result').hidden = true; draw();
}
$('pick-start').addEventListener('click', () => setPick(S.pick === 'start' ? null : 'start'));
$('pick-end').addEventListener('click', () => setPick(S.pick === 'end' ? null : 'end'));
for (const w of ['start', 'end']) $(w).addEventListener('change', () => { const p = parseLL($(w).value); S[w] = p; S.route = null; draw(); });
$('swap').addEventListener('click', () => { const a = S.start, b = S.end; S.start = b; S.end = a; $('start').value = fmtLL(b); $('end').value = fmtLL(a); S.route = null; draw(); });

/* ------------------------------------------------------------------ Jobs */
function runJob(path, body, title) {
  return new Promise(async (resolve, reject) => {
    $('ov-title').textContent = title; $('ov-bar').style.width = '2%'; $('ov-msg').textContent = ''; $('overlay').hidden = false;
    let id;
    try { id = (await api(path, body)).id; } catch (e) { $('overlay').hidden = true; return reject(e); }
    $('ov-cancel').onclick = () => api('/api/cancel', {});
    const tick = async () => {
      try {
        const j = await api('/api/job');
        if (j.id !== id) return setTimeout(tick, 250);
        $('ov-bar').style.width = Math.max(2, j.progress * 100) + '%'; $('ov-msg').textContent = j.message || '';
        if (j.state === 'running') return setTimeout(tick, 220);
        $('overlay').hidden = true;
        if (j.state === 'done') return resolve(j.result);
        if (j.state === 'cancelled') return reject(new Error('Abgebrochen.'));
        reject(new Error(j.error || 'Unbekannter Fehler'));
      } catch (e) { $('overlay').hidden = true; reject(e); }
    };
    tick();
  });
}

/* ------------------------------------------------------------------ Karte laden */
function excluded() {
  const out = [];
  if ($('ex-motorway').checked) out.push('motorway', 'motorway_link');
  if ($('ex-trunk').checked) out.push('trunk', 'trunk_link');
  return out;
}
function applyState(st, refit) {
  S.info = st; const ok = st.loaded;
  $('netpill').classList.toggle('ok', ok);
  $('nettext').textContent = ok ? `${st.source} · ${de(st.n_nodes)} Knoten` : 'Keine Karte geladen';
  $('go').disabled = !ok; $('rebuildbtn').disabled = !ok; $('sweepgo').disabled = !ok;
  $('empty').hidden = ok;
  if (ok) {
    if (refit) fitBounds(...st.bounds);
    if (!S.start && !S.end) setPick('start');
  }
  if (st.out_dir && !$('outdir').value) $('outdir').value = store.get('outdir', st.out_dir);
}
const ANDROID = typeof window.AndroidBridge !== 'undefined';
if (ANDROID) document.documentElement.classList.add('android');
async function pickFile(kind) {
  if (ANDROID) return new Promise((res) => { window.__pickDone = (p) => res(p || null); window.AndroidBridge.pick(kind); });
  const j = await api('/api/pick', { kind }); return j.path;
}
async function choosePbf() {
  const p = await pickFile('pbf'); if (!p) return;
  $('pbfpath').value = p; store.set('pbf', p); await showHeader();
}
async function showHeader() {
  const p = $('pbfpath').value.trim(); const box = $('hdrinfo'); box.innerHTML = '';
  if (!p) return;
  try {
    const h = await api('/api/header', { path: p });
    if (h.size) box.insertAdjacentHTML('beforeend', `<span class="chip">${de(h.size / 1048576, 0)} MB</span>`);
    if (h.bbox && h.bbox.top != null) box.insertAdjacentHTML('beforeend', `<span class="chip">${h.bbox.bottom.toFixed(2)}–${h.bbox.top.toFixed(2)}° N, ${h.bbox.left.toFixed(2)}–${h.bbox.right.toFixed(2)}° O</span>`);
  } catch { /* Kopfzeile ist nur Info */ }
}
async function loadMap() {
  showErr($('loaderr'), '');
  const path = $('pbfpath').value.trim();
  if (!path) { showErr($('loaderr'), 'Wähle zuerst eine Kartendatei (.pbf).'); return; }
  store.set('pbf', path);
  const v = ['bb-s', 'bb-w', 'bb-n', 'bb-e'].map((i) => $(i).value);
  let bbox = null;
  if (v.some((x) => x !== '')) {
    if (v.some((x) => x === '' || !isFinite(+x))) { showErr($('loaderr'), 'Für einen Ausschnitt alle vier Werte angeben.'); return; }
    bbox = v.map(Number);
  }
  try {
    const st = await runJob('/api/load', { path, bbox, excluded: excluded(), par_dist: +$('par-dist').value, par_share: +$('par-share').value / 100 }, 'Karte wird geladen');
    S.net = null; S.route = null; $('result').hidden = true; S.sweep = null;
    applyState(st, true); toast('Karte geladen');
  } catch (e) { showErr($('loaderr'), e.message); }
}
$('pickpbf').addEventListener('click', choosePbf);
$('emptyload').addEventListener('click', async () => { await choosePbf(); if ($('pbfpath').value.trim()) loadMap(); });
$('pbfpath').addEventListener('change', showHeader);
$('loadbtn').addEventListener('click', loadMap);
$('par-dist').addEventListener('input', () => { $('par-dist-v').textContent = $('par-dist').value; });
$('par-share').addEventListener('input', () => { $('par-share-v').textContent = $('par-share').value; });
$('rebuildbtn').addEventListener('click', async () => {
  try {
    const st = await runJob('/api/rebuild', { excluded: excluded(), par_dist: +$('par-dist').value, par_share: +$('par-share').value / 100 }, 'Wegnetz wird neu aufgebaut');
    S.net = null; S.route = null; $('result').hidden = true; applyState(st, false); fetchNet(); toast('Wegnetz aktualisiert');
  } catch (e) { showErr($('loaderr'), e.message); }
});

/* ------------------------------------------------------------------ GPX */
async function loadGpx() {
  const p = await pickFile('gpx'); if (!p) return;
  try {
    const j = await api('/api/gpx', { path: p });
    S.gpx = { name: p.split(/[\\/]/).pop(), points: j.points };
    $('gpxname').textContent = `${S.gpx.name} (${de(j.points.length)} Punkte)`; $('gpxopts').hidden = false;
    const lats = j.points.map((q) => q[0]), lons = j.points.map((q) => q[1]);
    if (S.info && S.info.loaded) fitBounds(Math.min(...lats), Math.min(...lons), Math.max(...lats), Math.max(...lons)); else draw();
  } catch (e) { toast(e.message, 5000); }
}
$('pickgpx').addEventListener('click', loadGpx);
$('cor-w').addEventListener('input', () => { $('cor-w-v').textContent = $('cor-w').value; });
$('gpx-ends').addEventListener('click', () => {
  if (!S.gpx) return; const p = S.gpx.points;
  setPoint('start', p[0]); setPoint('end', p[p.length - 1]); setPick(null);
});
$('gpx-clear').addEventListener('click', () => { S.gpx = null; $('gpxopts').hidden = true; $('gpxname').textContent = ''; draw(); });

/* ------------------------------------------------------------------ Route */
function routeBody() {
  const body = { start: S.start, end: S.end, mode: S.method };
  if (S.method === 'paths') body.k = kValue();
  else { body.factors = {}; for (const k in S.factors) body.factors[k] = S.factors[k].locked ? null : S.factors[k].v; }
  if (S.gpx && $('cor-on').checked) body.corridor = { points: S.gpx.points, width: +$('cor-w').value };
  return body;
}
function needPoints() {
  S.start = parseLL($('start').value); S.end = parseLL($('end').value);
  if (!S.start || !S.end) { showErr($('routeerr'), 'Start und Ziel fehlen. Klicke auf die Karte oder gib Koordinaten wie 47.656, 9.177 ein.'); return false; }
  showErr($('routeerr'), ''); return true;
}
$('go').addEventListener('click', async () => {
  if (!needPoints()) return;
  setPick(null);
  try {
    const res = await runJob('/api/route', routeBody(), 'Route wird berechnet');
    S.route = res; showResult(res);
    const lats = [], lons = [];
    for (const r of res.runs) for (const p of r.pts) { lats.push(p[0]); lons.push(p[1]); }
    fitBounds(Math.min(...lats), Math.min(...lons), Math.max(...lats), Math.max(...lons), 0.16, W > 900 ? 360 : 0);
  } catch (e) { showErr($('routeerr'), e.message); }
});

function arc(cx, cy, r, a0, a1) {
  const p = (a) => [cx + r * Math.cos(a * D2R), cy + r * Math.sin(a * D2R)];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
function gauge(p) {
  const A0 = 135, SW = 270, a1 = A0 + SW * Math.max(0.0001, Math.min(1, p));
  let ticks = '';
  for (let i = 0; i <= 10; i++) {
    const a = (A0 + SW * i / 10) * D2R, big = i % 5 === 0, r0 = 92, r1 = big ? 83 : 87;
    ticks += `<line x1="${110 + r0 * Math.cos(a)}" y1="${110 + r0 * Math.sin(a)}" x2="${110 + r1 * Math.cos(a)}" y2="${110 + r1 * Math.sin(a)}" stroke="#4d7f9b" stroke-width="${big ? 2 : 1.2}" stroke-linecap="round"/>`;
  }
  const ke = a1 * D2R, kx = 110 + 72 * Math.cos(ke), ky = 110 + 72 * Math.sin(ke);
  $('gauge').innerHTML = `
  <defs>
    <linearGradient id="bz" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset=".45" stop-color="#c8dbe6"/><stop offset=".55" stop-color="#8fa9ba"/><stop offset="1" stop-color="#e8f3f9"/></linearGradient>
    <radialGradient id="face" cx=".5" cy=".4" r=".7"><stop offset="0" stop-color="#ffffff"/><stop offset=".7" stop-color="#d9f0fb"/><stop offset="1" stop-color="#b5dff4"/></radialGradient>
    <linearGradient id="val" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#ffb347"/><stop offset=".55" stop-color="#ff6a4d"/><stop offset="1" stop-color="#e8362a"/></linearGradient>
    <linearGradient id="dome" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".85"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
    <radialGradient id="knob" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#fff"/><stop offset=".4" stop-color="#ffd7ce"/><stop offset="1" stop-color="#ff5a4d"/></radialGradient>
  </defs>
  <circle cx="110" cy="110" r="107" fill="url(#bz)"/><circle cx="110" cy="110" r="100" fill="url(#bz)" transform="rotate(180 110 110)" opacity=".9"/>
  <circle cx="110" cy="110" r="96" fill="url(#face)" stroke="#7fa6bd" stroke-width="1.5"/>
  <path d="${arc(110, 110, 72, A0, A0 + SW)}" fill="none" stroke="rgba(8,100,160,.28)" stroke-width="15" stroke-linecap="round"/>
  <path d="${arc(110, 110, 72, A0, A0 + SW)}" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.5" stroke-linecap="round" transform="translate(0 6.5)" opacity=".6"/>
  <path d="${arc(110, 110, 72, A0, a1)}" fill="none" stroke="url(#val)" stroke-width="15" stroke-linecap="round"/>
  <path d="${arc(110, 110, 76.5, A0, a1)}" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="3" stroke-linecap="round"/>
  ${ticks}
  <circle cx="${kx}" cy="${ky}" r="9" fill="url(#knob)" stroke="#b8281c" stroke-width="1"/>
  <text x="110" y="116" text-anchor="middle" font-size="46" font-weight="600" fill="#0a4f7a" stroke="#fff" stroke-width="4" paint-order="stroke">${de(p * 100, 0)}<tspan font-size="22"> %</tspan></text>
  <text x="110" y="142" text-anchor="middle" font-size="14" fill="#3f7a9c">Pfadanteil</text>
  <path d="M26 96 C 30 44, 190 44, 194 96 C 150 70, 70 70, 26 96Z" fill="url(#dome)"/>`;
}
function showResult(res) {
  const s = res.summary, k = s.kuerzeste;
  $('result').hidden = false; gauge(s.pfad_share);
  $('r-len').textContent = `${de(s.length_m / 1000, 2)} km`;
  const roadM = Object.entries(s.by_class).filter(([c]) => S.byKey[c].strasse).reduce((a, [, v]) => a + v, 0);
  $('r-road').textContent = `${de(s.strasse_share * 100, 1)} %`; $('r-road').title = `${de(roadM)} m`;
  $('r-detour').textContent = (s.umweg_vs_kuerzeste >= 0 ? '+' : '') + de(s.umweg_vs_kuerzeste * 100, 0) + ' %';
  const warn = res.access.filter((r) => r.hinweise.length);
  $('r-hints').textContent = String(warn.length);
  const entries = Object.entries(s.by_class);
  $('r-stack').innerHTML = entries.map(([c, v]) => `<i style="width:${v / s.length_m * 100}%;background-color:${S.byKey[c].color}" title="${S.byKey[c].label}"></i>`).join('');
  $('r-legend').innerHTML = entries.map(([c, v]) => `<li><span class="sw" style="background:${S.byKey[c].color}"></span>${S.byKey[c].label}<span class="m">${de(v)} m</span></li>`).join('');
  $('r-cmp').innerHTML = [
    ['Pfad, Route', s.pfad_share, ''], ['Pfad, kürzeste', k.pfad_share, 'ref'],
    ['Straße, Route', s.strasse_share, ''], ['Straße, kürzeste', k.strasse_share, 'ref'],
  ].map(([n, v, c]) => `<div class="cmp"><span>${n}</span><span class="bar"><i class="${c}" style="width:${v * 100}%"></i></span><span class="n">${de(v * 100, 0)} %</span></div>`).join('');
  $('openout').hidden = true;
  renderLog(res.access, false);
  const b = $('logbadge'); b.textContent = warn.length; b.hidden = !warn.length;
}

/* ------------------------------------------------------------------ Schublade, Protokoll, Vergleich */
function openDrawer(tab) {
  const d = $('drawer'); d.classList.add('open'); $('drawer-toggle').textContent = 'Ausblenden';
  for (const t of ['log', 'sweep']) { $('tab-' + t).setAttribute('aria-selected', t === tab); $('panel-' + t).hidden = t !== tab; }
}
$('tab-log').addEventListener('click', () => openDrawer('log'));
$('tab-sweep').addEventListener('click', () => openDrawer('sweep'));
$('drawer-toggle').addEventListener('click', () => {
  const d = $('drawer'); const open = !d.classList.contains('open');
  if (open) openDrawer($('tab-sweep').getAttribute('aria-selected') === 'true' ? 'sweep' : 'log');
  else { d.classList.remove('open'); $('drawer-toggle').textContent = 'Einblenden'; }
  setTimeout(resize, 300);
});
function renderLog(rows, all) {
  const shown = all ? rows : rows.filter((r) => r.hinweise.length);
  const rest = rows.length - shown.length;
  let html = shown.map((r) => `<tr class="${r.hinweise.length ? 'warn' : ''}"><td><a class="lnk" data-url="${r.link}">${r.name || 'Weg ' + r.way}</a></td>
    <td><span class="swd" style="background:${S.byKey[r.cls].color}"></span>${r.klasse}</td><td class="num">${de(r.laenge_m)}</td><td>${r.hinweise.join('; ') || '–'}</td></tr>`).join('');
  if (!shown.length) html = `<tr><td colspan="4">Keine Zugangshinweise auf dieser Route.</td></tr>`;
  if (rest > 0) html += `<tr><td colspan="4">${rest} Abschnitte ohne Hinweis. <a class="lnk" id="logall">Alle anzeigen</a></td></tr>`;
  if (all) html += `<tr><td colspan="4"><a class="lnk" id="lognone">Nur Abschnitte mit Hinweis zeigen</a></td></tr>`;
  $('logbody').innerHTML = html;
  const a = $('logall'), n = $('lognone');
  if (a) a.onclick = () => renderLog(rows, true); if (n) n.onclick = () => renderLog(rows, false);
}
$('logbody').addEventListener('click', (e) => { const u = e.target.dataset && e.target.dataset.url; if (u) api('/api/browse', { url: u }); });

$('sweepgo').addEventListener('click', async () => {
  if (!needPoints()) return;
  const ks = $('sweepks').value.split(/[\s,;]+/).map(Number).filter((v) => v > 0);
  if (!ks.length) { toast('Gib mindestens einen Wert für K an.'); return; }
  const body = { start: S.start, end: S.end, ks };
  if (S.gpx && $('cor-on').checked) body.corridor = { points: S.gpx.points, width: +$('cor-w').value };
  try { const r = await runJob('/api/sweep', body, 'Vergleich läuft'); S.sweep = r.rows; renderSweep(); openDrawer('sweep'); }
  catch (e) { toast(e.message, 5000); }
});
function renderSweep() {
  const rows = S.sweep; if (!rows) return;
  const maxX = Math.max(10, ...rows.map((r) => r.umweg * 100)) * 1.08, X = (v) => 46 + v / maxX * 450, Y = (v) => 176 - v * 150;
  let g = '';
  for (let i = 0; i <= 4; i++) { const v = i / 4; g += `<line x1="46" x2="500" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(8,110,170,.2)"/><text x="40" y="${Y(v) + 4}" text-anchor="end" font-size="11" fill="#3f7a9c">${v * 100} %</text>`; }
  const pts = rows.map((r) => [X(Math.max(0, r.umweg * 100)), Y(r.pfad_share)]);
  g += `<path d="M${pts.map((p) => p.join(' ')).join('L')}" fill="none" stroke="#ff5a4d" stroke-width="3" stroke-linejoin="round"/>`;
  rows.forEach((r, i) => { g += `<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="5.5" fill="${r.k ? '#ff5a4d' : '#6f8798'}" stroke="#fff" stroke-width="2"/><text x="${pts[i][0]}" y="${pts[i][1] - 10}" text-anchor="middle" font-size="10.5" fill="#0a4f7a">${r.k ? r.k : 'kürzeste'}</text>`; });
  g += `<text x="273" y="196" text-anchor="middle" font-size="11" fill="#3f7a9c">Umweg gegenüber der kürzesten Route (%)</text>`;
  $('sweepsvg').innerHTML = g;
  $('sweepbody').innerHTML = rows.map((r) => `<tr data-k="${r.k || ''}" style="cursor:${r.k ? 'pointer' : 'default'}"><td>${r.k ? 'K ' + r.k : 'kürzeste'}</td><td class="num">${de(r.length_m / 1000, 1)} km</td><td class="num">${de(r.pfad_share * 100, 0)} % Pfad</td></tr>`).join('');
}
$('sweepbody').addEventListener('click', (e) => {
  const tr = e.target.closest('tr'); if (!tr || !tr.dataset.k) return;
  $('k').value = valToPos(+tr.dataset.k); paintK(); setMethod('paths'); toast(`K = ${tr.dataset.k} übernommen. Mit „Route berechnen“ anzeigen.`);
});

/* ------------------------------------------------------------------ Export */
$('pickout').addEventListener('click', async () => { const p = await pickFile('folder'); if (p) { $('outdir').value = p; store.set('outdir', p); } });
$('save').addEventListener('click', async () => {
  try {
    draw();
    const png = cv.toDataURL('image/png');
    const tag = `${S.method === 'paths' ? 'pfade_k' + kValue() : 'strassenarm'}_${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}`;
    const r = await api('/api/export', { out_dir: $('outdir').value.trim(), tag, png });
    store.set('outdir', r.dir); $('openout').hidden = false; $('openout').dataset.dir = r.dir;
    toast(`${r.files.length} Dateien gespeichert`);
  } catch (e) { toast(e.message, 6000); }
});
$('openout').addEventListener('click', () => (ANDROID ? window.AndroidBridge.share($('openout').dataset.dir) : api('/api/open', { path: $('openout').dataset.dir })));

/* ------------------------------------------------------------------ Start */
(async function init() {
  try {
    S.defs = await api('/api/defaults');
    for (const c of S.defs.classes) S.byKey[c.key] = c;
    buildFactors(); paintK();
    $('pbfpath').value = store.get('pbf', '');
    const st = await api('/api/state'); applyState(st, true);
    if ($('pbfpath').value) showHeader();
  } catch (e) { toast(e.message, 8000); }
  resize();
  window.__nrmm = { S, draw, fitBounds, setPoint, gauge };
})();
})();
